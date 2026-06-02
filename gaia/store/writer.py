"""
gaia.store.writer -- CRUD API for the Gaia SQLite substrate.

The writer is the only authorized path to mutate `~/.gaia/gaia.db`. Every
mutation consults `agent_permissions(table_name, agent_name, allow_write)`
before touching data. If the (table, agent) pair is missing or has
``allow_write=0``, the operation returns ``{"status": "rejected",
"reason": "not_authorized"}`` without modifying the DB.

Vocabulary:
  * ``workspaces`` table -- organizational containers (e.g. "me", "bildwiz").
  * ``projects`` table  -- git-bearing source projects within a workspace.
  * Column ``workspace`` -- FK to workspaces.name.
  * Column ``project``   -- FK to projects(workspace, name).

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram. See NOTICE.md.

Public API::

    upsert_project(workspace, name, fields, agent, topic_key=None) -> dict
    upsert_app(workspace, project, name, fields, agent, topic_key=None) -> dict
    delete_missing_in(table, workspace, surviving_keys) -> int
    bulk_upsert(table, workspace, rows, agent) -> dict
    wipe_workspace(workspace) -> None
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Schema file lives alongside this module
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Tables we recognize (whitelist for delete_missing_in / bulk_upsert)
_KNOWN_TABLES = {
    "workspaces",
    "projects",
    "apps",
    "libraries",
    "services",
    "features",
    "tf_modules",
    "tf_live",
    "releases",
    "workloads",
    "clusters_defined",
    "clusters",
    "integrations",
    "gaia_installations",
    "machines",
}


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    """Resolve the DB path via gaia.paths (B0). Imported lazily to avoid
    side effects at import time."""
    from gaia.paths import db_path
    return db_path()


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a connection, ensuring the schema is materialized.

    Args:
        db_path: Optional explicit DB path (used by tests). When None,
            resolves via ``gaia.paths.db_path()``.

    Returns:
        Open sqlite3.Connection with foreign_keys=ON.
    """
    if db_path is None:
        db_path = _db_path()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not db_path.exists()
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    # Register gaia_sha256: scalar function used by the ai_approval_events_hash
    # trigger to compute this_hash = SHA-256(prev_hash || fingerprint).
    # SQLite does not include SHA-256 built-in; we inject it as a Python function
    # at connection time. All connections opened via _connect() get this function,
    # which means the trigger fires correctly on any INSERT into approval_events.
    # The function accepts a single TEXT argument and returns the hex digest.
    def _gaia_sha256(value: str | None) -> str:
        return hashlib.sha256((value or "").encode("utf-8")).hexdigest()

    con.create_function("gaia_sha256", 1, _gaia_sha256, deterministic=True)

    if fresh:
        con.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        con.commit()
    return con


