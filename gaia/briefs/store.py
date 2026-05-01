"""
gaia.briefs.store -- DB operations for briefs / plans / dependencies.

Layered atop gaia.store.writer._connect (B1). Reuses the `~/.gaia/gaia.db`
substrate; tables `briefs`, `acceptance_criteria`, `milestones`,
`brief_dependencies`, `plans`, `tasks`, plus `briefs_fts` (FTS5 mirror).

This module does NOT consult ``agent_permissions``: brief authorship is a
user-driven CLI flow (``gaia brief`` from the user's terminal), not an
agent-driven mutation. That matches the design in B1 where agent_permissions
gates *agent-owned* tables (apps, repos) but leaves user-owned interactions
free.

Public API::

    upsert_brief(workspace, name, fields, *, db_path=None) -> dict
    list_briefs(workspace, *, status=None, db_path=None) -> list[dict]
    get_brief(workspace, name, *, db_path=None) -> dict | None
    close_brief(workspace, name, *, db_path=None) -> bool
    get_dependencies(workspace, name, *, db_path=None) -> list[dict]
    search_briefs(workspace, query, *, limit=10, db_path=None) -> list[dict]
    import_from_fs(source, *, workspace="me", db_path=None) -> dict
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
from gaia.store.writer import _connect, _ensure_project_row


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


def _strip_dir_prefix(dir_name: str) -> tuple[str, str | None]:
    """Strip the ``open_/closed_/in-progress_/archived_`` prefix from a directory
    name; return (bare_name, derived_status).

    derived_status is one of 'draft', 'in-progress', 'closed', 'archived', or
    None when no known prefix matched.
    """
    mapping = {
        "open_": "draft",
        "in-progress_": "in-progress",
        "closed_": "closed",
        "archived_": "archived",
    }
    for prefix, status in mapping.items():
        if dir_name.startswith(prefix):
            return dir_name[len(prefix):], status
    return dir_name, None


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
        workspace: workspace identity (projects.name).
        name: bare brief name (no prefix).
        fields: dict matching the parse_brief_markdown shape; recognized keys:
            ``status``, ``surface_type``, ``topic_key``, ``title``,
            ``objective``, ``context``, ``approach``, ``out_of_scope``,
            ``acceptance_criteria``, ``milestones``, ``dependencies``.
        db_path: optional explicit DB path (tests).

    Returns:
        ``{"status": "applied", "brief_id": int, "acs": int, "milestones": int}``.
    """
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            _ensure_project_row(con, workspace)

            existing = con.execute(
                "SELECT id FROM briefs WHERE project = ? AND name = ?",
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
                    INSERT INTO briefs (project, name, status, surface_type, title,
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
                    "SELECT id FROM briefs WHERE project = ? AND name = ?",
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
                    "SELECT id FROM briefs WHERE project = ? AND name = ?",
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
                "FROM briefs WHERE project = ? ORDER BY name",
                (workspace,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT id, name, status, surface_type, title, updated_at "
                "FROM briefs WHERE project = ? AND status = ? ORDER BY name",
                (workspace, status),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# get_brief
# ---------------------------------------------------------------------------

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
            "SELECT * FROM briefs WHERE project = ? AND name = ?",
            (workspace, name),
        ).fetchone()
        if row is None:
            return None

        brief: dict[str, Any] = dict(row)
        brief.pop("project", None)

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
            "WHERE project = ? AND name = ?",
            (_now_iso(), workspace, name),
        )
        con.commit()
        return cur.rowcount > 0
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
            "DELETE FROM briefs WHERE project = ? AND name = ?",
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
            "SELECT id FROM briefs WHERE project = ? AND name = ?",
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

    Filters by ``project = workspace``; ranks by bm25.

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
              AND b.project = ?
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
# import_from_fs
# ---------------------------------------------------------------------------

def import_from_fs(
    source: str | Path,
    *,
    workspace: str = "me",
    db_path: Path | None = None,
) -> dict:
    """Walk ``source`` for ``<status>_<name>/brief.md`` directories and upsert
    each into the DB. Status is derived from the directory prefix; falls back
    to frontmatter status if the prefix is unknown.

    Args:
        source: directory containing brief subdirectories.
        workspace: project identity to assign to each imported brief.
        db_path: optional explicit DB path (tests).

    Returns:
        ``{"imported": int, "errors": [...], "names": [str, ...]}``.
    """
    src = Path(source)
    if not src.is_dir():
        return {"imported": 0, "errors": [f"source not a directory: {src}"], "names": []}

    imported: list[dict] = []
    errors: list[dict] = []
    names: list[str] = []

    # Two passes: pass 1 imports all briefs (without dependencies wired up),
    # pass 2 re-applies dependencies once every brief has an id.
    parsed_entries: list[tuple[str, dict]] = []

    for entry in sorted(src.iterdir()):
        if not entry.is_dir():
            continue
        bare, derived_status = _strip_dir_prefix(entry.name)
        if derived_status is None and not (entry / "brief.md").exists():
            continue
        brief_file = entry / "brief.md"
        if not brief_file.exists():
            continue
        try:
            text = brief_file.read_text(encoding="utf-8")
            parsed = parse_brief_markdown(text)
        except Exception as exc:
            errors.append({"name": entry.name, "error": f"parse: {exc}"})
            continue

        # Override status from directory prefix if frontmatter didn't pin one
        # OR if directory prefix conflicts (directory wins by convention).
        if derived_status is not None:
            parsed["status"] = derived_status

        parsed_entries.append((bare, parsed))

    # Pass 1: upsert without deps
    for bare, parsed in parsed_entries:
        no_deps = dict(parsed)
        no_deps["dependencies"] = []
        try:
            upsert_brief(workspace, bare, no_deps, db_path=db_path)
            imported.append({"name": bare, "acs": len(parsed.get("acceptance_criteria") or []),
                             "milestones": len(parsed.get("milestones") or [])})
            names.append(bare)
        except Exception as exc:
            errors.append({"name": bare, "error": f"upsert: {exc}"})

    # Pass 2: re-apply deps now that all rows exist
    for bare, parsed in parsed_entries:
        if not parsed.get("dependencies"):
            continue
        try:
            # We already have everything except deps; do a thin update via re-upsert
            upsert_brief(workspace, bare, parsed, db_path=db_path)
        except Exception as exc:
            errors.append({"name": bare, "error": f"deps: {exc}"})

    return {
        "imported": len(imported),
        "errors": errors,
        "names": names,
        "details": imported,
    }
