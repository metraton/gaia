"""
gaia.briefs.store -- DB operations for briefs / plans / dependencies.

Layered atop gaia.store.writer._connect (B1). Reuses the `~/.gaia/gaia.db`
substrate; tables `briefs`, `acceptance_criteria`, `milestones`,
`brief_dependencies`, `plans`, `tasks`, plus `briefs_fts` (FTS5 mirror).

This module does NOT consult the ``agent_permissions`` DB table (which gates
*scanner-owned* tables like apps/projects and has no human-CLI escape hatch).
Brief content is user-driven (``gaia brief`` from the user's terminal) or
orchestrator-authored, so ``upsert_brief`` instead consults the env-var
dispatch-identity content guard (``_assert_dispatch_can_write_content`` in
``gaia.state.permissions``): a human/orchestrator-main-session call (no
``GAIA_DISPATCH_AGENT``) is always allowed, while a dispatched subagent that is
not an authorized author is blocked. This mirrors the memory/evidence/state
guards -- same fail-open-on-unset contract -- rather than the table-based model.

Public API::

    upsert_brief(workspace, name, fields, *, db_path=None) -> dict
    list_briefs(workspace, *, status=None, db_path=None) -> list[dict]
    get_brief(workspace, name, *, db_path=None) -> dict | None
    close_brief(workspace, name, *, db_path=None) -> bool
    get_dependencies(workspace, name, *, db_path=None) -> list[dict]
    search_briefs(workspace, query, *, limit=10, db_path=None) -> list[dict]
    delete_brief(workspace, name, *, db_path=None) -> bool
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from gaia.briefs.serializer import (
    parse_brief_markdown,
    serialize_brief_to_markdown,
)
from gaia.store.writer import _connect, _ensure_workspace_row


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_FTS_SAFE = re.compile(r"^[A-Za-z0-9_*\s\"]+$")


def _prepare_fts_query(query: str) -> str:
    """Return an FTS5-safe MATCH expression.

    FTS5 treats characters such as ``-``, ``:``, ``(``, ``)`` as operators
    or column qualifiers; an unquoted ``foo-bar`` raises ``no such column:
    bar``. To keep callers' lives easy we quote the entire query as a phrase
    when it contains anything other than alphanumerics, underscores, ``*``,
    spaces, and quotes.
    """
    q = (query or "").strip()
    if not q:
        return q
    if _FTS_SAFE.match(q):
        return q
    # Escape inner double quotes by doubling (FTS5 phrase-quoting rule)
    return '"' + q.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BRIEF_COLUMNS = (
    "status", "surface_type", "title", "objective", "context",
    "approach", "out_of_scope", "topic_key",
)


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# upsert_brief
# ---------------------------------------------------------------------------

def upsert_brief(
    workspace: str,
    name: str,
    fields: Mapping[str, Any],
    *,
    db_path: Path | None = None,
) -> dict:
    """Insert or update a brief row and its child rows (ACs, milestones, deps).

    Args:
        workspace: workspace identity (workspaces.name).
        name: bare brief name (no prefix).
        fields: dict matching the parse_brief_markdown shape; recognized keys:
            ``status``, ``surface_type``, ``topic_key``, ``title``,
            ``objective``, ``context``, ``approach``, ``out_of_scope``,
            ``acceptance_criteria``, ``milestones``, ``dependencies``.
        db_path: optional explicit DB path (tests).

    Returns:
        ``{"status": "applied", "brief_id": int, "acs": int, "milestones": int}``.

    Raises:
        ContentWriteForbidden: when GAIA_DISPATCH_AGENT names a dispatched agent
            that is not authorized to author brief content (brief content is
            authored by the orchestrator). A human CLI call / orchestrator main
            session (no dispatch identity) is always allowed.
    """
    from gaia.state.permissions import _assert_dispatch_can_write_content
    _assert_dispatch_can_write_content("briefs")

    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_workspace_row(con, workspace)

            existing = con.execute(
                "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
                (workspace, name),
            ).fetchone()

            now = _now_iso()
            data = {col: fields.get(col) for col in _BRIEF_COLUMNS}
            # Normalize status to a non-null default
            if not data.get("status"):
                data["status"] = "draft"

            if existing is None:
                con.execute(
                    """
                    INSERT INTO briefs (workspace, name, status, surface_type, title,
                                        objective, context, approach, out_of_scope,
                                        topic_key, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace, name,
                        data["status"], data["surface_type"], data["title"],
                        data["objective"], data["context"], data["approach"],
                        data["out_of_scope"], data["topic_key"], now, now,
                    ),
                )
                brief_id = con.execute(
                    "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
                    (workspace, name),
                ).fetchone()["id"]
            else:
                brief_id = existing["id"]
                con.execute(
                    """
                    UPDATE briefs SET
                        status = ?, surface_type = ?, title = ?,
                        objective = ?, context = ?, approach = ?,
                        out_of_scope = ?, topic_key = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        data["status"], data["surface_type"], data["title"],
                        data["objective"], data["context"], data["approach"],
                        data["out_of_scope"], data["topic_key"], now, brief_id,
                    ),
                )

            # Replace ACs and milestones (full sync semantics)
            con.execute("DELETE FROM acceptance_criteria WHERE brief_id = ?", (brief_id,))
            ac_count = 0
            for ac in fields.get("acceptance_criteria") or []:
                shape = ac.get("evidence_shape")
                if isinstance(shape, (dict, list)):
                    shape = json.dumps(shape, sort_keys=True)
                con.execute(
                    """
                    INSERT INTO acceptance_criteria
                        (brief_id, ac_id, description, evidence_type,
                         evidence_shape, artifact_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        brief_id,
                        ac.get("ac_id", ""),
                        ac.get("description", ""),
                        ac.get("evidence_type"),
                        shape,
                        ac.get("artifact_path"),
                    ),
                )
                ac_count += 1

            con.execute("DELETE FROM milestones WHERE brief_id = ?", (brief_id,))
            ms_count = 0
            for idx, m in enumerate(fields.get("milestones") or [], start=1):
                con.execute(
                    """
                    INSERT INTO milestones (brief_id, order_num, name, description)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        brief_id, idx,
                        m.get("name", f"M{idx}"),
                        m.get("description", ""),
                    ),
                )
                ms_count += 1

            # Dependencies: replace edges originating at this brief
            con.execute(
                "DELETE FROM brief_dependencies WHERE brief_id = ?",
                (brief_id,),
            )
            for dep_name in fields.get("dependencies") or []:
                target = con.execute(
                    "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
                    (workspace, dep_name),
                ).fetchone()
                if target is None:
                    # Skip dangling deps (target not yet imported)
                    continue
                con.execute(
                    """
                    INSERT OR IGNORE INTO brief_dependencies (brief_id, depends_on_id)
                    VALUES (?, ?)
                    """,
                    (brief_id, target["id"]),
                )

            con.commit()
        except Exception:
            con.rollback()
            raise

        return {
            "status": "applied",
            "brief_id": brief_id,
            "acs": ac_count,
            "milestones": ms_count,
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# list_briefs
# ---------------------------------------------------------------------------

def list_briefs(
    workspace: str,
    *,
    status: str | None = None,
    db_path: Path | None = None,
) -> list[dict]:
    """Return briefs for a workspace, optionally filtered by status."""
    con = _connect(db_path)
    try:
        if status is None:
            rows = con.execute(
                "SELECT id, name, status, surface_type, title, updated_at "
                "FROM briefs WHERE workspace = ? ORDER BY name",
                (workspace,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, name, status, surface_type, title, updated_at "
                "FROM briefs WHERE workspace = ? AND status = ? ORDER BY name",
                (workspace, status),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# get_brief
# ---------------------------------------------------------------------------

def get_brief_by_id(
    brief_id: int,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Return the full brief dict by numeric primary key, or None.

    Like :func:`get_brief` but resolves by ``id`` instead of
    ``(workspace, name)``. Used by ``gaia brief show <int>`` so users can
    look up a brief by its DB id without knowing which workspace it lives in.
    """
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM briefs WHERE id = ?",
            (brief_id,),
        ).fetchone()
        if row is None:
            return None

        brief: dict[str, Any] = dict(row)
        brief.pop("workspace", None)

        ac_rows = con.execute(
            "SELECT ac_id, description, evidence_type, evidence_shape, artifact_path "
            "FROM acceptance_criteria WHERE brief_id = ? ORDER BY id",
            (brief["id"],),
        ).fetchall()
        acs: list[dict] = []
        for ar in ac_rows:
            shape = ar["evidence_shape"]
            if shape:
                try:
                    shape = json.loads(shape)
                except Exception:
                    pass
            acs.append({
                "ac_id": ar["ac_id"],
                "description": ar["description"],
                "evidence_type": ar["evidence_type"],
                "evidence_shape": shape,
                "artifact_path": ar["artifact_path"],
            })
        brief["acceptance_criteria"] = acs

        ms_rows = con.execute(
            "SELECT order_num, name, description FROM milestones "
            "WHERE brief_id = ? ORDER BY order_num",
            (brief["id"],),
        ).fetchall()
        brief["milestones"] = [
            {"order_num": m["order_num"], "name": m["name"],
             "description": m["description"]}
            for m in ms_rows
        ]

        dep_rows = con.execute(
            "SELECT b2.name FROM brief_dependencies bd "
            "JOIN briefs b2 ON b2.id = bd.depends_on_id "
            "WHERE bd.brief_id = ? ORDER BY b2.name",
            (brief["id"],),
        ).fetchall()
        brief["dependencies"] = [r["name"] for r in dep_rows]

        return brief
    finally:
        con.close()


