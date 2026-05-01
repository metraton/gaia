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
