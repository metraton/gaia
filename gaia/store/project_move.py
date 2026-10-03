"""Move one project between declared workspaces (``gaia project move``).

The project's row changes workspace, and what is the project's goes with it in
the same transaction: the scanner rows keyed to it, the briefs created for it
(``briefs.project``) and its entry in the workspace's ``project_identity``
contract, declared workflow included. Memory is never re-keyed: a project's
memory is read by its project key from every workspace
(``gaia.store.reader.pending_threads_by_project``). Workspace-level briefs and
every other contract stay where they are.

``gaia scan`` calls :func:`move_project` too, when the nearest declared
workspace of a repo is not the one its row is recorded under.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gaia.store.writer import _connect, _db_path, _project_child_fk_tables


class ProjectMoveError(ValueError):
    """A move refused before writing; ``candidates`` lists the matches of an ambiguous name."""

    def __init__(self, message: str, *, candidates: list[str] | None = None):
        super().__init__(message)
        self.candidates = candidates or []


_PROJECT_COLUMNS = "workspace, name, path, remote_url, project_identity, status"


def _find_project(con: sqlite3.Connection, project: str, from_workspace: str | None) -> sqlite3.Row:
    where = "(name = ? COLLATE NOCASE OR project_identity = ?)"
    params: list = [project, project]
    if from_workspace:
        where += " AND workspace = ?"
        params.append(from_workspace)
    rows = con.execute(
        f"SELECT {_PROJECT_COLUMNS} FROM projects WHERE {where} ORDER BY workspace, name",
        params,
    ).fetchall()
    scope = f" in workspace {from_workspace!r}" if from_workspace else ""
    if not rows:
        raise ProjectMoveError(f"no project {project!r}{scope}")
    if len(rows) > 1:
        candidates = [f"{r['workspace']}/{r['name']}" for r in rows]
        raise ProjectMoveError(
            f"project {project!r} is ambiguous{scope}: {', '.join(candidates)}. "
            f"Name its workspace with --from.",
            candidates=candidates,
        )
    return rows[0]


def _require_declared(workspace: str, db_path: Path | None) -> None:
    from gaia.install_root import registered_roots
    from gaia.project import DECLARE_COMMAND

    if workspace not in registered_roots(db_path or _db_path()).values():
        raise ProjectMoveError(
            f"workspace {workspace!r} is not declared.\nDeclare it with: {DECLARE_COMMAND}"
        )


def _free_name(con: sqlite3.Connection, workspace: str, name: str) -> str:
    """``name``, or ``name-2``, ``name-3``... when another row holds the slot.

    Any occupant counts, one without an identity included:
    ``writer._find_collision_free_name`` treats that one as the same repo so an
    upsert can adopt it, which a move cannot do without a primary-key clash.
    """
    candidate, suffix = name, 2
    while con.execute(
        "SELECT 1 FROM projects WHERE workspace = ? AND name = ?", (workspace, candidate)
    ).fetchone():
        candidate, suffix = f"{name}-{suffix}", suffix + 1
    return candidate


def _ficha_slug(payload: dict | None, row: sqlite3.Row) -> str | None:
    from gaia.identity_shape import classify_identity_shape
    from tools.scan.promote import _match_slug

    if classify_identity_shape(payload) != "map":
        return None
    return _match_slug(payload, {
        "path": row["path"], "remote_url": row["remote_url"], "name": row["name"],
    })


def _carry_ficha(con: sqlite3.Connection, row: sqlite3.Row, to_workspace: str) -> str | None:
    """Move the project's entry into the target contract, replacing a target entry for the same repo."""
    from gaia.identity_shape import WORKSPACE_META_KEY, classify_identity_shape
    from tools.scan.promote import (
        _new_slug, _select_identity_payload, _upsert_identity_payload,
    )

    source = _select_identity_payload(con, row["workspace"])
    slug = _ficha_slug(source, row)
    if slug is None:
        return None
    entry = source.pop(slug)

    target = _select_identity_payload(con, to_workspace) or {}
    if classify_identity_shape(target) not in ("map", "empty"):
        target = {WORKSPACE_META_KEY: target}
    target_slug = _ficha_slug(target, row)
    if target_slug is None:
        target_slug = _new_slug(entry.get("name") or row["name"], set(target))
    target[target_slug] = entry

    _upsert_identity_payload(con, row["workspace"], source)
    _upsert_identity_payload(con, to_workspace, target)
    return target_slug