def _now_iso() -> str:
    """Return current UTC time as ISO8601 (Z suffix)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Permission enforcement
# ---------------------------------------------------------------------------

def _is_authorized(con: sqlite3.Connection, table_name: str, agent: str) -> bool:
    """Return True iff (table_name, agent) has allow_write=1."""
    row = con.execute(
        "SELECT allow_write FROM agent_permissions WHERE table_name = ? AND agent_name = ?",
        (table_name, agent),
    ).fetchone()
    if row is None:
        return False
    return bool(row[0])


def _rejected(reason: str = "not_authorized") -> dict:
    return {"status": "rejected", "reason": reason}


def _applied(extra: dict | None = None) -> dict:
    out = {"status": "applied"}
    if extra:
        out.update(extra)
    return out


# ---------------------------------------------------------------------------
# Identity resolution (workspaces.identity)
# ---------------------------------------------------------------------------

def _resolve_identity(workspace: str, workspace_path: Path | None = None) -> str:
    """Resolve workspace identity.

    Rule (post-fix):
      * If ``workspace_path`` is provided AND ``workspace_path / .git`` exists
        (the workspace root is itself a git project), resolve identity from
        the git remote of that directory via ``gaia.project.current``.
      * Otherwise (organizational workspace -- no .git at the root), the
        identity IS the workspace name. We do NOT leak the remote of a child
        project up to the workspace row.

    This prevents the historical contamination where a workspace like ``me``
    received the identity of its first scanned child project.

    Falls back to the workspace string itself when path resolution fails.

    Args:
        workspace:      Workspace name used as the fallback / organizational identity.
        workspace_path: Directory whose git remote may supply the identity.
                        Defaults to None (treated as organizational workspace).
    """
    if workspace_path is None:
        return workspace.lower()

    # Only resolve a remote-derived identity when the workspace root is itself
    # a git project. Organizational workspaces (no .git at root) keep their
    # name as identity.
    try:
        if not (workspace_path / ".git").is_dir():
            return workspace.lower()
        from gaia.project import current as _project_current
        ident = _project_current(cwd=workspace_path)
        if ident and ident != "global":
            return ident
    except Exception:
        pass
    return workspace.lower()


def _ensure_workspace_row(
    con: sqlite3.Connection,
    workspace: str,
    workspace_path: Path | None = None,
) -> None:
    """Insert (or update) the workspaces row for a workspace.

    Identity is resolved from the git remote of ``workspace_path`` at insertion
    time IFF the workspace root itself is a git project (see
    :func:`_resolve_identity`). On a fresh row the identity is captured; for
    existing rows the identity is left intact (idempotent).

    Args:
        con:            Open SQLite connection.
        workspace:      Workspace name (workspaces.name PK).
        workspace_path: Directory whose git remote may supply the identity.
                        When None, identity defaults to the workspace name.
    """
    existing = con.execute(
        "SELECT name FROM workspaces WHERE name = ?",
        (workspace,),
    ).fetchone()
    if existing is not None:
        return
    identity = _resolve_identity(workspace, workspace_path)
    con.execute(
        "INSERT INTO workspaces (name, identity, created_at) VALUES (?, ?, ?)",
        (workspace, identity, _now_iso()),
    )


# ---------------------------------------------------------------------------
# Public API: set_workspace_last_scan_at
# ---------------------------------------------------------------------------

def set_workspace_last_scan_at(
    workspace: str,
    ts: str | None = None,
    *,
    db_path: Path | None = None,
) -> None:
    """Record the ISO8601 timestamp of the most recent successful gaia scan.

    Called by bin/cli/scan.py after a scan run completes without errors.
    The workspaces row is created (via _ensure_workspace_row) if it does
    not yet exist; the update is a no-op when the workspace is unknown.

    Args:
        workspace: Workspace name (workspaces.name PK).
        ts:        ISO8601 UTC timestamp string. Defaults to _now_iso().
        db_path:   Optional explicit DB path (used by tests).
    """
    if ts is None:
        ts = _now_iso()

    con = _connect(db_path)
    try:
        _ensure_workspace_row(con, workspace)
        con.execute(
            "UPDATE workspaces SET last_scan_at = ? WHERE name = ?",
            (ts, workspace),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_project
# ---------------------------------------------------------------------------

_PROJECT_FIELDS = ("role", "remote_url", "platform", "primary_language", "group_name", "path", "status", "missing_since")


def upsert_project(
    workspace: str,
    name: str,
    fields: Mapping[str, Any],
    agent: str,
    topic_key: str | None = None,
    *,
    db_path: Path | None = None,
    workspace_path: Path | None = None,
) -> dict:
    """Upsert a projects row, enforcing per-agent write permission.

    Args:
        workspace: Workspace name (matches workspaces.name / projects.workspace).
        name: Project name (basename).
        fields: Dict of column->value pairs. Recognized keys:
            ``role``, ``remote_url``, ``platform``, ``primary_language``,
            ``group_name``, ``path``, ``status``, ``missing_since``.
            ``status`` defaults to 'active' when not provided. ``missing_since``
            defaults to NULL. On re-upsert of a live project (status='active')
            the scanner should pass status='active' and missing_since=None to
            reactivate a previously-missing project; default values handle this
            when the caller omits both fields.
        agent: Agent name. Must have allow_write=1 for table 'projects' in
            agent_permissions.
        topic_key: Optional dimension key.
        db_path: Optional explicit DB path (used by tests).
        workspace_path: Directory whose git remote supplies the workspaces.identity
            value. Pass ``project_path`` from the scanner for correct
            multi-workspace ingestion.

    Returns:
        {"status": "applied"} on success.
        {"status": "rejected", "reason": "not_authorized"} if the agent lacks
        write permission for the 'projects' table.
    """
    con = _connect(db_path)
    try:
        if not _is_authorized(con, "projects", agent):
            return _rejected()
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace, workspace_path)
            data = {k: fields.get(k) for k in _PROJECT_FIELDS}
            # Default status to 'active' when not explicitly provided.
            # This ensures newly-inserted rows and re-upserted live projects
            # always carry an explicit status value.
            status_val = data["status"] if data["status"] is not None else "active"
            con.execute(
                """
                INSERT INTO projects (workspace, name, role, remote_url, platform,
                                      primary_language, scanner_ts, topic_key,
                                      group_name, path, status, missing_since)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
                    role = excluded.role,
                    remote_url = excluded.remote_url,
                    platform = excluded.platform,
                    primary_language = excluded.primary_language,
                    scanner_ts = excluded.scanner_ts,
                    topic_key = excluded.topic_key,
                    group_name = excluded.group_name,
                    path = excluded.path,
                    status = excluded.status,
                    missing_since = excluded.missing_since
                """,
                (
                    workspace, name,
                    data["role"], data["remote_url"], data["platform"],
                    data["primary_language"], _now_iso(), topic_key,
                    data["group_name"], data["path"],
                    status_val, data["missing_since"],
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_app
# ---------------------------------------------------------------------------

_APP_FIELDS = ("kind", "description", "status")


def upsert_app(
    workspace: str,
    project: str,
    name: str,
    fields: Mapping[str, Any],
    agent: str,
    topic_key: str | None = None,
    *,
    db_path: Path | None = None,
) -> dict:
    """Upsert an apps row, enforcing per-agent write permission.

    Args:
        workspace: Workspace name (matches apps.workspace).
        project: Parent project name (must reference a row in the
                 ``projects`` table).
        name: App name.
        fields: Dict with optional keys ``kind``, ``description``, ``status``.
        agent: Agent name. Requires allow_write=1 for table 'apps'.
        topic_key: Optional dimension key.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success.
        {"status": "rejected", "reason": "not_authorized"} otherwise.
    """
    con = _connect(db_path)
    try:
        if not _is_authorized(con, "apps", agent):
            return _rejected()
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            # Ensure parent project row exists -- create a minimal stub if missing
            existing_project = con.execute(
                "SELECT name FROM projects WHERE workspace = ? AND name = ?",
                (workspace, project),
            ).fetchone()
            if existing_project is None:
                con.execute(
                    "INSERT INTO projects (workspace, name, scanner_ts) VALUES (?, ?, ?)",
                    (workspace, project, _now_iso()),
                )
            data = {k: fields.get(k) for k in _APP_FIELDS}
            con.execute(
                """
                INSERT INTO apps (workspace, project, name, kind, description, status,
                                  topic_key, scanner_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, project, name) DO UPDATE SET
                    kind = excluded.kind,
                    description = excluded.description,
                    status = excluded.status,
                    topic_key = excluded.topic_key,
                    scanner_ts = excluded.scanner_ts
                """,
                (
                    workspace, project, name,
                    data["kind"], data["description"], data["status"],
                    topic_key, _now_iso(),
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: delete_missing_in
# ---------------------------------------------------------------------------

def delete_missing_in(
    table: str,
    workspace: str,
    surviving_keys: Iterable[Sequence[Any]],
    *,
    db_path: Path | None = None,
) -> int:
    """Delete rows from `table` (filtered by workspace) whose primary
    key is NOT in surviving_keys.

    Args:
        table: Target table name (must be in _KNOWN_TABLES).
        workspace: Workspace name (workspace FK value).
        surviving_keys: Iterable of tuples representing the PK fragments to
            keep. For ``projects`` use ``[(name,), ...]``. For ``apps`` use
            ``[(project, name), ...]``.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Number of rows deleted.

    Raises:
        ValueError: if `table` is not in the whitelist.
    """
    if table not in _KNOWN_TABLES:
        raise ValueError(f"unknown table: {table!r}")

    surviving = list(surviving_keys)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            pk_columns = {
                "workspaces": ("name",),
                "projects": ("name",),
                "apps": ("project", "name"),
                "libraries": ("project", "name"),
                "services": ("project", "name"),
                "features": ("project", "name"),
                "tf_modules": ("project", "name"),
                "tf_live": ("project", "name"),
                "releases": ("project", "name"),
                "workloads": ("project", "name"),
                "clusters_defined": ("project", "name"),
                "clusters": ("name",),
                "integrations": ("name",),
                "gaia_installations": ("machine",),
                "machines": ("name",),
            }[table]

            cols_sql = ", ".join(pk_columns)
            existing = con.execute(
                f"SELECT {cols_sql} FROM {table} WHERE workspace = ?",
                (workspace,),
            ).fetchall()
            existing_set = {tuple(row) for row in existing}
            surviving_set = {tuple(s) for s in surviving}
            to_delete = existing_set - surviving_set

            count = 0
            for key in to_delete:
                placeholders = " AND ".join(f"{c} = ?" for c in pk_columns)
                con.execute(
                    f"DELETE FROM {table} WHERE workspace = ? AND {placeholders}",
                    (workspace, *key),
                )
                count += 1
            con.commit()
            return count
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: mark_missing_in (soft-delete; mirror of delete_missing_in)
# ---------------------------------------------------------------------------

def mark_missing_in(
    table: str,
    workspace: str,
    surviving_keys: Iterable[Sequence[Any]],
    *,
    db_path: Path | None = None,
) -> int:
    """Soft-delete rows in `table` (filtered by workspace) whose primary key is
    NOT in surviving_keys: set ``status='missing'`` and ``missing_since=<now>``
    instead of DELETEing them.

    This is the mirror of :func:`delete_missing_in` but UPDATEs instead of
    DELETEs. A scan that only partially discovers projects (a partial walk, a
    permissions hiccup, a transient error) must never destroy real rows; it
    marks them missing so the data survives and remains consultable.

    Only rows that are not ALREADY missing are touched -- a row already
    ``status='missing'`` keeps its original ``missing_since`` timestamp (the
    moment it first disappeared), so repeated re-scans do not keep bumping it.

    Args:
        table: Target table name. Must be ``"projects"`` -- it is the only
            table carrying the ``status`` / ``missing_since`` soft-delete
            columns. Any other table raises ValueError because marking it
            missing has no column to write.
        workspace: Workspace name (workspace FK value). Scoping is strict;
            rows in other workspaces are never touched.
        surviving_keys: Iterable of tuples representing the PK fragments to
            keep active. For ``projects`` use ``[(name,), ...]``.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Number of rows newly marked missing.

    Raises:
        ValueError: if `table` is not whitelisted or does not carry the
            soft-delete columns.
    """
    if table not in _KNOWN_TABLES:
        raise ValueError(f"unknown table: {table!r}")
    # Only `projects` carries status/missing_since. Marking any other table
    # missing is a programming error -- fail loudly instead of writing to a
    # column that does not exist.
    if table != "projects":
        raise ValueError(
            f"mark_missing_in only supports the 'projects' table "
            f"(soft-delete columns status/missing_since); got {table!r}"
        )

    surviving = list(surviving_keys)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            existing = con.execute(
                "SELECT name, status FROM projects WHERE workspace = ?",
                (workspace,),
            ).fetchall()
            surviving_set = {tuple(s) for s in surviving}

            now = _now_iso()
            count = 0
            for row in existing:
                key = (row["name"],)
                if key in surviving_set:
                    continue
                # Already missing -> leave missing_since intact (first-seen-gone).
                if row["status"] == "missing":
                    continue
                con.execute(
                    "UPDATE projects SET status = 'missing', missing_since = ? "
                    "WHERE workspace = ? AND name = ?",
                    (now, workspace, row["name"]),
                )
                count += 1
            con.commit()
            return count
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: bulk_upsert
# ---------------------------------------------------------------------------

def bulk_upsert(
    table: str,
    workspace: str,
    rows: Iterable[Mapping[str, Any]],
    agent: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Upsert multiple rows in a single transaction.

    Returns:
        {"applied": int, "rejected": int}
    """
    rows_list = list(rows)
    applied = 0
    rejected = 0
    if table == "projects":
        for r in rows_list:
            res = upsert_project(
                workspace,
                r["name"],
                r,
                agent,
                topic_key=r.get("topic_key"),
                db_path=db_path,
            )
            if res.get("status") == "applied":
                applied += 1
            else:
                rejected += 1
        return {"applied": applied, "rejected": rejected}

    if table == "apps":
        for r in rows_list:
            res = upsert_app(
                workspace,
                r["project"],
                r["name"],
                r,
                agent,
                topic_key=r.get("topic_key"),
                db_path=db_path,
            )
            if res.get("status") == "applied":
                applied += 1
            else:
                rejected += 1
        return {"applied": applied, "rejected": rejected}

    # Generic path: enforce permission + ON CONFLICT DO UPDATE that ONLY
    # updates the columns the caller provided.
    pk_columns = {
        "workspaces": ("name",),
        "projects": ("name",),
        "apps": ("project", "name"),
        "libraries": ("project", "name"),
        "services": ("project", "name"),
        "features": ("project", "name"),
        "tf_modules": ("project", "name"),
        "tf_live": ("project", "name"),
        "releases": ("project", "name"),
        "workloads": ("project", "name"),
        "clusters_defined": ("project", "name"),
        "clusters": ("name",),
        "integrations": ("name",),
        "gaia_installations": ("machine",),
        "machines": ("name",),
    }
    if table not in pk_columns:
        raise ValueError(f"unknown table for bulk_upsert: {table!r}")
    pk = ("workspace", *pk_columns[table])

    con = _connect(db_path)
    try:
        if not _is_authorized(con, table, agent):
            return {"applied": 0, "rejected": len(rows_list)}
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            for r in rows_list:
                row_data = dict(r)
                cols = ["workspace"] + list(row_data.keys())
                vals = [workspace] + list(row_data.values())
                placeholders = ", ".join(["?"] * len(cols))
                update_cols = [c for c in row_data.keys() if c not in pk]
                pk_sql = ", ".join(pk)
                if update_cols:
                    set_clause = ", ".join(
                        f"{c} = excluded.{c}" for c in update_cols
                    )
                    sql = (
                        f"INSERT INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT({pk_sql}) DO UPDATE SET {set_clause}"
                    )
                else:
                    sql = (
                        f"INSERT INTO {table} ({', '.join(cols)}) "
                        f"VALUES ({placeholders}) "
                        f"ON CONFLICT({pk_sql}) DO NOTHING"
                    )
                con.execute(sql, vals)
                applied += 1
            con.commit()
        except Exception:
            con.rollback()
            raise
        return {"applied": applied, "rejected": rejected}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: save_integration
# ---------------------------------------------------------------------------

_INTEGRATION_FIELDS = ("kind", "version", "install_path", "topic_key")