def find_brief_workspaces(
    name: str,
    *,
    db_path: Path | None = None,
) -> list[str]:
    """Return all workspace names that contain a brief with the given slug.

    Used by ``gaia brief show`` to emit a helpful cross-workspace hint when
    a brief is not found in the resolved workspace.  Returns an empty list
    when no brief with that name exists anywhere.
    """
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT workspace FROM briefs WHERE name = ? ORDER BY workspace",
            (name,),
        ).fetchall()
        return [r["workspace"] for r in rows]
    finally:
        con.close()


def get_brief(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> dict | None:
    """Return the full brief dict (incl. ACs, milestones, deps) or None."""
    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT * FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            return None

        brief: dict[str, Any] = dict(row)
        brief.pop("workspace", None)

        ac_rows = con.execute(
            "SELECT ac_id, description, evidence_type, evidence_shape, artifact_path "
            "FROM acceptance_criteria WHERE brief_id = ? ORDER BY id",
            (brief["id"],),
        ).fetchall()
        acs: list[dict] = []
        for ar in ac_rows:
            shape = ar["evidence_shape"]
            if shape:
                try:
                    shape = json.loads(shape)
                except Exception:
                    pass
            acs.append({
                "ac_id": ar["ac_id"],
                "description": ar["description"],
                "evidence_type": ar["evidence_type"],
                "evidence_shape": shape,
                "artifact_path": ar["artifact_path"],
            })
        brief["acceptance_criteria"] = acs

        ms_rows = con.execute(
            "SELECT order_num, name, description FROM milestones "
            "WHERE brief_id = ? ORDER BY order_num",
            (brief["id"],),
        ).fetchall()
        brief["milestones"] = [
            {"order_num": m["order_num"], "name": m["name"], "description": m["description"]}
            for m in ms_rows
        ]

        dep_rows = con.execute(
            "SELECT b2.name FROM brief_dependencies bd "
            "JOIN briefs b2 ON b2.id = bd.depends_on_id "
            "WHERE bd.brief_id = ? ORDER BY b2.name",
            (brief["id"],),
        ).fetchall()
        brief["dependencies"] = [r["name"] for r in dep_rows]

        return brief
    finally:
        con.close()


# ---------------------------------------------------------------------------
# close_brief
# ---------------------------------------------------------------------------

def close_brief(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> bool:
    """Set the brief status to 'closed' and update updated_at."""
    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE briefs SET status = 'closed', updated_at = ? "
            "WHERE workspace = ? AND name = ?",
            (_now_iso(), workspace, name),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# set_status_brief (validated transitions for the state machine)
# ---------------------------------------------------------------------------

# Legal transitions for the brief state machine (Opción A — DB canónica).
# The full enum is draft|open|in-progress|closed|archived. ``deprecated`` is
# intentionally NOT yet a valid status; it is reserved for the upcoming
# state-machines-cli brief.
_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    # draft -> closed/archived: real-world shortcut for briefs implemented
    # directly without an explicit planning intermediate (i.e. the work
    # was small enough to skip open/in-progress).
    "draft": {"open", "closed", "archived"},
    "open": {"in-progress"},
    "in-progress": {"closed"},
    "closed": {"archived", "open"},  # archived (normal flow) or reopened
    "archived": set(),
}

# Statuses recognized by the enum. set_status_brief rejects anything else.
# Single source of truth: ``gaia.state.VALID_BRIEF_STATUSES``. Re-exported here
# for backward compatibility; callers that import ``VALID_STATUSES`` from this
# module continue to work without changes.
from gaia.state import VALID_BRIEF_STATUSES as VALID_STATUSES  # noqa: E402


def set_status_brief(
    workspace: str,
    name: str,
    new_status: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Transition a brief's ``status`` after validating the move is legal.

    Returns ``{"old_status": str, "new_status": str, "name": str}`` on success.

    Raises:
        ValueError: when the brief does not exist, ``new_status`` is not in
            :data:`VALID_STATUSES`, or the transition from current to new is
            not in :data:`_LEGAL_TRANSITIONS`.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("briefs")

    if new_status not in VALID_STATUSES:
        raise ValueError(
            f"invalid status '{new_status}'; must be one of {list(VALID_STATUSES)}"
        )

    con = _connect(db_path)
    try:
        row = con.execute(
            "SELECT id, status FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"brief '{name}' not found in workspace '{workspace}'"
            )

        old_status = row["status"] or "draft"

        if old_status == new_status:
            # No-op; treat as legal idempotent transition.
            return {
                "name": name,
                "old_status": old_status,
                "new_status": new_status,
                "action": "noop",
            }

        allowed = _LEGAL_TRANSITIONS.get(old_status, set())
        if new_status not in allowed:
            raise ValueError(
                f"illegal transition '{old_status}' -> '{new_status}' for "
                f"brief '{name}'; allowed from '{old_status}': "
                f"{sorted(allowed) or '(none)'}"
            )

        con.execute(
            "UPDATE briefs SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now_iso(), row["id"]),
        )
        con.commit()
        return {
            "name": name,
            "old_status": old_status,
            "new_status": new_status,
            "action": "updated",
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# delete_brief (used by tests; not exposed via CLI)
# ---------------------------------------------------------------------------

def delete_brief(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> bool:
    con = _connect(db_path)
    try:
        cur = con.execute(
            "DELETE FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        )
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


# ---------------------------------------------------------------------------
# get_dependencies (recursive, depth-limited)
# ---------------------------------------------------------------------------

def get_dependencies(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
    max_depth: int = 32,
) -> list[dict]:
    """Return a list of {name, depth} representing the transitive closure
    of dependencies for the given brief.
    """
    con = _connect(db_path)
    try:
        root = con.execute(
            "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if root is None:
            return []

        result: list[dict] = []
        seen: set[int] = set()
        frontier: list[tuple[int, int]] = [(root["id"], 0)]
        while frontier:
            current_id, depth = frontier.pop(0)
            if depth >= max_depth:
                continue
            rows = con.execute(
                "SELECT depends_on_id FROM brief_dependencies WHERE brief_id = ?",
                (current_id,),
            ).fetchall()
            for r in rows:
                dep_id = r["depends_on_id"]
                if dep_id in seen:
                    continue
                seen.add(dep_id)
                dep_row = con.execute(
                    "SELECT name FROM briefs WHERE id = ?",
                    (dep_id,),
                ).fetchone()
                if dep_row is None:
                    continue
                result.append({"name": dep_row["name"], "depth": depth + 1})
                frontier.append((dep_id, depth + 1))

        return result
    finally:
        con.close()


# ---------------------------------------------------------------------------
# search_briefs (FTS5)
# ---------------------------------------------------------------------------

def search_briefs(
    workspace: str,
    query: str,
    *,
    limit: int = 10,
    db_path: Path | None = None,
) -> list[dict]:
    """Run FTS5 MATCH against briefs_fts and join with the briefs table.

    Filters by ``workspace = workspace``; ranks by bm25.

    The query is quoted as a single FTS5 phrase iff it contains characters
    FTS5 treats as syntax (hyphen, colon, parens, etc). Multi-word queries
    are passed through unmodified to keep boolean syntax usable.
    """
    fts_query = _prepare_fts_query(query)
    con = _connect(db_path)
    try:
        rows = con.execute(
            """
            SELECT b.name, b.title, b.status,
                   snippet(briefs_fts, -1, '[', ']', '...', 16) AS snippet,
                   bm25(briefs_fts) AS rank
            FROM briefs_fts
            JOIN briefs b ON b.id = briefs_fts.rowid
            WHERE briefs_fts MATCH ?
              AND b.workspace = ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, workspace, limit),
        ).fetchall()
        return [
            {
                "name": r["name"],
                "title": r["title"],
                "status": r["status"],
                "snippet": r["snippet"],
                "rank": r["rank"],
            }
            for r in rows
        ]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Granular AC mutation (v5 -- T5.1)
# ---------------------------------------------------------------------------
#
# These functions provide individual add/remove/edit semantics for
# acceptance_criteria rows, bypassing the full-sync `upsert_brief` path.
# Permission matrix D1: acceptance_criteria is `curator_only=False` --
# subagents are allowed to mutate. The guard is still invoked so that the
# enforcement layer can evolve without touching callers.


def _resolve_brief_id_local(con, workspace: str, brief_name: str) -> int:
    row = con.execute(
        "SELECT id FROM briefs WHERE workspace = ? AND name = ?",
        (workspace, brief_name),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"brief '{brief_name}' not found in workspace '{workspace}'"
        )
    return row["id"]


def add_ac(
    workspace: str,
    brief_name: str,
    ac_id: str,
    *,
    description: str | None = None,
    evidence_type: str | None = None,
    evidence_shape: Any = None,
    artifact_path: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Insert a new acceptance_criteria row.

    Raises ValueError on duplicate ac_id for the same brief or on missing brief.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("acceptance_criteria")

    if not ac_id or not ac_id.strip():
        raise ValueError("ac_id cannot be empty")

    shape = evidence_shape
    if isinstance(shape, (dict, list)):
        shape = json.dumps(shape, sort_keys=True)

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id_local(con, workspace, brief_name)
        existing = con.execute(
            "SELECT id FROM acceptance_criteria WHERE brief_id = ? AND ac_id = ?",
            (brief_id, ac_id),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"AC '{ac_id}' already exists in brief '{brief_name}'"
            )

        con.execute(
            "INSERT INTO acceptance_criteria "
            "(brief_id, ac_id, description, evidence_type, evidence_shape, "
            " artifact_path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (brief_id, ac_id, description or "", evidence_type, shape,
             artifact_path),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "brief_name": brief_name,
            "ac_id": ac_id,
        }
    finally:
        con.close()


def remove_ac(
    workspace: str,
    brief_name: str,
    ac_id: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete an acceptance_criteria row by (brief, ac_id).

    Raises ValueError if the brief or AC does not exist.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("acceptance_criteria")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id_local(con, workspace, brief_name)
        cur = con.execute(
            "DELETE FROM acceptance_criteria WHERE brief_id = ? AND ac_id = ?",
            (brief_id, ac_id),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"AC '{ac_id}' not found in brief '{brief_name}'"
            )
        con.commit()
        return {
            "status": "applied",
            "action": "deleted",
            "brief_name": brief_name,
            "ac_id": ac_id,
        }
    finally:
        con.close()


def update_ac(
    workspace: str,
    brief_name: str,
    ac_id: str,
    *,
    description: str | None = None,
    evidence_type: str | None = None,
    evidence_shape: Any = None,
    artifact_path: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Update fields of an existing acceptance_criteria row.

    None fields are not modified. At least one non-None field must be passed.
    Raises ValueError if the AC or brief does not exist.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("acceptance_criteria")

    updates: dict[str, Any] = {}
    if description is not None:
        updates["description"] = description
    if evidence_type is not None:
        updates["evidence_type"] = evidence_type
    if evidence_shape is not None:
        shape = evidence_shape
        if isinstance(shape, (dict, list)):
            shape = json.dumps(shape, sort_keys=True)
        updates["evidence_shape"] = shape
    if artifact_path is not None:
        updates["artifact_path"] = artifact_path

    if not updates:
        raise ValueError("at least one field must be specified for update")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id_local(con, workspace, brief_name)
        existing = con.execute(
            "SELECT id FROM acceptance_criteria WHERE brief_id = ? AND ac_id = ?",
            (brief_id, ac_id),
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"AC '{ac_id}' not found in brief '{brief_name}'"
            )

        set_clauses = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [existing["id"]]
        con.execute(
            f"UPDATE acceptance_criteria SET {set_clauses} WHERE id = ?",
            values,
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "ac_id": ac_id,
            "fields": list(updates.keys()),
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Granular milestone mutation (v5 -- T5.2)
# ---------------------------------------------------------------------------
#
# Milestones are `curator_only=True` per D1 -- only orchestrator/operator may
# add/remove/edit them. The guard rejects non-curator dispatches.


def add_milestone(
    workspace: str,
    brief_name: str,
    name: str,
    *,
    description: str | None = None,
    order_num: int | None = None,
    db_path: Path | None = None,
) -> dict:
    """Insert a new milestones row.

    If order_num is None, it auto-assigns to MAX(order_num)+1 for the brief.
    Raises ValueError on duplicate name within the brief or missing brief.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("milestones")

    if not name or not name.strip():
        raise ValueError("milestone name cannot be empty")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id_local(con, workspace, brief_name)
        existing = con.execute(
            "SELECT id FROM milestones WHERE brief_id = ? AND name = ?",
            (brief_id, name),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"milestone '{name}' already exists in brief '{brief_name}'"
            )

        if order_num is None:
            row = con.execute(
                "SELECT COALESCE(MAX(order_num), 0) AS max_o FROM milestones "
                "WHERE brief_id = ?",
                (brief_id,),
            ).fetchone()
            order_num = (row["max_o"] or 0) + 1

        con.execute(
            "INSERT INTO milestones (brief_id, order_num, name, description) "
            "VALUES (?, ?, ?, ?)",
            (brief_id, order_num, name, description or ""),
        )
        con.commit()
        return {
            "status": "applied",
            "action": "inserted",
            "brief_name": brief_name,
            "name": name,
            "order_num": order_num,
        }
    finally:
        con.close()


def remove_milestone(
    workspace: str,
    brief_name: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Delete a milestones row by (brief, name).

    Raises ValueError if the brief or milestone does not exist.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("milestones")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id_local(con, workspace, brief_name)
        cur = con.execute(
            "DELETE FROM milestones WHERE brief_id = ? AND name = ?",
            (brief_id, name),
        )
        if cur.rowcount == 0:
            raise ValueError(
                f"milestone '{name}' not found in brief '{brief_name}'"
            )
        con.commit()
        return {
            "status": "applied",
            "action": "deleted",
            "brief_name": brief_name,
            "name": name,
        }
    finally:
        con.close()


def update_milestone(
    workspace: str,
    brief_name: str,
    name: str,
    *,
    new_name: str | None = None,
    description: str | None = None,
    db_path: Path | None = None,
) -> dict:
    """Update name or description of an existing milestone.

    None fields are not modified. Raises ValueError if the milestone or
    brief does not exist, or if no field is specified.
    """
    from gaia.state.permissions import _assert_dispatch_can_advance_state
    _assert_dispatch_can_advance_state("milestones")

    updates: dict[str, Any] = {}
    if new_name is not None:
        if not new_name.strip():
            raise ValueError("new_name cannot be empty")
        updates["name"] = new_name
    if description is not None:
        updates["description"] = description

    if not updates:
        raise ValueError("at least one field must be specified for update")

    con = _connect(db_path)
    try:
        brief_id = _resolve_brief_id_local(con, workspace, brief_name)
        existing = con.execute(
            "SELECT id FROM milestones WHERE brief_id = ? AND name = ?",
            (brief_id, name),
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"milestone '{name}' not found in brief '{brief_name}'"
            )

        # If renaming, check for collision
        if "name" in updates and updates["name"] != name:
            collision = con.execute(
                "SELECT id FROM milestones WHERE brief_id = ? AND name = ?",
                (brief_id, updates["name"]),
            ).fetchone()
            if collision is not None:
                raise ValueError(
                    f"milestone '{updates['name']}' already exists in brief "
                    f"'{brief_name}'"
                )

        set_clauses = ", ".join(f"{k} = ?" for k in updates.keys())
        values = list(updates.values()) + [existing["id"]]
        con.execute(
            f"UPDATE milestones SET {set_clauses} WHERE id = ?",
            values,
        )
        con.commit()
        return {
            "status": "applied",
            "action": "updated",
            "brief_name": brief_name,
            "name": name,
            "fields": list(updates.keys()),
        }
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Brief invariant verification (v5 -- T5.6)
# ---------------------------------------------------------------------------


def verify_brief(
    workspace: str,
    name: str,
    *,
    db_path: Path | None = None,
) -> dict:
    """Run invariant checks on a brief and return a structured diagnosis.

    Returns dict with keys:
      * brief_name (str)
      * inconsistencies (list[dict]): each dict has {kind, detail}
      * pass (bool): True if inconsistencies is empty
    """
    con = _connect(db_path)
    try:
        brief_row = con.execute(
            "SELECT id, name, status FROM briefs WHERE workspace = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if brief_row is None:
            raise ValueError(
                f"brief '{name}' not found in workspace '{workspace}'"
            )
        brief_id = brief_row["id"]
        brief_status = brief_row["status"]

        inconsistencies: list[dict] = []

        # The TERMINAL set for an AC: an AC is "resolved" when it is either
        # satisfied ('done') or deliberately dropped ('descoped', v21). Any other
        # status ('pending', 'blocked') is non-terminal -- the AC is still live.
        # Adding 'descoped' to this set is what lets a brief close honestly
        # without leaving "false done" ACs (an AC that was dropped but had no
        # terminal status to record the drop).
        _AC_TERMINAL_STATUSES = ("done", "descoped")

        # Invariant 1: plans with zero tasks
        plan_row = con.execute(
            "SELECT id FROM plans WHERE brief_id = ?", (brief_id,)
        ).fetchone()
        plan_id = plan_row["id"] if plan_row else None

        if plan_id is not None:
            task_count = con.execute(
                "SELECT COUNT(*) AS c FROM tasks WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()["c"]
            if task_count == 0:
                inconsistencies.append({
                    "kind": "empty_plan",
                    "detail": f"plan for brief '{name}' has zero tasks",
                })

        # Invariant 2: tasks goal references an ac_id that does not exist on
        # the brief. Heuristic: scan task.goal for tokens like 'AC-<n>' and
        # confirm each one exists in acceptance_criteria(brief_id, ac_id).
        if plan_id is not None:
            ac_rows = con.execute(
                "SELECT ac_id FROM acceptance_criteria WHERE brief_id = ?",
                (brief_id,),
            ).fetchall()
            known_acs = {r["ac_id"] for r in ac_rows}

            ac_pattern = re.compile(r"\bAC-\d+\b")
            task_rows = con.execute(
                "SELECT order_num, goal FROM tasks WHERE plan_id = ?",
                (plan_id,),
            ).fetchall()
            for trow in task_rows:
                goal = trow["goal"] or ""
                for token in ac_pattern.findall(goal):
                    if token not in known_acs:
                        inconsistencies.append({
                            "kind": "orphan_task_ac_ref",
                            "detail": (
                                f"task order_num={trow['order_num']} references "
                                f"unknown AC '{token}'"
                            ),
                        })

        # Invariant 3: AC with status='done' but no artifact_path AND
        # evidence_type is set (the type promised evidence; the artifact is
        # missing).
        bad_acs = con.execute(
            "SELECT ac_id, evidence_type, artifact_path FROM acceptance_criteria "
            "WHERE brief_id = ? AND status = 'done' "
            "AND evidence_type IS NOT NULL "
            "AND (artifact_path IS NULL OR artifact_path = '')",
            (brief_id,),
        ).fetchall()
        for ac_row in bad_acs:
            inconsistencies.append({
                "kind": "done_ac_without_artifact",
                "detail": (
                    f"AC '{ac_row['ac_id']}' is status=done with "
                    f"evidence_type={ac_row['evidence_type']!r} but no "
                    f"artifact_path"
                ),
            })

        # Invariant 4: plan is 'active' but all tasks are 'done' -> should be closed
        if plan_id is not None:
            plan_status = con.execute(
                "SELECT status FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()["status"]
            if plan_status == "active":
                task_status_counts = con.execute(
                    "SELECT status, COUNT(*) AS c FROM tasks WHERE plan_id = ? "
                    "GROUP BY status",
                    (plan_id,),
                ).fetchall()
                statuses = {r["status"]: r["c"] for r in task_status_counts}
                total = sum(statuses.values())
                done_count = statuses.get("done", 0)
                if total > 0 and done_count == total:
                    inconsistencies.append({
                        "kind": "active_plan_all_tasks_done",
                        "detail": (
                            f"plan for brief '{name}' is 'active' but all "
                            f"{total} tasks are 'done' -- consider closing"
                        ),
                    })

        # Invariant 5: if a plan is closed, at least one agent_contract_handoffs
        # row with task_status='COMPLETE' must exist for this brief.
        # Catches plans manually forced-closed without any agent completing them.
        if plan_id is not None:
            plan_status_row = con.execute(
                "SELECT status FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if plan_status_row and plan_status_row["status"] == "closed":
                complete_count = con.execute(
                    "SELECT COUNT(*) AS c FROM agent_contract_handoffs "
                    "WHERE brief_id = ? AND task_status = 'COMPLETE'",
                    (brief_id,),
                ).fetchone()["c"]
                if complete_count == 0:
                    inconsistencies.append({
                        "kind": "closed_plan_without_completion_handoff",
                        "detail": (
                            f"plan for brief '{name}' is 'closed' but no "
                            f"agent_contract_handoffs row with task_status='COMPLETE' "
                            f"exists for this brief"
                        ),
                    })

        # Invariant 6: if the most recent agent_contract_handoffs row for this
        # brief has task_status != 'COMPLETE', the agent session ended without
        # completing. Surface as a stalled_handoff inconsistency.
        latest_handoff = con.execute(
            "SELECT id, agent_id, task_status, created_at "
            "FROM agent_contract_handoffs "
            "WHERE brief_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (brief_id,),
        ).fetchone()
        if latest_handoff is not None and latest_handoff["task_status"] != "COMPLETE":
            inconsistencies.append({
                "kind": "stalled_handoff",
                "detail": (
                    f"most recent handoff (agent_id='{latest_handoff['agent_id']}', "
                    f"created_at='{latest_handoff['created_at']}') has "
                    f"task_status='{latest_handoff['task_status']}' (not COMPLETE)"
                ),
            })

        # Invariant 7: a CLOSED brief with any AC in a NON-terminal status
        # (pending / blocked). Closing the brief while an AC is still live is the
        # "false done" gap: the AC was neither satisfied ('done') nor explicitly
        # dropped ('descoped'). Advisory only -- never blocks the close.
        if brief_status == "closed":
            nonterminal_acs = con.execute(
                "SELECT ac_id, status FROM acceptance_criteria "
                "WHERE brief_id = ? "
                "AND status NOT IN ({}) "
                "ORDER BY ac_id".format(
                    ", ".join("?" for _ in _AC_TERMINAL_STATUSES)
                ),
                (brief_id, *_AC_TERMINAL_STATUSES),
            ).fetchall()
            for ac_row in nonterminal_acs:
                inconsistencies.append({
                    "kind": "closed_brief_nonterminal_ac",
                    "detail": (
                        f"brief '{name}' is 'closed' but AC '{ac_row['ac_id']}' "
                        f"is status='{ac_row['status']}' (not terminal; terminal "
                        f"set is {{done, descoped}}) -- mark it 'done' or "
                        f"'descoped' to close honestly"
                    ),
                })

        # Invariant 8: a CLOSED brief whose plan is NOT closed. Advisory-only and
        # NO automatic cascade -- consistent with the existing advisory philosophy
        # (close never mutates plan/AC/milestone status; it only surfaces drift).
        if brief_status == "closed" and plan_id is not None:
            plan_state = con.execute(
                "SELECT status FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
            if plan_state is not None and plan_state["status"] != "closed":
                inconsistencies.append({
                    "kind": "closed_brief_open_plan",
                    "detail": (
                        f"brief '{name}' is 'closed' but its plan is "
                        f"status='{plan_state['status']}' (not 'closed') -- "
                        f"close the plan or reopen the brief (no automatic "
                        f"cascade is performed)"
                    ),
                })

        return {
            "brief_name": name,
            "inconsistencies": inconsistencies,
            "pass": len(inconsistencies) == 0,
        }
    finally:
        con.close()