def _plan(con: sqlite3.Connection, row: sqlite3.Row, to_workspace: str) -> dict:
    from tools.scan.promote import _select_identity_payload

    source, identity = row["workspace"], row["project_identity"]
    tables: dict[str, dict] = {"projects": {"moves": [row["name"]], "stays": []}}
    for table, ws_col, proj_col in _project_child_fk_tables(con):
        n = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {ws_col} = ? AND {proj_col} = ?",
            (source, row["name"]),
        ).fetchone()[0]
        if n:
            tables[table] = {"moves": n, "stays": 0}

    briefs = con.execute(
        "SELECT name, project FROM briefs WHERE workspace = ? ORDER BY name", (source,)
    ).fetchall()
    moving_briefs = [b["name"] for b in briefs if identity and b["project"] == identity]
    tables["briefs"] = {
        "moves": moving_briefs,
        "stays": [b["name"] for b in briefs if b["name"] not in moving_briefs],
    }

    slug = _ficha_slug(_select_identity_payload(con, source), row)
    replaced = _ficha_slug(_select_identity_payload(con, to_workspace), row) if slug else None
    contracts = [
        r[0] for r in con.execute(
            "SELECT contract_name FROM project_context_contracts WHERE workspace = ? "
            "ORDER BY contract_name", (source,),
        )
    ]
    tables["project_context_contracts"] = {
        "moves": [f"project_identity.{slug}"] if slug else [],
        "stays": contracts,
        "replaces": [f"project_identity.{replaced}"] if replaced else [],
    }

    memory = 0
    if identity:
        from gaia.store.writer import canonical_project_key
        key = canonical_project_key(identity)
        memory = sum(
            1 for r in con.execute(
                "SELECT project_ref, initiative FROM memory WHERE deleted_at IS NULL"
            )
            if canonical_project_key(r["project_ref"], r["initiative"]) == key
        )
    tables["memory"] = {"moves": 0, "stays": memory, "read_through_project": True}

    collisions = [
        r[0] for r in con.execute(
            f"SELECT name FROM briefs WHERE workspace = ? "
            f"AND name IN ({','.join('?' * len(moving_briefs))})",
            (to_workspace, *moving_briefs),
        )
    ] if moving_briefs else []

    name_in_target = _free_name(con, to_workspace, row["name"])
    return {
        "project": row["name"],
        "project_identity": identity,
        "from": source,
        "to": to_workspace,
        "name_in_target": name_in_target,
        "name_taken_in_target": row["name"] if name_in_target != row["name"] else None,
        "tables": tables,
        "collisions": {"briefs": collisions} if collisions else {},
    }


def move_project(
    project: str,
    to_workspace: str,
    *,
    from_workspace: str | None = None,
    dry_run: bool = False,
    db_path: Path | None = None,
) -> dict:
    """Move ``project`` (a name or a project_identity) into the declared ``to_workspace``.

    Returns the plan -- per table what moves and what stays -- with ``mode``
    ``dry-run`` or ``applied``. A dry-run writes nothing.

    Raises:
        ProjectMoveError: the target is not declared, the project is unknown,
            ambiguous or already there, or a brief that moves collides by name
            in the target. Nothing is written.
    """
    _require_declared(to_workspace, db_path)
    con = _connect(db_path)
    try:
        con.execute("BEGIN")
        try:
            row = _find_project(con, project, from_workspace)
            if row["workspace"] == to_workspace:
                raise ProjectMoveError(
                    f"project {row['name']!r} is already in workspace {to_workspace!r}"
                )
            plan = _plan(con, row, to_workspace)
            if plan["collisions"]:
                raise ProjectMoveError(
                    f"workspace {to_workspace!r} already has brief(s) "
                    f"{', '.join(plan['collisions']['briefs'])}; rename them before moving"
                )
            if dry_run:
                con.rollback()
                return {"mode": "dry-run", **plan}

            source, name, new_name = row["workspace"], row["name"], plan["name_in_target"]
            con.execute("PRAGMA defer_foreign_keys = ON")
            for table, ws_col, proj_col in _project_child_fk_tables(con):
                con.execute(
                    f"UPDATE {table} SET {ws_col} = ?, {proj_col} = ? "
                    f"WHERE {ws_col} = ? AND {proj_col} = ?",
                    (to_workspace, new_name, source, name),
                )
            con.execute(
                "UPDATE projects SET workspace = ?, name = ? WHERE workspace = ? AND name = ?",
                (to_workspace, new_name, source, name),
            )
            if plan["tables"]["briefs"]["moves"]:
                con.execute(
                    "UPDATE briefs SET workspace = ? WHERE workspace = ? AND project = ?",
                    (to_workspace, source, row["project_identity"]),
                )
            plan["ficha_slug"] = _carry_ficha(con, row, to_workspace)
            con.commit()
        except sqlite3.IntegrityError as exc:
            con.rollback()
            raise ProjectMoveError(
                f"moving {project!r} into {to_workspace!r} would break a key there "
                f"({exc}); nothing was written"
            ) from exc
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()
    return {"mode": "applied", **plan}