def save_integration(
    workspace: str,
    name: str,
    *,
    kind: str | None = None,
    version: str | None = None,
    install_path: str | None = None,
    topic_key: str | None = None,
    agent: str = "system",
    db_path: Path | None = None,
) -> dict:
    """Upsert an integrations row, bypassing per-agent permission enforcement.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            con.execute(
                """
                INSERT INTO integrations (workspace, name, kind, version,
                                          install_path, topic_key, scanner_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
                    kind         = COALESCE(excluded.kind, kind),
                    version      = COALESCE(excluded.version, version),
                    install_path = COALESCE(excluded.install_path, install_path),
                    topic_key    = COALESCE(excluded.topic_key, topic_key),
                    scanner_ts   = excluded.scanner_ts
                """,
                (workspace, name, kind, version, install_path, topic_key, _now_iso()),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: upsert_memory
# ---------------------------------------------------------------------------

VALID_MEMORY_TYPES = ("project", "user", "feedback", "atom", "decision", "negative")


# ---------------------------------------------------------------------------
# Structural enforcement: curated memory is owned by the orchestrator-operator
# pair. When a subagent dispatch carries GAIA_DISPATCH_AGENT, only those two
# identities are allowed to mutate the `memory` table. Absence of the env var
# means the caller is a human shell (CLI run directly) -- always permitted.
# See brief: memory-model-refactor-class-status-links-structural-enforcement.
# ---------------------------------------------------------------------------

class MemoryWriteForbidden(PermissionError):
    """Raised when a non-curator subagent attempts to mutate curated memory."""


_MEMORY_CURATOR_AGENTS = frozenset({
    "orchestrator",
    "operator",
    "gaia-orchestrator",
    "gaia-operator",
})


def _assert_dispatch_can_write_memory() -> None:
    """Block memory writes from non-curator subagent dispatches.

    Reads ``GAIA_DISPATCH_AGENT`` from the environment. The contract:

    * Unset -> human caller running the CLI directly. Allowed.
    * Set to an empty string -> treated as unset. Allowed.
    * Set to one of the curator identities -> allowed.
    * Set to anything else -> raises ``MemoryWriteForbidden``.

    Curated memory is the orchestrator-operator pair's substrate. Subagents
    (developer, platform-architect, gitops-operator, ...) record episodic
    events via the audit pipeline; they do not author the curated layer.
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    if raw in _MEMORY_CURATOR_AGENTS:
        return
    raise MemoryWriteForbidden(
        f"Curated memory writes are forbidden from subagent dispatches "
        f"(current GAIA_DISPATCH_AGENT={raw!r}). Memory is owned by the "
        f"orchestrator-operator pair."
    )

# Curated slug taxonomy: when the type is one of the new curated types
# (atom / decision / negative), the `name` must start with the MATCHING prefix
# for that specific type, and use snake_case slug discipline. The legacy types
# (project / user / feedback) keep their historical naming freedom, but are
# NOT allowed to use a curated prefix -- that combination is a mismatch that
# must fail loudly.
#
# Single-source-of-truth rule: the slug prefix IS the type. (slug, type) pairs
# that disagree are always an error -- never silently reclassified.
import re as _re_for_slug
_CURATED_SLUG_TYPES = ("atom", "decision", "negative")
_LEGACY_SLUG_TYPES = ("project", "user", "feedback")

# Pre-computed per-type patterns for precise prefix enforcement.
_CURATED_TYPE_PATTERNS = {
    t: _re_for_slug.compile(rf"^{t}_[a-z0-9_]+$")
    for t in _CURATED_SLUG_TYPES
}
# Used to detect when a legacy-type call uses a curated prefix (cross-direction mismatch).
_CURATED_PREFIX_PATTERN = _re_for_slug.compile(
    r"^(atom|decision|negative)_"
)


def _validate_curated_slug(name: str, type: str) -> None:
    """Raise ValueError when the slug and type disagree, in either direction.

    Rules (single source of truth: the slug prefix IS the type):
      * type in (atom, decision, negative): name must match '^{type}_[a-z0-9_]+$'
        exactly -- not just any curated prefix, the SPECIFIC one for this type.
      * type in (project, user, feedback): name must NOT start with any curated
        prefix (atom_, decision_, negative_). If it does, caller is expressing
        an impossible pair; fail loudly instead of reclassifying silently.
    """
    if type in _CURATED_SLUG_TYPES:
        pattern = _CURATED_TYPE_PATTERNS[type]
        if not pattern.match(name):
            raise ValueError(
                f"slug {name!r} does not match type={type!r}: "
                f"expected '^{type}_[a-z0-9_]+$' (e.g. '{type}_my_topic'). "
                f"The slug prefix must match the type -- they are the same thing."
            )
    elif type in _LEGACY_SLUG_TYPES:
        if _CURATED_PREFIX_PATTERN.match(name):
            # Extract the conflicting prefix so the error is actionable.
            conflicting_prefix = name.split("_")[0]
            raise ValueError(
                f"slug {name!r} starts with '{conflicting_prefix}_' but type={type!r}: "
                f"the slug prefix and the type must agree. "
                f"Either use --type={conflicting_prefix} to match the slug prefix, "
                f"or rename the slug to start with '{type}_'."
            )


def upsert_memory(
    workspace: str,
    name: str,
    *,
    type: str,
    body: str,
    description: str | None = None,
    origin_session_id: str | None = None,
    db_path: Path | None = None,
    workspace_path: Path | None = None,
) -> dict:
    """Upsert a curated-memory row in the ``memory`` table.
    """
    _assert_dispatch_can_write_memory()

    if type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"invalid memory type {type!r}; must be one of {list(VALID_MEMORY_TYPES)}"
        )
    if not body or not body.strip():
        raise ValueError("memory body cannot be empty")
    if not name or not name.strip():
        raise ValueError("memory name cannot be empty")
    _validate_curated_slug(name, type)

    if origin_session_id is None:
        origin_session_id = os.environ.get("GAIA_SESSION_ID") or None

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace, workspace_path)

            existing = con.execute(
                "SELECT name FROM memory WHERE workspace = ? AND name = ?",
                (workspace, name),
            ).fetchone()
            action = "updated" if existing is not None else "inserted"

            now = _now_iso()
            con.execute(
                """
                INSERT INTO memory (workspace, name, type, description, body,
                                    origin_session_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
                    type              = excluded.type,
                    description       = excluded.description,
                    body              = excluded.body,
                    origin_session_id = excluded.origin_session_id,
                    updated_at        = excluded.updated_at
                """,
                (workspace, name, type, description, body,
                 origin_session_id, now),
            )
            con.commit()
            return {
                "status": "applied",
                "action": action,
                "name": name,
                "updated_at": now,
            }
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: delete_memory / update_memory_field
# ---------------------------------------------------------------------------

_MEMORY_PATCHABLE_FIELDS = ("description", "body")


