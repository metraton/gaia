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
# Public API: upsert_project
# ---------------------------------------------------------------------------

_PROJECT_FIELDS = ("role", "remote_url", "platform", "primary_language")


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
            ``role``, ``remote_url``, ``platform``, ``primary_language``.
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
            con.execute(
                """
                INSERT INTO projects (workspace, name, role, remote_url, platform,
                                      primary_language, scanner_ts, topic_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace, name) DO UPDATE SET
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
