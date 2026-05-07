"""
gaia.store.writer -- CRUD API for the Gaia SQLite substrate.

The writer is the only authorized path to mutate `~/.gaia/gaia.db`. Every
mutation consults `agent_permissions(table_name, agent_name, allow_write)`
before touching data. If the (table, agent) pair is missing or has
``allow_write=0``, the operation returns ``{"status": "rejected",
"reason": "not_authorized"}`` without modifying the DB.

Path resolution uses ``gaia.paths.db_path()`` (B0). The schema is created
on-demand on the first connection if the DB file does not yet exist.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram. See NOTICE.md.

Public API::

    upsert_repo(workspace, name, fields, agent, topic_key=None) -> dict
    upsert_app(workspace, repo, name, fields, agent, topic_key=None) -> dict
    delete_missing_in(table, workspace, surviving_keys) -> int
    bulk_upsert(table, workspace, rows, agent) -> dict
    wipe_project(workspace) -> None
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Schema file lives alongside this module
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Tables we recognize (whitelist for delete_missing_in / bulk_upsert)
_KNOWN_TABLES = {
    "projects",
    "repos",
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
# Identity normalization (mirror of gaia.project._normalize_remote, but
# applied at the store layer when populating projects.identity)
# ---------------------------------------------------------------------------

def _resolve_identity(workspace: str, workspace_path: Path | None = None) -> str:
    """Resolve workspace identity by inspecting the git remote of *workspace_path*.

    When ``workspace_path`` is provided, identity is derived from the git remote
    of that directory (via ``gaia.project.current(workspace_path)``). This
    ensures that multiple workspaces ingested in the same Python process each
    receive the correct identity instead of all collapsing to the cwd identity.

    Falls back to the workspace string itself (already lowercase by convention)
    when the path is absent or identity cannot be resolved.

    Args:
        workspace:      Workspace name used as the fallback.
        workspace_path: Directory whose git remote supplies the identity.
                        Defaults to the current working directory when None.
    """
    try:
        from gaia.project import current as _project_current
        ident = _project_current(cwd=workspace_path) if workspace_path is not None else _project_current()
        if ident and ident != "global":
            return ident
    except Exception:
        pass
    return workspace.lower()


def _ensure_project_row(
    con: sqlite3.Connection,
    workspace: str,
    workspace_path: Path | None = None,
) -> None:
    """Insert (or update) the projects row for a workspace.

    Identity is resolved from the git remote of ``workspace_path`` at insertion
    time. On a fresh row the identity is captured; for existing rows the
    identity is left intact (idempotent).

    Args:
        con:            Open SQLite connection.
        workspace:      Workspace name (projects.name PK).
        workspace_path: Directory whose git remote supplies the identity.
                        When None, falls back to cwd (legacy behaviour).
    """
    existing = con.execute(
        "SELECT name FROM projects WHERE name = ?",
        (workspace,),
    ).fetchone()
    if existing is not None:
        return
    identity = _resolve_identity(workspace, workspace_path)
    con.execute(
        "INSERT INTO projects (name, identity, created_at) VALUES (?, ?, ?)",
        (workspace, identity, _now_iso()),
    )


# ---------------------------------------------------------------------------
# Public API: upsert_repo
# ---------------------------------------------------------------------------

_REPO_FIELDS = ("role", "remote_url", "platform", "primary_language")


def upsert_repo(
    workspace: str,
    name: str,
    fields: Mapping[str, Any],
    agent: str,
    topic_key: str | None = None,
    *,
    db_path: Path | None = None,
    workspace_path: Path | None = None,
) -> dict:
    """Upsert a repos row, enforcing per-agent write permission.

    Args:
        workspace: Workspace identity (matches projects.name / repos.project).
        name: Repo name (basename).
        fields: Dict of column->value pairs. Recognized keys:
            ``role``, ``remote_url``, ``platform``, ``primary_language``.
        agent: Agent name. Must have allow_write=1 for table 'repos' in
            agent_permissions.
        topic_key: Optional dimension key.
        db_path: Optional explicit DB path (used by tests).
        workspace_path: Directory whose git remote supplies the projects.identity
            value. When provided, identity is resolved from this path instead of
            the process cwd. Pass ``repo_path`` from the scanner for correct
            multi-workspace ingestion.

    Returns:
        {"status": "applied"} on success.
        {"status": "rejected", "reason": "not_authorized"} if the agent lacks
        write permission for the 'repos' table.
    """
    con = _connect(db_path)
    try:
        if not _is_authorized(con, "repos", agent):
            return _rejected()
        con.execute("BEGIN")
        try:
            _ensure_project_row(con, workspace, workspace_path)
            data = {k: fields.get(k) for k in _REPO_FIELDS}
            con.execute(
                """
                INSERT INTO repos (project, name, role, remote_url, platform,
                                   primary_language, scanner_ts, topic_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, name) DO UPDATE SET
                    role = excluded.role,
                    remote_url = excluded.remote_url,
                    platform = excluded.platform,
                    primary_language = excluded.primary_language,
                    scanner_ts = excluded.scanner_ts,
                    topic_key = excluded.topic_key
                """,
                (
                    workspace, name,
                    data["role"], data["remote_url"], data["platform"],
                    data["primary_language"], _now_iso(), topic_key,
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
    repo: str,
    name: str,
    fields: Mapping[str, Any],
    agent: str,
    topic_key: str | None = None,
    *,
    db_path: Path | None = None,
) -> dict:
    """Upsert an apps row, enforcing per-agent write permission.

    Args:
        workspace: Workspace identity (matches apps.project).
        repo: Parent repo name (must reference a row in repos).
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
            _ensure_project_row(con, workspace)
            # Ensure parent repo row exists -- create a minimal stub if missing
            existing_repo = con.execute(
                "SELECT name FROM repos WHERE project = ? AND name = ?",
                (workspace, repo),
            ).fetchone()
            if existing_repo is None:
                con.execute(
                    "INSERT INTO repos (project, name, scanner_ts) VALUES (?, ?, ?)",
                    (workspace, repo, _now_iso()),
                )
            data = {k: fields.get(k) for k in _APP_FIELDS}
            con.execute(
                """
                INSERT INTO apps (project, repo, name, kind, description, status,
                                  topic_key, scanner_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, repo, name) DO UPDATE SET
                    kind = excluded.kind,
                    description = excluded.description,
                    status = excluded.status,
                    topic_key = excluded.topic_key,
                    scanner_ts = excluded.scanner_ts
                """,
                (
                    workspace, repo, name,
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
    """Delete rows from `table` (filtered by project=workspace) whose primary
    key is NOT in surviving_keys.

    Args:
        table: Target table name (must be in _KNOWN_TABLES).
        workspace: Workspace identity (project FK value).
        surviving_keys: Iterable of tuples representing the PK fragments to
            keep. For ``repos`` use ``[(name,), ...]``. For ``apps`` use
            ``[(repo, name), ...]``.
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
            if table == "repos" or table in {
                "libraries", "tf_live", "releases", "workloads", "clusters_defined"
            }:
                # PK = (project, name) for repos; (project, repo, name) for child tables
                pass
            # Simple approach: load all PK rows, compare in Python, delete the diff.
            # For repos: PK is (project, name). For apps: (project, repo, name). Etc.
            pk_columns = {
                "projects": ("name",),
                "repos": ("name",),
                "apps": ("repo", "name"),
                "libraries": ("repo", "name"),
                "services": ("repo", "name"),
                "features": ("repo", "name"),
                "tf_modules": ("repo", "name"),
                "tf_live": ("repo", "name"),
                "releases": ("repo", "name"),
                "workloads": ("repo", "name"),
                "clusters_defined": ("repo", "name"),
                "clusters": ("name",),
                "integrations": ("name",),
                "gaia_installations": ("machine",),
                "machines": ("name",),
            }[table]

            cols_sql = ", ".join(pk_columns)
            existing = con.execute(
                f"SELECT {cols_sql} FROM {table} WHERE project = ?",
                (workspace,),
            ).fetchall()
            existing_set = {tuple(row) for row in existing}
            surviving_set = {tuple(s) for s in surviving}
            to_delete = existing_set - surviving_set

            count = 0
            for key in to_delete:
                placeholders = " AND ".join(f"{c} = ?" for c in pk_columns)
                con.execute(
                    f"DELETE FROM {table} WHERE project = ? AND {placeholders}",
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

    Currently dispatches to upsert_repo / upsert_app for those tables. Other
    tables fall through to a generic dict-driven path (caller responsibility
    to provide all required columns).

    Returns:
        {"applied": int, "rejected": int}
    """
    rows_list = list(rows)
    applied = 0
    rejected = 0
    if table == "repos":
        for r in rows_list:
            res = upsert_repo(
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
                r["repo"],
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
    # updates the columns the caller provided. Columns not in `r.keys()` are
    # preserved (agent-owned columns survive scanner upserts).
    pk_columns = {
        "projects": ("name",),
        "repos": ("name",),
        "apps": ("repo", "name"),
        "libraries": ("repo", "name"),
        "services": ("repo", "name"),
        "features": ("repo", "name"),
        "tf_modules": ("repo", "name"),
        "tf_live": ("repo", "name"),
        "releases": ("repo", "name"),
        "workloads": ("repo", "name"),
        "clusters_defined": ("repo", "name"),
        "clusters": ("name",),
        "integrations": ("name",),
        "gaia_installations": ("machine",),
        "machines": ("name",),
    }
    if table not in pk_columns:
        raise ValueError(f"unknown table for bulk_upsert: {table!r}")
    pk = ("project", *pk_columns[table])

    con = _connect(db_path)
    try:
        if not _is_authorized(con, table, agent):
            return {"applied": 0, "rejected": len(rows_list)}
        con.execute("BEGIN")
        try:
            _ensure_project_row(con, workspace)
            for r in rows_list:
                cols = ["project"] + list(r.keys())
                vals = [workspace] + list(r.values())
                placeholders = ", ".join(["?"] * len(cols))
                # Build the ON CONFLICT DO UPDATE clause -- update only the
                # columns the caller passed, EXCLUDING the PK columns.
                update_cols = [c for c in r.keys() if c not in pk]
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
                    # No columns to update -- DO NOTHING preserves the row.
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

    This function is called exclusively by the subagent_stop hook to auto-
    capture install events detected in tool output. The hook runs as
    ``agent='system'`` -- a synthetic identity that is not registered in
    agent_permissions -- so permission enforcement is intentionally skipped
    here (the hook layer is the authorization boundary, not the store layer).

    Idempotency: when ``topic_key`` is supplied, a reinstall of the same tool
    produces the same ``topic_key``, which causes the ON CONFLICT clause to
    update the existing row instead of inserting a duplicate.

    Args:
        workspace:    Workspace identity (matches projects.name).
        name:         Integration name (e.g. "acli", "gcloud").
        kind:         Optional kind string (e.g. "cli", "pkg").
        version:      Optional version string.
        install_path: Optional install path.
        topic_key:    Optional dimension key for idempotent upserts
                      (e.g. "cli/atlassian/acli").
        agent:        Agent identifier (recorded for audit; not checked against
                      agent_permissions -- hook layer enforces authorization).
        db_path:      Optional explicit DB path (used by tests).

    Returns:
        {"status": "applied"} on success.
        {"status": "error", "reason": <str>} on failure.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_project_row(con, workspace)
            con.execute(
                """
                INSERT INTO integrations (project, name, kind, version,
                                          install_path, topic_key, scanner_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, name) DO UPDATE SET
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

VALID_MEMORY_TYPES = ("project", "user", "feedback")


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

    Memory rows are user-driven (``gaia memory add`` from the user's terminal),
    not agent-driven mutations -- so the per-agent ``agent_permissions`` gate is
    intentionally NOT consulted here, matching the design used by briefs (B8).

    On INSERT, ``updated_at`` is set to the current UTC ISO8601 timestamp.
    On UPDATE (PK = ``(project, name)``), the same timestamp is refreshed and
    ``description``, ``body``, ``type``, ``origin_session_id`` are overwritten
    with the supplied values. The FTS5 mirror (``memory_fts``) is kept in sync
    by the schema-defined triggers (``memory_ai``, ``memory_au``, ``memory_ad``).

    Args:
        workspace:         Workspace identity (matches projects.name; FK).
        name:              Memory slug (e.g. ``project_gaia_v5``). PK with
                           ``workspace``.
        type:              One of ``"project"``, ``"user"``, ``"feedback"``
                           (CHECK constraint in the schema).
        body:              Markdown body (without frontmatter). Required.
        description:       Optional one-line summary (mirrors the legacy
                           frontmatter ``description`` field).
        origin_session_id: Optional session identifier; defaults to
                           ``$GAIA_SESSION_ID`` when present, else ``NULL``.
        db_path:           Optional explicit DB path (used by tests).
        workspace_path:    Directory whose git remote supplies the
                           ``projects.identity`` value when the project row is
                           first created. Defaults to cwd.

    Returns:
        ``{"status": "applied", "action": "inserted" | "updated",
           "name": str, "updated_at": iso8601}``.

    Raises:
        ValueError: if ``type`` is not in ``VALID_MEMORY_TYPES`` or ``body``
                    is empty.
    """
    import os

    if type not in VALID_MEMORY_TYPES:
        raise ValueError(
            f"invalid memory type {type!r}; must be one of {list(VALID_MEMORY_TYPES)}"
        )
    if not body or not body.strip():
        raise ValueError("memory body cannot be empty")
    if not name or not name.strip():
        raise ValueError("memory name cannot be empty")

    if origin_session_id is None:
        origin_session_id = os.environ.get("GAIA_SESSION_ID") or None

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_project_row(con, workspace, workspace_path)

            existing = con.execute(
                "SELECT name FROM memory WHERE project = ? AND name = ?",
                (workspace, name),
            ).fetchone()
            action = "updated" if existing is not None else "inserted"

            now = _now_iso()
            con.execute(
                """
                INSERT INTO memory (project, name, type, description, body,
                                    origin_session_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project, name) DO UPDATE SET
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

# Whitelist of memory columns that can be patched via update_memory_field.
# Mirrors the schema (description, body) -- type changes go through delete+add.
_MEMORY_PATCHABLE_FIELDS = ("description", "body")


def delete_memory(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Hard-delete a curated memory row.

    The FTS5 mirror (``memory_fts``) is kept consistent automatically by the
    schema-defined ``memory_ad`` trigger.

    Args:
        workspace: Workspace identity (matches projects.name).
        name:      Memory slug (PK with workspace).
        db_path:   Optional explicit DB path (used by tests).

    Returns:
        ``True`` if a row was deleted, ``False`` if no row matched.
    """
    con = _connect(db_path)
    try:
        cur = con.execute(
            "DELETE FROM memory WHERE project = ? AND name = ?",
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
    """Patch a single column on a curated memory row.

    Args:
        workspace: Workspace identity.
        name:      Memory slug (PK with workspace).
        field:     Whitelisted column name (``description`` or ``body``).
        content:   New value (or content to append).
        append:    When True, concatenate with the existing value using a
                   ``\\n\\n`` separator instead of overwriting. If the existing
                   value is NULL/empty, the new content is written as-is.
        db_path:   Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "name": str, "field": str, "action":
        "appended" | "overwritten", "updated_at": iso8601}``.

    Raises:
        ValueError: when ``field`` is not whitelisted, the row does not
                    exist, or ``content`` is empty.
    """
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
            f"SELECT {field}, body FROM memory WHERE project = ? AND name = ?",
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

        # body must remain non-empty (NOT NULL constraint in schema)
        if field == "body" and not new_value.strip():
            raise ValueError("memory body cannot be empty")

        now = _now_iso()
        con.execute(
            f"UPDATE memory SET {field} = ?, updated_at = ? "
            "WHERE project = ? AND name = ?",
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
    """Run FTS5 MATCH against ``memory_fts`` and join with the ``memory`` table.

    Filters by ``project = workspace``; ranks by bm25.

    Args:
        workspace: Workspace identity.
        query:     Free-text query (auto-quoted when it contains FTS5 syntax).
        limit:     Maximum result count.
        db_path:   Optional explicit DB path (used by tests).

    Returns:
        List of ``{"name", "type", "description", "snippet", "rank"}`` dicts.
    """
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
              AND m.project = ?
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
# Public API: memory read helpers (get_memory / list_memory)
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
            "SELECT project, name, type, description, body, "
            "       origin_session_id, updated_at "
            "FROM memory WHERE project = ? AND name = ?",
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
                "FROM memory WHERE project = ? ORDER BY name",
                (workspace,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT name, type, description, updated_at "
                "FROM memory WHERE project = ? AND type = ? ORDER BY name",
                (workspace, type),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: brief field patch (DB-only; used by `gaia brief edit --headless`)
# ---------------------------------------------------------------------------

# Whitelist of brief columns that can be patched via update_brief_field.
# `status` is intentionally excluded -- use set_status_brief for that.
_BRIEF_PATCHABLE_FIELDS = (
    "objective",
    "context",
    "approach",
    "out_of_scope",
    "description",   # legacy alias for `objective`; mapped below
    "title",
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
    """Patch a single text column on a brief row.

    Args:
        workspace: Workspace identity.
        name:      Brief slug (PK with workspace).
        field:     One of ``objective``, ``context``, ``approach``,
                   ``out_of_scope``, ``description`` (alias for objective),
                   or ``title``.
        content:   New value (or value to append).
        append:    When True, concatenate using ``\\n\\n`` separator if the
                   field already has content; otherwise overwrite.
        db_path:   Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "name": str, "field": str, "action": ...,
           "updated_at": iso8601}``.

    Raises:
        ValueError: when ``field`` is not whitelisted, the brief does not
                    exist, or ``content`` is empty.
    """
    if field not in _BRIEF_PATCHABLE_FIELDS:
        raise ValueError(
            f"invalid brief field {field!r}; must be one of "
            f"{list(_BRIEF_PATCHABLE_FIELDS)}"
        )
    if content is None or content == "":
        raise ValueError("content cannot be empty")

    # `description` is treated as a friendly alias for `objective`.
    column = "objective" if field == "description" else field

    con = _connect(db_path)
    try:
        row = con.execute(
            f"SELECT id, {column} FROM briefs WHERE project = ? AND name = ?",
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
# Public API: plan CRUD (DB-only; used by `gaia plan ...` CLI)
# ---------------------------------------------------------------------------

VALID_PLAN_LIFECYCLE_STATUSES = ("draft", "active", "closed")


def _resolve_brief_id(
    con: sqlite3.Connection,
    workspace: str,
    brief_name: str,
) -> int | None:
    row = con.execute(
        "SELECT id FROM briefs WHERE project = ? AND name = ?",
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
    """Insert or update the plan attached to a brief.

    A plan row is uniquely identified by ``brief_id`` (the FK is UNIQUE in the
    schema). Calling ``upsert_plan`` with an existing ``(workspace,
    brief_name)`` updates the row in place; calling it with a new brief
    creates the row.

    Args:
        workspace:  Workspace identity.
        brief_name: Slug of the parent brief (must already exist).
        content:    Optional markdown body of the plan. ``None`` leaves the
                    existing content unchanged on update.
        status:     One of ``draft``, ``active``, ``closed`` (default
                    ``draft`` on insert, preserved on update if not changed).
        db_path:    Optional explicit DB path (used by tests).

    Returns:
        ``{"status": "applied", "action": "inserted" | "updated",
           "brief_name": str, "plan_id": int, "plan_status": str,
           "updated_at": iso8601}``.

    Raises:
        ValueError: brief not found or status invalid.
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
    """Return the plan row attached to a brief, or ``None`` when absent."""
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
    """List plans for a workspace, optionally filtered by brief and/or status."""
    con = _connect(db_path)
    try:
        sql = (
            "SELECT p.id, p.brief_id, p.status, p.created_at, p.updated_at, "
            "       b.name AS brief_name "
            "FROM plans p "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.project = ? "
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
    """Delete the plan attached to a brief (the brief itself is untouched)."""
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
    """Validated state-machine transition on a plan row.

    Uses :func:`gaia.state.transitions.assert_legal_plan_lifecycle` for the
    transition rules so the CLI and the substrate share a single source of
    truth.

    Returns:
        ``{"name": str, "old_status": str, "new_status": str, "action":
        "updated" | "noop"}``.

    Raises:
        ValueError: brief/plan not found, status invalid, or transition illegal.
    """
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
            }

        assert_legal_plan_lifecycle(old_status, new_status)

        con.execute(
            "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now_iso(), row["id"]),
        )
        con.commit()
        return {
            "brief_name": brief_name,
            "old_status": old_status,
            "new_status": new_status,
            "action": "updated",
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Public API: wipe_project
# ---------------------------------------------------------------------------

def wipe_project(workspace: str, *, db_path: Path | None = None) -> None:
    """Delete the projects row for `workspace`. FK CASCADE removes all
    child rows (repos, apps, integrations, etc.) automatically.

    Args:
        workspace: Workspace identity (projects.name value).
        db_path: Optional explicit DB path (used by tests).
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            con.execute("DELETE FROM projects WHERE name = ?", (workspace,))
            con.commit()
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()