def delete_memory(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Hard-delete a curated memory row."""
    _assert_dispatch_can_write_memory()
    con = _connect(db_path)
    try:
        cur = con.execute(
            "DELETE FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def update_memory_field(
    workspace: str,
    name: str,
    field: str,
    content: str,
    *,
    append: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Patch a single column on a curated memory row."""
    _assert_dispatch_can_write_memory()
    if field not in _MEMORY_PATCHABLE_FIELDS:
        raise ValueError(
            f"invalid memory field {field!r}; must be one of "
            f"{list(_MEMORY_PATCHABLE_FIELDS)}"
        )
    if content is None or content == "":
        raise ValueError("content cannot be empty")

    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT {field}, body FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"memory '{name}' not found in workspace '{workspace}'"
            )

        existing = row[field] or ""
        if append and existing:
            new_value = f"{existing}\n\n{content}"
            action = "appended"
        else:
            new_value = content
            action = "overwritten"

        if field == "body" and not new_value.strip():
            raise ValueError("memory body cannot be empty")

        now = _now_iso()
        con.execute(
            f"UPDATE memory SET {field} = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (new_value, now, workspace, name),
        )
        con.commit()
        return {
            "status": "applied",
            "name": name,
            "field": field,
            "action": action,
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: memory_links (v4 graph primitives)
# ---------------------------------------------------------------------------
#
# Brief: memory-model-refactor-class-status-links-structural-enforcement (T4).
#
# Duplicate-edge policy (architectural decision):
#   The writer accepts ``if_exists`` with default ``"skip"``. Re-creating the
#   same edge is idempotent -- no error, returns ``{"status":"applied",
#   "action":"noop"}``. Callers that want strict semantics pass
#   ``if_exists="error"`` to receive a ValueError on duplicates.
#
# Rationale: declarative wiring ("thread X supersedes anchor Y") is the dominant
# CLI use case; idempotent default makes the `gaia memory link` command safely
# re-runnable from scripts and migration tooling. The strict mode is preserved
# for callers that need to detect drift (e.g. reclassify pipelines verifying
# that an edge they expected to be a one-time event did not silently re-fire).
#
# Existence enforcement: both src_name and dst_name MUST already exist in the
# ``memory`` table for the workspace. Links to non-existent slugs would leave
# dangling edges that the injector cannot resolve -- the writer raises ValueError
# instead of accepting them. ON DELETE CASCADE on workspace handles the deeper
# integrity guarantees at the SQLite layer.
# ---------------------------------------------------------------------------

VALID_MEMORY_LINK_KINDS = ("relates_to", "supersedes", "derived_from", "graduated_to")


def insert_memory_link(
    workspace: str,
    src_name: str,
    dst_name: str,
    kind: str,
    *,
    if_exists: str = "skip",
    db_path: Path | None = None,
) -> dict:
    """Insert a row into ``memory_links``. Idempotent by default.

    Both ``src_name`` and ``dst_name`` must already exist in the ``memory``
    table for ``workspace`` -- otherwise the writer refuses to create a
    dangling edge.

    Args:
        workspace:  Workspace name (FK -> workspaces.name).
        src_name:   Source memory slug (must exist in memory).
        dst_name:   Destination memory slug (must exist in memory).
        kind:       One of VALID_MEMORY_LINK_KINDS. The schema enforces this
                    via CHECK; the writer validates first for clearer errors.
        if_exists:  ``"skip"`` (default) -> idempotent re-insert returns
                    ``action="noop"``. ``"error"`` -> raise ValueError when
                    the (workspace, src, dst, kind) row already exists.
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "action": "inserted"|"noop",
         "workspace": ..., "src_name": ..., "dst_name": ..., "kind": ...,
         "created_at": ...}

    Raises:
        ValueError: invalid kind, missing src/dst, or if_exists="error" on dup.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    if kind not in VALID_MEMORY_LINK_KINDS:
        raise ValueError(
            f"invalid link kind {kind!r}; must be one of "
            f"{list(VALID_MEMORY_LINK_KINDS)}"
        )
    if if_exists not in ("skip", "error"):
        raise ValueError(
            f"invalid if_exists {if_exists!r}; must be 'skip' or 'error'"
        )
    if not src_name or not src_name.strip():
        raise ValueError("src_name cannot be empty")
    if not dst_name or not dst_name.strip():
        raise ValueError("dst_name cannot be empty")

    con = _connect(db_path)
    try:
        # Validate endpoints exist. Without these checks we silently create
        # edges to slugs that do not (yet) exist -- the injector and graph
        # walkers cannot recover from that.
        src_row = con.execute(
            "SELECT name FROM memory WHERE workspace = ? AND name = ?",
            (workspace, src_name),
        ).fetchone()
        if src_row is None:
            raise ValueError(
                f"src memory {src_name!r} not found in workspace "
                f"{workspace!r}"
            )
        dst_row = con.execute(
            "SELECT name FROM memory WHERE workspace = ? AND name = ?",
            (workspace, dst_name),
        ).fetchone()
        if dst_row is None:
            raise ValueError(
                f"dst memory {dst_name!r} not found in workspace "
                f"{workspace!r}"
            )

        existing = con.execute(
            "SELECT created_at FROM memory_links "
            "WHERE workspace = ? AND src_name = ? AND dst_name = ? AND kind = ?",
            (workspace, src_name, dst_name, kind),
        ).fetchone()
        if existing is not None:
            if if_exists == "error":
                raise ValueError(
                    f"memory_link already exists: ({workspace}, {src_name}, "
                    f"{dst_name}, {kind}) -- created_at={existing['created_at']}"
                )
            return {
                "status": "applied",
                "action": "noop",
                "workspace": workspace,
                "src_name": src_name,
                "dst_name": dst_name,
                "kind": kind,
                "created_at": existing["created_at"],
            }

        now = _now_iso()
        con.execute(
            "INSERT INTO memory_links (workspace, src_name, dst_name, kind, "
            "                          created_at) VALUES (?, ?, ?, ?, ?)",
            (workspace, src_name, dst_name, kind, now),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "workspace": workspace,
            "src_name": src_name,
            "dst_name": dst_name,
            "kind": kind,
            "created_at": now,
        }
    finally:
        con.close()


def delete_memory_link(
    workspace: str,
    src_name: str,
    dst_name: str,
    kind: str,
    *,
    if_missing: str = "skip",
    db_path: Path | None = None,
) -> dict:
    """Delete a row from ``memory_links``. Idempotent by default.

    Args:
        workspace, src_name, dst_name, kind: Full PK of the link.
        if_missing: ``"skip"`` (default) -> deleting a non-existent edge
                    returns ``action="noop"``. ``"error"`` -> raise ValueError.
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "action": "deleted"|"noop",
         "workspace": ..., "src_name": ..., "dst_name": ..., "kind": ...}

    Raises:
        ValueError: invalid kind, invalid if_missing, or if_missing="error"
                    when the row does not exist.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    if kind not in VALID_MEMORY_LINK_KINDS:
        raise ValueError(
            f"invalid link kind {kind!r}; must be one of "
            f"{list(VALID_MEMORY_LINK_KINDS)}"
        )
    if if_missing not in ("skip", "error"):
        raise ValueError(
            f"invalid if_missing {if_missing!r}; must be 'skip' or 'error'"
        )

    con = _connect(db_path)
    try:
        cur = con.execute(
            "DELETE FROM memory_links "
            "WHERE workspace = ? AND src_name = ? AND dst_name = ? AND kind = ?",
            (workspace, src_name, dst_name, kind),
        )
        con.commit()
        if cur.rowcount == 0:
            if if_missing == "error":
                raise ValueError(
                    f"memory_link not found: ({workspace}, {src_name}, "
                    f"{dst_name}, {kind})"
                )
            return {
                "status": "applied",
                "action": "noop",
                "workspace": workspace,
                "src_name": src_name,
                "dst_name": dst_name,
                "kind": kind,
            }
        return {
            "status": "applied",
            "action": "deleted",
            "workspace": workspace,
            "src_name": src_name,
            "dst_name": dst_name,
            "kind": kind,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: reclassify_memory (v4 class/status fields)
# ---------------------------------------------------------------------------
#
# Brief: memory-model-refactor-class-status-links-structural-enforcement (T5).
#
# The writer is the source of truth for the class/status enums (the schema
# does NOT carry CHECK constraints on these columns -- see schema.sql L572-578
# for the rebuild-avoidance rationale). Validation lives here.
#
# Design decisions captured here so future readers don't have to mine the
# brief:
#
#   1. **Auto-clear status when class moves away from thread.** When the
#      caller changes class from 'thread' to 'anchor' or 'log' (and does NOT
#      pass an explicit status flag), the writer NULLs the status column on
#      its own. Rationale: status is semantically meaningful only for
#      class=thread (schema.sql L576-578). Leaving a stale 'open' status on
#      an anchor row would silently corrupt the lifecycle view. Forcing
#      callers to pass --status=null on every class change is busywork that
#      hides the rule rather than naming it.
#
#   2. **Empty-string sentinel for explicit clear.** The Python signature
#      uses ``status=None`` to mean "don't touch this column". When the CLI
#      caller passes ``--status=null`` (the literal string), we translate it
#      to ``status=""`` in the kwargs -- the writer treats the empty string
#      as "explicitly clear to NULL". This separation is the only way to
#      distinguish "leave alone" from "wipe" when both routes need to coexist
#      on the same function signature.
# ---------------------------------------------------------------------------

VALID_MEMORY_CLASSES = ("anchor", "thread", "log")
VALID_MEMORY_STATUSES = ("open", "carry_forward", "graduated", "closed")


def reclassify_memory(
    workspace: str,
    name: str,
    *,
    class_: str | None = None,
    status: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Update the ``class`` and/or ``status`` columns on a curated memory row.

    Args:
        workspace: Workspace name (FK -> workspaces.name).
        name:      Curated memory slug; the row must already exist.
        class_:    New value for the ``class`` column. ``None`` means
                   "do not touch". Must be one of VALID_MEMORY_CLASSES
                   when set. The trailing underscore avoids the Python
                   reserved word.
        status:    New value for the ``status`` column. ``None`` means
                   "do not touch". Empty string ``""`` is the explicit-
                   clear sentinel: it nulls the column. Otherwise must be
                   one of VALID_MEMORY_STATUSES.
        db_path:   Optional explicit DB path (used by tests).

    Behaviour:
        * If neither ``class_`` nor ``status`` is supplied (both None) the
          writer raises ``ValueError`` -- there is nothing to do.
        * ``status`` may only resolve to a non-NULL value when the resulting
          class is ``"thread"``. If the caller asks for ``status="open"``
          on a row that is (or will be) class=anchor/log, ValueError fires
          with a message explaining the constraint.
        * When the caller changes class FROM 'thread' TO 'anchor' or 'log'
          and does NOT pass a status flag, status is auto-NULLed.

    Returns:
        ``{"status": "applied", "action": "reclassified", "name": name,
           "class": ..., "status": ..., "updated_at": ...}``.

    Raises:
        ValueError: missing row, invalid enum, missing both flags, or
                    status-without-thread.
        MemoryWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_memory()

    # Disambiguate the three input modes for status:
    #   * status is None        -> do not touch the column
    #   * status == ""          -> explicit clear (write NULL)
    #   * status == "<value>"   -> set to value; must be in enum
    status_explicit_clear = (status == "")
    status_touches_column = (status is not None)

    if class_ is None and not status_touches_column:
        raise ValueError(
            "reclassify_memory requires at least one of class_ or status"
        )

    if class_ is not None and class_ not in VALID_MEMORY_CLASSES:
        raise ValueError(
            f"invalid class {class_!r}; must be one of "
            f"{list(VALID_MEMORY_CLASSES)}"
        )

    if (status_touches_column
            and not status_explicit_clear
            and status not in VALID_MEMORY_STATUSES):
        raise ValueError(
            f"invalid status {status!r}; must be one of "
            f"{list(VALID_MEMORY_STATUSES)} (or empty string to clear)"
        )

    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT class, status FROM memory "
            "WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"memory {name!r} not found in workspace {workspace!r}"
            )

        current_class = row["class"]
        current_status = row["status"]

        new_class = class_ if class_ is not None else current_class

        # Decide the new status value:
        #   * Caller passed status explicit-clear -> NULL.
        #   * Caller passed status="<value>"      -> that value (already
        #                                            enum-checked above).
        #   * Caller did NOT pass status, AND class moved from thread to
        #     non-thread -> auto-NULL.
        #   * Otherwise -> leave current_status untouched.
        if status_touches_column:
            new_status = None if status_explicit_clear else status
        elif (current_class == "thread"
              and class_ is not None
              and class_ != "thread"):
            new_status = None  # auto-clear on demotion / promotion
        else:
            new_status = current_status

        if new_status is not None and new_class != "thread":
            raise ValueError(
                "status only applies to class=thread "
                f"(resulting class={new_class!r}, status={new_status!r})"
            )

        now = _now_iso()
        con.execute(
            "UPDATE memory SET class = ?, status = ?, updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (new_class, new_status, now, workspace, name),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "reclassified",
            "workspace": workspace,
            "name": name,
            "class": new_class,
            "memory_status": new_status,  # avoid colliding with envelope 'status'
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: search_memory_curated (FTS5 over the memory table)
# ---------------------------------------------------------------------------

import re as _re_for_fts

_MEMORY_FTS_SAFE = _re_for_fts.compile(r"^[A-Za-z0-9_*\s\"]+$")


def _prepare_memory_fts_query(query: str) -> str:
    q = (query or "").strip()
    if not q:
        return q
    if _MEMORY_FTS_SAFE.match(q):
        return q
    return '"' + q.replace('"', '""') + '"'


def search_memory_curated(
    workspace: str,
    query: str,
    *,
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Run FTS5 MATCH against ``memory_fts`` and join with the ``memory`` table."""
    fts_q = _prepare_memory_fts_query(query)
    con = _connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT m.name, m.type, m.description,
                   snippet(memory_fts, -1, '[', ']', '...', 16) AS snippet,
                   bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memory m ON m.rowid = memory_fts.rowid
            WHERE memory_fts MATCH ?
              AND m.workspace = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_q, workspace, limit),
        ).fetchall()
        return [
            {
                "name": r["name"],
                "type": r["type"],
                "description": r["description"],
                "snippet": r["snippet"],
                "rank": r["rank"],
            }
            for r in rows
        ]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: memory read helpers
# ---------------------------------------------------------------------------

def get_memory(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Return a curated memory row as a dict, or ``None`` when missing."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT workspace, name, type, description, body, "
            "       origin_session_id, updated_at "
            "FROM memory WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}
    finally:
        con.close()


def list_memory(
    workspace: str,
    *,
    type: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """List curated memory rows, optionally filtered by ``type``."""
    con = _connect(db_path)
    try:
        if type is None:
            rows = con.execute(
                "SELECT name, type, description, updated_at "
                "FROM memory WHERE workspace = ? ORDER BY name",
                (workspace,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT name, type, description, updated_at "
                "FROM memory WHERE workspace = ? AND type = ? ORDER BY name",
                (workspace, type),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: brief field patch
# ---------------------------------------------------------------------------

_BRIEF_PATCHABLE_FIELDS = (
    "objective",
    "context",
    "approach",
    "out_of_scope",
    "description",
    "title",
    # v5 (T5.4): metadata fields added to the whitelist so they are
    # reachable via `gaia brief edit --headless --field=<field>`.
    "surface_type",
    "topic_key",
)


def update_brief_field(
    workspace: str,
    name: str,
    field: str,
    content: str,
    *,
    append: bool = False,
    db_path: Path | None = None,
) -> dict:
    if field not in _BRIEF_PATCHABLE_FIELDS:
        raise ValueError(
            f"invalid brief field {field!r}; must be one of "
            f"{list(_BRIEF_PATCHABLE_FIELDS)}"
        )
    if content is None or content == "":
        raise ValueError("content cannot be empty")

    column = "objective" if field == "description" else field

    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT id, {column} FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"brief '{name}' not found in workspace '{workspace}'"
            )

        existing = row[column] or ""
        if append and existing:
            new_value = f"{existing}\n\n{content}"
            action = "appended"
        else:
            new_value = content
            action = "overwritten"

        now = _now_iso()
        con.execute(
            f"UPDATE briefs SET {column} = ?, updated_at = ? WHERE id = ?",
            (new_value, now, row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "name": name,
            "field": field,
            "action": action,
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: plan CRUD
# ---------------------------------------------------------------------------

VALID_PLAN_LIFECYCLE_STATUSES = ("draft", "active", "closed")

# Brief statuses that reject plan persistence (D11, D13).
# Saving a plan against a closed or archived brief is always a mistake --
# fail-fast rather than silently creating an orphaned plan.
_PLAN_SAVE_REJECTED_BRIEF_STATUSES = frozenset({"closed", "archived"})


def _resolve_brief_id(
    con: sqlite3.Connection,
    workspace: str,
    brief_name: str,
) -> int | None:
    row = con.execute(
        "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
        (workspace, brief_name),
    ).fetchone()
    return row["id"] if row else None


def upsert_plan(
    workspace: str,
    brief_name: str,
    *,
    content: str | None = None,
    status: str = "draft",
    db_path: Path | None = None,
) -> dict:
    """Upsert the plan attached to ``brief_name``.

    Canonical persistence path for plans
    ------------------------------------
    This function is the ONE canonical writer for plan content. It is invoked
    by ``gaia plan save --brief=<name> --content="..." [--status=...]`` and
    has UPSERT semantics:

    * If no plan row exists for the brief -> INSERT a new row.
    * If a plan row exists -> UPDATE ``status`` and ``content`` (preserving
      the existing ``content`` when ``content=None`` is passed).

    The ``plans`` row id is permanent across re-runs of ``gaia plan save``;
    only the content and status fields are updated. Verify after saving with
    ``gaia plan show <brief>``.

    Note that this writer is NOT full-sync. It only touches
    ``plans.status`` and ``plans.content``. The ``tasks`` table is a
    separate child of ``plans`` (FK CASCADE on delete). To mutate the
    task list, use the granular writers ``add_task_to_plan``,
    ``remove_task_from_plan``, and ``reorder_tasks`` (NOT this function).

    Anti-patterns -- DO NOT use any of these:

    * ``gaia brief edit <name>`` to persist a plan. ``gaia brief edit``
      writes to the ``briefs`` table, not the ``plans`` table. Plans and
      briefs are separate rows in separate tables. Edits applied to the
      brief body do not appear in ``gaia plan show``.

    * ``EDITOR=cp /tmp/plan.md gaia brief edit <name>``. This was a hack
      used during session 2026-05-22 to side-load plan content. It bypasses
      DB semantics, writes to the wrong table, and produces a stale brief
      body that does not appear in ``gaia plan show``. Never repeat this
      pattern. Use ``gaia plan save`` with ``--content="$(cat /tmp/plan.md)"``
      if the content is too large to pass inline.

    Raises ValueError if the brief does not exist, if the brief status is
    ``closed`` or ``archived`` (D11 fail-fast), or if the status enum is
    invalid.
    """
    if status not in VALID_PLAN_LIFECYCLE_STATUSES:
        raise ValueError(
            f"invalid plan status {status!r}; must be one of "
            f"{list(VALID_PLAN_LIFECYCLE_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )

        # D11 / D13: fail-fast guard -- reject plan persistence against
        # a brief whose lifecycle has ended.
        brief_status_row = con.execute(
            "SELECT status FROM briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if brief_status_row is not None:
            brief_status = brief_status_row["status"]
            if brief_status in _PLAN_SAVE_REJECTED_BRIEF_STATUSES:
                raise ValueError(
                    f"cannot save plan for brief '{brief_name}': brief is "
                    f"'{brief_status}'. Only briefs with status in "
                    f"{{draft, open, in-progress}} accept new plans."
                )

        existing = con.execute(
            "SELECT id, status, content FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()

        now = _now_iso()
        if existing is None:
            con.execute(
                "INSERT INTO plans (brief_id, status, content, created_at, "
                "                   updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (brief_id, status, content, now, now),
            )
            plan_id = con.execute(
                "SELECT id FROM plans WHERE brief_id = ?",
                (brief_id,),
            ).fetchone()["id"]
            action = "inserted"
            new_status = status
        else:
            plan_id = existing["id"]
            new_status = status
            new_content = content if content is not None else existing["content"]
            con.execute(
                "UPDATE plans SET status = ?, content = ?, updated_at = ? "
                "WHERE id = ?",
                (new_status, new_content, now, plan_id),
            )
            action = "updated"

        con.commit()
        return {
            "status": "applied",
            "action": action,
            "brief_name": brief_name,
            "plan_id": plan_id,
            "plan_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


def get_plan(
    workspace: str,
    brief_name: str,
    *,
    db_path: Path | None = None,
) -> dict | None:
    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            return None
        row = con.execute(
            "SELECT id, brief_id, status, content, created_at, updated_at "
            "FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()
        if row is None:
            return None
        out = {k: row[k] for k in row.keys()}
        out["brief_name"] = brief_name
        return out
    finally:
        con.close()


def list_plans(
    workspace: str,
    *,
    brief_name: str | None = None,
    status: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    con = _connect(db_path)
    try:
        sql = (
            "SELECT p.id, p.brief_id, p.status, p.created_at, p.updated_at, "
            "       b.name AS brief_name "
            "FROM plans p "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.workspace = ? "
        )
        params: list = [workspace]
        if brief_name is not None:
            sql += "AND b.name = ? "
            params.append(brief_name)
        if status is not None:
            sql += "AND p.status = ? "
            params.append(status)
        sql += "ORDER BY b.name"
        rows = con.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_plan(
    workspace: str,
    brief_name: str,
    *,
    db_path: Path | None = None,
) -> bool:
    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            return False
        cur = con.execute("DELETE FROM plans WHERE brief_id = ?", (brief_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def set_plan_status(
    workspace: str,
    brief_name: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("plans")

    if new_status not in VALID_PLAN_LIFECYCLE_STATUSES:
        raise ValueError(
            f"invalid plan status {new_status!r}; must be one of "
            f"{list(VALID_PLAN_LIFECYCLE_STATUSES)}"
        )

    from gaia.state.transitions import assert_legal_plan_lifecycle

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        row = con.execute(
            "SELECT id, status FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}' in workspace "
                f"'{workspace}'"
            )

        old_status = row["status"] or "draft"
        if old_status == new_status:
            return {
                "brief_name": brief_name,
                "old_status": old_status,
                "new_status": new_status,
                "action": "noop",
                "warnings": [],
            }

        assert_legal_plan_lifecycle(old_status, new_status)

        con.execute(
            "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now_iso(), row["id"]),
        )
        con.commit()

        # D11 (advisory): when closing a plan, check AC satisfaction.
        # Emit warnings for pending/blocked ACs -- the close is still
        # permitted (ACs may be intentionally deferred), but the caller
        # should surface any unsatisfied criteria to the user.
        warnings: list[str] = []
        if new_status == "closed":
            pending_acs = con.execute(
                "SELECT ac_id, status FROM acceptance_criteria "
                "WHERE brief_id = ? AND status != 'done'",
                (brief_id,),
            ).fetchall()
            warnings = [
                f"AC '{r['ac_id']}' is status='{r['status']}' (not done)"
                for r in pending_acs
            ]

        return {
            "brief_name": brief_name,
            "old_status": old_status,
            "new_status": new_status,
            "action": "updated",
            "warnings": warnings,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: set_task_status, set_ac_status, set_milestone_status (v5)
# ---------------------------------------------------------------------------

def set_task_status(
    workspace: str,
    brief_name: str,
    task_id: int,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition a task's ``status`` after validating the move is legal.

    Navigates workspace -> brief_name -> brief_id -> plan_id -> task row
    by ``(plan_id, order_num)`` where ``task_id`` is the order_num integer.

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises ValueError on illegal transition or missing entity.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")
    from gaia.state import VALID_TASK_STATUSES
    from gaia.state.transitions import assert_legal_task_lifecycle

    if new_status not in VALID_TASK_STATUSES:
        raise ValueError(
            f"invalid task status {new_status!r}; must be one of "
            f"{list(VALID_TASK_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?",
            (brief_id,),
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}' in workspace '{workspace}'"
            )
        plan_id = plan_row["id"]

        task_row = con.execute(
            "SELECT id, status FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, task_id),
        ).fetchone()
        if task_row is None:
            raise ValueError(
                f"task with order_num={task_id} not found in plan for brief "
                f"'{brief_name}'"
            )

        old_status = task_row["status"] or "pending"
        if old_status == new_status:
            return {
                "status": "applied",
                "action": "noop",
                "brief_name": brief_name,
                "entity_id": task_id,
                "old_status": old_status,
                "new_status": new_status,
                "updated_at": _now_iso(),
            }

        assert_legal_task_lifecycle(old_status, new_status)

        now = _now_iso()
        con.execute(
            "UPDATE tasks SET status = ? WHERE id = ?",
            (new_status, task_row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "entity_id": task_id,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


def set_ac_status(
    workspace: str,
    brief_name: str,
    ac_id: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition an acceptance criterion's ``status`` after validating the move.

    Navigates workspace -> brief_name -> brief_id -> AC row by (brief_id, ac_id).

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises ValueError on illegal transition or missing entity.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("acceptance_criteria")
    from gaia.state import VALID_AC_STATUSES
    from gaia.state.transitions import assert_legal_ac_lifecycle

    if new_status not in VALID_AC_STATUSES:
        raise ValueError(
            f"invalid AC status {new_status!r}; must be one of "
            f"{list(VALID_AC_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )

        ac_row = con.execute(
            "SELECT id, status FROM acceptance_criteria "
            "WHERE brief_id = ? AND ac_id = ?",
            (brief_id, ac_id),
        ).fetchone()
        if ac_row is None:
            raise ValueError(
                f"AC '{ac_id}' not found in brief '{brief_name}'"
            )

        old_status = ac_row["status"] or "pending"
        if old_status == new_status:
            return {
                "status": "applied",
                "action": "noop",
                "brief_name": brief_name,
                "entity_id": ac_id,
                "old_status": old_status,
                "new_status": new_status,
                "updated_at": _now_iso(),
            }

        assert_legal_ac_lifecycle(old_status, new_status)

        now = _now_iso()
        con.execute(
            "UPDATE acceptance_criteria SET status = ? WHERE id = ?",
            (new_status, ac_row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "entity_id": ac_id,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


def set_milestone_status(
    workspace: str,
    brief_name: str,
    milestone_name: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition a milestone's ``status`` after validating the move.

    Navigates workspace -> brief_name -> brief_id -> milestone row by
    (brief_id, name).

    Returns a dict with keys: status, action, brief_name, entity_id,
    old_status, new_status, updated_at.

    Raises ValueError on illegal transition or missing entity.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("milestones")
    from gaia.state import VALID_MILESTONE_STATUSES
    from gaia.state.transitions import assert_legal_milestone_lifecycle

    if new_status not in VALID_MILESTONE_STATUSES:
        raise ValueError(
            f"invalid milestone status {new_status!r}; must be one of "
            f"{list(VALID_MILESTONE_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )

        ms_row = con.execute(
            "SELECT id, status FROM milestones "
            "WHERE brief_id = ? AND name = ?",
            (brief_id, milestone_name),
        ).fetchone()
        if ms_row is None:
            raise ValueError(
                f"milestone '{milestone_name}' not found in brief '{brief_name}'"
            )

        old_status = ms_row["status"] or "pending"
        if old_status == new_status:
            return {
                "status": "applied",
                "action": "noop",
                "brief_name": brief_name,
                "entity_id": milestone_name,
                "old_status": old_status,
                "new_status": new_status,
                "updated_at": _now_iso(),
            }

        assert_legal_milestone_lifecycle(old_status, new_status)

        now = _now_iso()
        con.execute(
            "UPDATE milestones SET status = ? WHERE id = ?",
            (new_status, ms_row["id"]),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "entity_id": milestone_name,
            "old_status": old_status,
            "new_status": new_status,
            "updated_at": now,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: granular task mutation in plans (v5 -- T5.3)
# ---------------------------------------------------------------------------
#
# upsert_plan is intentionally NOT full-sync (D7): it only touches
# plans.status and plans.content. The tasks child table needs its own
# granular writers. tasks are curator_only=False (D1) -- subagents allowed.


def add_task_to_plan(
    workspace: str,
    brief_name: str,
    order_num: int,
    goal: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert a new task row at ``order_num`` in the plan attached to brief.

    Raises ValueError on duplicate order_num within the plan or missing plan.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    if not goal or not goal.strip():
        raise ValueError("task goal cannot be empty")
    if order_num is None or order_num < 1:
        raise ValueError("order_num must be a positive integer")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}'"
            )
        plan_id = plan_row["id"]

        collision = con.execute(
            "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, order_num),
        ).fetchone()
        if collision is not None:
            raise ValueError(
                f"task with order_num={order_num} already exists in plan "
                f"for brief '{brief_name}'"
            )

        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, 'pending')",
            (plan_id, order_num, goal),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "brief_name": brief_name,
            "order_num": order_num,
        }
    finally:
        con.close()


def remove_task_from_plan(
    workspace: str,
    brief_name: str,
    order_num: int,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete a task row by (plan, order_num).

    Raises ValueError if the brief, plan, or task does not exist.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}'"
            )
        plan_id = plan_row["id"]

        cur = con.execute(
            "DELETE FROM tasks WHERE plan_id = ? AND order_num = ?",
            (plan_id, order_num),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"task with order_num={order_num} not found in plan for "
                f"brief '{brief_name}'"
            )
        con.commit()
        return {
            "status": "applied",
            "action": "deleted",
            "brief_name": brief_name,
            "order_num": order_num,
        }
    finally:
        con.close()


def reorder_tasks(
    workspace: str,
    brief_name: str,
    swaps: list[list[int]],
    *,
    db_path: Path | None = None,
) -> dict:
    """Swap task order_num pairs in a single transaction.

    ``swaps`` is a list of ``[from_order, to_order]`` pairs. Each swap
    exchanges the order_num of the two tasks atomically. If either task
    does not exist, the entire operation is rolled back.

    Raises ValueError on missing brief/plan or task not found.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("tasks")

    if not swaps:
        raise ValueError("swaps cannot be empty")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id(con, workspace, brief_name)
        if brief_id is None:
            raise ValueError(
                f"brief '{brief_name}' not found in workspace '{workspace}'"
            )
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(
                f"no plan attached to brief '{brief_name}'"
            )
        plan_id = plan_row["id"]

        applied: list[dict] = []
        try:
            con.execute("BEGIN")
            for pair in swaps:
                if len(pair) != 2:
                    raise ValueError(
                        f"swap entries must be [from, to] pairs, got {pair!r}"
                    )
                from_o, to_o = pair[0], pair[1]
                from_row = con.execute(
                    "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
                    (plan_id, from_o),
                ).fetchone()
                to_row = con.execute(
                    "SELECT id FROM tasks WHERE plan_id = ? AND order_num = ?",
                    (plan_id, to_o),
                ).fetchone()
                if from_row is None:
                    raise ValueError(
                        f"task with order_num={from_o} not found in plan"
                    )
                if to_row is None:
                    raise ValueError(
                        f"task with order_num={to_o} not found in plan"
                    )
                # Three-step swap via temporary negative order_num to avoid
                # the UNIQUE/CHECK conflict if we ever add a unique index.
                # tasks table currently has no unique index on (plan_id,
                # order_num), so two-step would also work. Three-step is
                # defensive.
                con.execute(
                    "UPDATE tasks SET order_num = -1 * order_num WHERE id = ?",
                    (from_row["id"],),
                )
                con.execute(
                    "UPDATE tasks SET order_num = ? WHERE id = ?",
                    (from_o, to_row["id"]),
                )
                con.execute(
                    "UPDATE tasks SET order_num = ? WHERE id = ?",
                    (to_o, from_row["id"]),
                )
                applied.append({"from": from_o, "to": to_o})
            con.commit()
        except Exception:
            con.rollback()
            raise

        return {
            "status": "applied",
            "action": "reordered",
            "brief_name": brief_name,
            "swaps": applied,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: wipe_workspace
# ---------------------------------------------------------------------------

def wipe_workspace(workspace: str, *, db_path: Path | None = None) -> None:
    """Delete the workspaces row for `workspace`. FK CASCADE removes all
    child rows (projects, apps, integrations, etc.) automatically.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute("DELETE FROM workspaces WHERE name = ?", (workspace,))
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: approval_grants (DB-backed command_set grant store, M3)
# ---------------------------------------------------------------------------
# These functions are the authoritative write path for the approval_grants
# table added in v7 (M3).  Filesystem JSON approval files are superseded by
# this DB store per D5.  No permission enforcement is applied here -- the
# approval_grants table is system-internal and not agent-owned per the
# agent_permissions matrix.

import json as _json  # local alias to avoid shadowing top-level json in callers


def insert_approval_grant(
    approval_id: str,
    command_set: list[dict],
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    scope: str = "COMMAND_SET",
    expires_at: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Insert a new approval grant row (status=PENDING).

    Args:
        approval_id: Unique nonce identifying this grant.
        command_set: List of dicts with keys ``command`` (str) and
            ``rationale`` (str).  Each entry is single-use; the
            ``consumed_indexes_json`` column tracks which have been used.
        agent_id: Requesting agent identifier.
        session_id: CLAUDE_SESSION_ID at grant creation time.
        scope: Grant scope type (default 'COMMAND_SET').
        expires_at: Optional ISO8601 expiry timestamp.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "error", "reason": ...} on failure.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT INTO approval_grants
                    (approval_id, agent_id, session_id, command_set_json,
                     scope, created_at, expires_at, status,
                     consumed_indexes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', '[]')
                """,
                (
                    approval_id,
                    agent_id,
                    session_id,
                    _json.dumps(command_set),
                    scope,
                    _now_iso(),
                    expires_at,
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def update_approval_grant_status(
    approval_id: str,
    status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Update the status column of an existing approval_grants row.

    Args:
        approval_id: The grant to update.
        status: New status value (PENDING|CONSUMED|REVOKED|EXPIRED).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                "UPDATE approval_grants SET status = ? WHERE approval_id = ?",
                (status, approval_id),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def mark_command_set_item_consumed(
    approval_id: str,
    index: int,
    *,
    db_path: Path | None = None,
) -> dict:
    """Mark a single command_set item (by index) as consumed.

    Appends ``index`` to ``consumed_indexes_json``.  When all items in the
    command_set are consumed the grant status is set to CONSUMED and
    ``consumed_at`` is stamped.

    Args:
        approval_id: The grant whose item was just executed.
        index: Zero-based index of the command_set item that matched.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied", "all_consumed": bool} on success.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            row = con.execute(
                "SELECT command_set_json, consumed_indexes_json, status "
                "FROM approval_grants WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                con.rollback()
                return {"status": "error", "reason": f"approval_id {approval_id!r} not found"}

            command_set = _json.loads(row[0] or "[]")
            consumed = _json.loads(row[1] or "[]")
            if index not in consumed:
                consumed.append(index)

            all_consumed = len(consumed) >= len(command_set)
            now = _now_iso()
            if all_consumed:
                con.execute(
                    """
                    UPDATE approval_grants
                    SET consumed_indexes_json = ?,
                        status = 'CONSUMED',
                        consumed_at = ?
                    WHERE approval_id = ?
                    """,
                    (_json.dumps(consumed), now, approval_id),
                )
            else:
                con.execute(
                    "UPDATE approval_grants SET consumed_indexes_json = ? WHERE approval_id = ?",
                    (_json.dumps(consumed), approval_id),
                )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied({"all_consumed": all_consumed})
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def revoke_approval_grant(
    approval_id: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Revoke an approval grant (status=REVOKED, revoked_at=now).

    After revocation, any command in the command_set that hasn't been
    executed yet will require fresh approval.

    Args:
        approval_id: The grant to revoke.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "not_found"} if no row.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                UPDATE approval_grants
                SET status = 'REVOKED', revoked_at = ?
                WHERE approval_id = ? AND status NOT IN ('REVOKED', 'CONSUMED')
                """,
                (_now_iso(), approval_id),
            )
            if cur.rowcount == 0:
                # Either not found or already in terminal state
                exists = con.execute(
                    "SELECT status FROM approval_grants WHERE approval_id = ?",
                    (approval_id,),
                ).fetchone()
                con.rollback()
                if exists is None:
                    return {"status": "not_found"}
                return {"status": "no_op", "current_status": exists[0]}
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def list_approval_grants(
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    db_path: Path | None = None,
) -> list[dict]:
    """Query approval_grants rows with optional filters.

    Args:
        agent_id: Filter by requesting agent.
        session_id: Filter by CLAUDE session ID.
        status: Filter by status (PENDING|CONSUMED|REVOKED|EXPIRED).
        limit: Maximum rows to return (default 100).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        List of dicts keyed by column name, ordered by created_at DESC.
    """
    con = _connect(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = con.execute(
            f"SELECT * FROM approval_grants {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: insert_semantic_grant / check_db_semantic_grant /
#             consume_db_semantic_grant (CHECK-side DB cutover, Brief 71)
# ---------------------------------------------------------------------------
#
# These three functions implement the DB-primary path for SCOPE_SEMANTIC_SIGNATURE
# grants created by activate_db_pending_by_prefix().  They use the same
# approval_grants table (scope='SCOPE_SEMANTIC_SIGNATURE') so all grant lifecycle
# is visible in one place.
#
# Lifecycle:
#   insert_semantic_grant()     -- called by activate_db_pending_by_prefix(); writes
#                                  row with status=PENDING.
#   check_db_semantic_grant()   -- called by check_approval_grant(); returns the
#                                  matching row dict when a valid grant exists.
#   consume_db_semantic_grant() -- called by bash_validator after command executes;
#                                  sets status=CONSUMED + consumed_at.
# ---------------------------------------------------------------------------


def insert_semantic_grant(
    approval_id: str,
    command: str,
    scope_signature: dict,
    *,
    agent_id: str | None = None,
    session_id: str | None = None,
    ttl_minutes: int = 5,
    db_path: Path | None = None,
) -> dict:
    """Insert a SCOPE_SEMANTIC_SIGNATURE row into approval_grants (status=PENDING).

    Called by activate_db_pending_by_prefix() after the user approves via
    AskUserQuestion.  The row represents a grant valid for one execution of
    the approved command within the TTL window.

    Args:
        approval_id: The P-{hex} approval id that was activated.  Used as PK.
        command: The exact command string approved by the user.
        scope_signature: Dict from ApprovalSignature.to_dict() -- stored in
            command_set_json so check_db_semantic_grant() can match semantically.
        agent_id: Requesting agent identifier.
        session_id: CLAUDE_SESSION_ID of the subagent that will execute.
        ttl_minutes: Grant lifetime in minutes (default 5, matches filesystem TTL).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success, {"status": "error", "reason": ...} otherwise.
    """
    from datetime import datetime, timezone, timedelta

    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # command_set_json stores the scope_signature dict so CHECK side can match.
    # We also include the original command for audit trail.
    grant_data = {
        "command": command,
        "scope_signature": scope_signature,
    }

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT OR IGNORE INTO approval_grants
                    (approval_id, agent_id, session_id, command_set_json,
                     scope, created_at, expires_at, status,
                     consumed_indexes_json)
                VALUES (?, ?, ?, ?, 'SCOPE_SEMANTIC_SIGNATURE', ?, ?, 'PENDING', '[]')
                """,
                (
                    approval_id,
                    agent_id,
                    session_id,
                    _json.dumps(grant_data),
                    _now_iso(),
                    expires_at,
                ),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


def check_db_semantic_grant(
    command: str,
    session_id: str | None = None,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Find an active SCOPE_SEMANTIC_SIGNATURE grant for command in the DB.

    Called by check_approval_grant() as the primary (DB) check path.

    Matching uses the scope_signature stored in command_set_json:
    - Deserializes the ApprovalSignature via ApprovalSignature.from_dict()
    - Delegates to matches_approval_signature() for semantic comparison

    Grant must:
    - Have scope='SCOPE_SEMANTIC_SIGNATURE'
    - Have status='PENDING'
    - Not be past its expires_at timestamp
    - Belong to session_id (when provided)

    Args:
        command: The command string to check.
        session_id: CLAUDE_SESSION_ID to scope lookup.  When None, all sessions
            are searched -- useful for cross-session grant lookup.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        Dict with grant row data when a matching grant is found, None otherwise.
    """
    from datetime import datetime, timezone
    from pathlib import Path as _Path

    try:
        # Import matching utilities lazily to avoid circular imports at module load.
        # These are in the hooks package, not gaia.store.
        import sys as _sys
        _hooks_root = str(_Path(__file__).resolve().parents[2] / "hooks")
        if _hooks_root not in _sys.path:
            _sys.path.insert(0, _hooks_root)

        from modules.security.approval_scopes import (
            ApprovalSignature,
            matches_approval_signature,
        )
    except ImportError:
        # Hooks package not available (e.g. standalone gaia.store test context).
        # Fall back to None -- callers handle None gracefully.
        return None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    con = _connect(db_path)
    try:
        clauses = [
            "scope = 'SCOPE_SEMANTIC_SIGNATURE'",
            "status = 'PENDING'",
        ]
        params: list = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)

        where = " AND ".join(clauses)
        rows = con.execute(
            f"SELECT * FROM approval_grants WHERE {where} ORDER BY created_at DESC",
            params,
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            # TTL check: expires_at column holds ISO8601 string.
            expires_at = row_dict.get("expires_at")
            if expires_at and expires_at < now_iso:
                continue

            command_set_json = row_dict.get("command_set_json") or "{}"
            try:
                grant_data = _json.loads(command_set_json)
            except Exception:
                continue

            sig_dict = grant_data.get("scope_signature")
            if not sig_dict:
                continue

            try:
                signature = ApprovalSignature.from_dict(sig_dict)
                if matches_approval_signature(signature, command):
                    return row_dict
            except Exception:
                continue

        return None
    except Exception:
        return None
    finally:
        con.close()


def consume_db_semantic_grant(
    approval_id: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Mark a SCOPE_SEMANTIC_SIGNATURE grant as CONSUMED (replay protection).

    Called by bash_validator immediately after a command is allowed via a DB
    semantic grant.  Setting status=CONSUMED prevents the same grant from
    being reused within the TTL window (Gap B fix).

    Args:
        approval_id: The grant to consume.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        True if the grant was found and consumed, False otherwise.
    """
    now = _now_iso()
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                UPDATE approval_grants
                SET status = 'CONSUMED', consumed_at = ?
                WHERE approval_id = ?
                  AND scope = 'SCOPE_SEMANTIC_SIGNATURE'
                  AND status = 'PENDING'
                """,
                (now, approval_id),
            )
            con.commit()
            return cur.rowcount > 0
        except Exception:
            con.rollback()
            raise
    except Exception:
        return False
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: agent_contract_handoffs (v9 / M4)
# ---------------------------------------------------------------------------
#
# Brief: agent-contract-handoff M4 (handoff persistence).
#
# Guard pattern mirrors _assert_dispatch_can_write_memory:
#   Handoff rows are written by the SubagentStop hook, which runs as the
#   orchestrator or operator context.  Subagent dispatches (specialist
#   agents) must NOT write their own handoff rows -- that would allow
#   self-reporting without hook oversight.
# ---------------------------------------------------------------------------

class HandoffWriteForbidden(PermissionError):
    """Raised when a non-curator subagent attempts to write a handoff row."""


_HANDOFF_WRITER_AGENTS = frozenset({
    "orchestrator",
    "operator",
    "gaia-orchestrator",
    "gaia-operator",
    # The SubagentStop hook runs outside any dispatch context (GAIA_DISPATCH_AGENT
    # is unset in the hook process), so the unset case is also allowed below.
})


def _assert_dispatch_can_write_handoff() -> None:
    """Block handoff writes from non-curator subagent dispatches.

    Contract (mirrors _assert_dispatch_can_write_memory):
    * GAIA_DISPATCH_AGENT unset / empty -> hook context or CLI, allowed.
    * Set to a curator identity -> allowed.
    * Set to anything else -> raises HandoffWriteForbidden.
    """
    raw = os.environ.get("GAIA_DISPATCH_AGENT")
    if not raw:
        return
    if raw in _HANDOFF_WRITER_AGENTS:
        return
    raise HandoffWriteForbidden(
        f"agent_contract_handoffs writes are forbidden from subagent dispatches "
        f"(current GAIA_DISPATCH_AGENT={raw!r}). Handoff rows are written by "
        f"the SubagentStop hook only."
    )


def insert_agent_contract_handoff(
    agent_id: str,
    workspace: str,
    task_status: str,
    raw_handoff_json: str,
    *,
    session_id: str | None = None,
    brief_id: int | None = None,
    db_path: "Path | None" = None,
) -> int:
    """Insert a row into agent_contract_handoffs.

    Called by the SubagentStop hook after parsing and resolving the contract
    envelope.  Returns the new row's id (handoff_id).

    Args:
        agent_id:         Agent identity string (e.g. "a1b2c3d4e5").
        workspace:        Workspace name (FK -> workspaces.name).
        task_status:      Resolved plan_status from the contract envelope.
        raw_handoff_json: Full contract envelope serialized as JSON string.
        session_id:       CLAUDE_SESSION_ID at SubagentStop time (optional).
        brief_id:         briefs.id FK (optional -- EXTENSION_POINT for
                          state-machine-completion downstream briefs).
        db_path:          Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the inserted row.

    Raises:
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
    """
    _assert_dispatch_can_write_handoff()

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            cur = con.execute(
                """
                INSERT INTO agent_contract_handoffs
                    (agent_id, session_id, workspace, brief_id,
                     task_status, raw_handoff_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    session_id,
                    workspace,
                    brief_id,
                    task_status,
                    raw_handoff_json,
                    _now_iso(),
                ),
            )
            handoff_id = cur.lastrowid
            con.commit()
            return handoff_id
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


def insert_handoff_approval(
    handoff_id: int,
    approval_id: str,
    decision: str,
    decided_at: str,
    *,
    db_path: "Path | None" = None,
) -> int:
    """Insert a row into agent_contract_handoff_approvals.

    Args:
        handoff_id:  FK -> agent_contract_handoffs.id.
        approval_id: FK -> approval_grants.approval_id.
        decision:    One of APPROVED|REJECTED|EXPIRED|REVOKED.
        decided_at:  ISO8601 timestamp of the decision.
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        Integer primary key of the inserted row.

    Raises:
        HandoffWriteForbidden: when GAIA_DISPATCH_AGENT names a non-curator.
        ValueError: if decision is not a valid enum value.
    """
    _assert_dispatch_can_write_handoff()

    _VALID_DECISIONS = {"APPROVED", "REJECTED", "EXPIRED", "REVOKED"}
    if decision not in _VALID_DECISIONS:
        raise ValueError(
            f"invalid decision {decision!r}; must be one of "
            f"{sorted(_VALID_DECISIONS)}"
        )

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            cur = con.execute(
                """
                INSERT INTO agent_contract_handoff_approvals
                    (handoff_id, approval_id, decision, decided_at)
                VALUES (?, ?, ?, ?)
                """,
                (handoff_id, approval_id, decision, decided_at),
            )
            approval_row_id = cur.lastrowid
            con.commit()
            return approval_row_id
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()


def list_agent_contract_handoffs(
    *,
    workspace: str | None = None,
    agent_id: str | None = None,
    session_id: str | None = None,
    brief_id: int | None = None,
    task_status: str | None = None,
    limit: int = 100,
    db_path: "Path | None" = None,
) -> list[dict]:
    """Query agent_contract_handoffs with optional filters.

    Args:
        workspace:   Filter by workspace name.
        agent_id:    Filter by agent identity string.
        session_id:  Filter by CLAUDE session ID.
        brief_id:    Filter by briefs.id FK.
        task_status: Filter by resolved plan_status.
        limit:       Maximum rows to return (default 100).
        db_path:     Optional explicit DB path (used by tests).

    Returns:
        List of dicts keyed by column name, ordered by created_at DESC.
    """
    con = _connect(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if workspace is not None:
            clauses.append("workspace = ?")
            params.append(workspace)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if brief_id is not None:
            clauses.append("brief_id = ?")
            params.append(brief_id)
        if task_status is not None:
            clauses.append("task_status = ?")
            params.append(task_status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = con.execute(
            f"SELECT * FROM agent_contract_handoffs {where} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: insert_episode / insert_episode_anomaly
# ---------------------------------------------------------------------------
#
# Brief: episodic-workflow-to-db (T4).
#
# Episodes are the persistence target for SubagentStop telemetry: every agent
# turn produces one row in the ``episodes`` table plus zero or more child rows
# in ``episode_anomalies``. The legacy filesystem writers
# (.claude/project-context/episodic-memory/episodes.jsonl + per-episode JSON
# files; .claude/project-context/workflow-episodic-memory/run-snapshots.jsonl;
# anomalies.jsonl) are superseded by these DB writers.
#
# No agent_permissions enforcement: episodes are system-internal telemetry
# written exclusively by the SubagentStop hook chain (not by named subagents).
# This mirrors the approach used for ``approval_grants`` and
# ``agent_contract_handoffs`` -- they are infrastructure tables, not surfaces
# the per-agent permission matrix governs.
# ---------------------------------------------------------------------------

# Columns the episodes table accepts as direct INSERT targets. The schema
# also exposes ``rowid`` (implicit), but no field outside this whitelist is
# allowed at the writer boundary -- this keeps the contract explicit and
# stops accidental drift from the schema definition.
_EPISODE_COLUMNS = (
    "episode_id",
    "workspace",
    "timestamp",
    "session_id",
    "task_id",
    "agent",
    "tier",
    "type",
    "title",
    "prompt",
    "enriched_prompt",
    "wf_prompt",
    "clarifications",
    "keywords",
    "tags",
    "commands_executed",
    "context_metrics",
    "relevance_score",
    "outcome",
    "duration_seconds",
    "exit_code",
    "plan_status",
    "output_length",
    "output_tokens_approx",
)


def insert_episode(
    workspace: str,
    episode_id: str,
    fields: Mapping[str, Any],
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert a row into the ``episodes`` table.

    Called from ``EpisodicMemory.store_episode()`` (T4 of brief
    episodic-workflow-to-db). The caller is the SubagentStop hook chain,
    which has no GAIA_DISPATCH_AGENT set -- there is no per-agent permission
    check.

    JSON-shaped columns (``clarifications``, ``keywords``, ``tags``,
    ``commands_executed``, ``context_metrics``) accept either a Python
    container -- which the writer serializes via ``json.dumps`` -- or a
    pre-serialized string. ``None`` is preserved as SQL NULL.

    Args:
        workspace: Workspace name (FK -> workspaces.name). Required.
        episode_id: PK for the new row. Required.
        fields: Dict of column -> value pairs. Recognized keys are the
            columns in ``_EPISODE_COLUMNS`` minus ``workspace`` and
            ``episode_id``. Unknown keys are silently dropped (callers may
            pass workflow-shaped dicts that contain telemetry fields that
            do not map to columns).
        db_path: Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "episode_id": <id>}`` on success.
        ``{"status": "error", "reason": str}`` on failure.
    """
    if not workspace or not workspace.strip():
        return {"status": "error", "reason": "workspace required"}
    if not episode_id or not episode_id.strip():
        return {"status": "error", "reason": "episode_id required"}

    # Normalize: serialize JSON-shaped values, preserve scalars and NULLs.
    json_cols = {
        "clarifications",
        "keywords",
        "tags",
        "commands_executed",
        "context_metrics",
    }
    data: dict[str, Any] = {"workspace": workspace, "episode_id": episode_id}
    for col in _EPISODE_COLUMNS:
        if col in ("workspace", "episode_id"):
            continue
        if col not in fields:
            continue
        val = fields[col]
        if val is None:
            data[col] = None
            continue
        if col in json_cols and not isinstance(val, str):
            data[col] = _json.dumps(val)
        else:
            data[col] = val

    # timestamp defaults to now() when caller did not supply one.
    if "timestamp" not in data or data["timestamp"] is None:
        data["timestamp"] = _now_iso()

    cols = list(data.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)
            con.execute(
                f"INSERT INTO episodes ({col_sql}) VALUES ({placeholders})",
                tuple(data[c] for c in cols),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied({"episode_id": episode_id})
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()


_EPISODE_ANOMALY_COLUMNS = (
    "episode_id",
    "workspace",
    "timestamp",
    "type",
    "severity",
    "message",
    "payload",
)


def insert_episode_anomaly(
    workspace: str,
    episode_id: str,
    fields: Mapping[str, Any],
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert a row into the ``episode_anomalies`` child table.

    Args:
        workspace: Workspace name (denormalized; matches parent episode).
        episode_id: FK -> episodes.episode_id. The parent row must already
            exist (FK ON DELETE CASCADE).
        fields: Dict with optional keys ``timestamp``, ``type``, ``severity``,
            ``message``, ``payload``. ``payload`` is JSON-serialized when it
            is not already a string.
        db_path: Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied"}`` on success or ``{"status": "error",
        "reason": str}`` on failure.
    """
    if not workspace or not workspace.strip():
        return {"status": "error", "reason": "workspace required"}
    if not episode_id or not episode_id.strip():
        return {"status": "error", "reason": "episode_id required"}

    anomaly_type = fields.get("type")
    if not anomaly_type or not str(anomaly_type).strip():
        return {"status": "error", "reason": "type required"}

    payload = fields.get("payload")
    if payload is not None and not isinstance(payload, str):
        payload = _json.dumps(payload)

    ts = fields.get("timestamp") or _now_iso()
    severity = fields.get("severity")
    message = fields.get("message")

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute(
                """
                INSERT INTO episode_anomalies
                    (episode_id, workspace, timestamp, type, severity,
                     message, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (episode_id, workspace, ts, anomaly_type, severity,
                 message, payload),
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        return _applied()
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    finally:
        con.close()
