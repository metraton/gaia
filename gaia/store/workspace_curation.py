"""Curate the workspace registry without deleting history.

Four conditions leave rows nothing should list, and each has a remedy that
keeps every history row readable:

- a phantom: a workspace with no declared root that is not retired. With
  nothing it owns, it is marked ``retired`` and drops out of listings. With
  rows it owns, or history and a declared owner, it is retired into that
  owner (:mod:`gaia.store.workspace_retire`), whose alias keeps its history
  readable from the owner. Owned rows with no owner to take them are reported
  and left alone.
- a stale alias: a retire alias whose name is declared again, so the name
  still resolves to the retire target.
- an unevidenced integration: a row with neither version nor install path
  (``UNEVIDENCED_INTEGRATION_SQL``). Listings leave it out; it is kept.
- a dangling facet: a ``worktree`` or ``copy`` facet whose folder is gone.

:func:`find_conditions` reads all four for ``gaia doctor``;
:func:`plan_curation` reports exactly what :func:`apply_curation` writes.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

PATH_FACET_SCOPES = ("worktree", "copy")
RETIRED = "retired"
_INTEGRATIONS_ON_CONFLICT = {"integrations": "keep-target"}


def is_dangling_facet(scope: str, key: str) -> bool:
    """True when a path facet names a folder that is no longer on disk."""
    return scope in PATH_FACET_SCOPES and not Path(key).is_dir()


def _gaia_scopes() -> tuple[str, ...]:
    from gaia.store.writer import HOST_WORKSPACE, USER_WORKSPACE

    return (USER_WORKSPACE, HOST_WORKSPACE, "global")


def _count(con: sqlite3.Connection, table: str, where: str, params: tuple) -> int:
    try:
        return con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {where}', params).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def find_conditions(con: sqlite3.Connection) -> dict:
    """Every phantom, stale alias, unevidenced integration and dangling facet, read-only."""
    from gaia.store.writer import UNEVIDENCED_INTEGRATION_SQL

    scopes = _gaia_scopes()
    marks = ",".join("?" * len(scopes))
    phantoms = [
        r[0] for r in con.execute(
            "SELECT name FROM workspaces WHERE (root_path IS NULL OR root_path = '') "
            f"AND status != ? AND name NOT IN ({marks}) ORDER BY name",
            (RETIRED, *scopes),
        )
    ]
    stale_aliases = [
        (r[0], r[1]) for r in con.execute(
            "SELECT a.alias, a.target FROM workspace_aliases a "
            "JOIN workspaces w ON w.name = a.alias "
            "WHERE w.root_path IS NOT NULL AND w.root_path != '' ORDER BY a.alias"
        )
    ]
    unevidenced = [
        (r[0], r[1]) for r in con.execute(
            f"SELECT workspace, name FROM integrations WHERE {UNEVIDENCED_INTEGRATION_SQL} "
            "ORDER BY workspace, name"
        )
    ]
    facet_marks = ",".join("?" * len(PATH_FACET_SCOPES))
    dangling = [
        (r[0], r[1], r[2], r[3]) for r in con.execute(
            "SELECT workspace, project, scope, key FROM project_facets "
            f"WHERE scope IN ({facet_marks}) ORDER BY workspace, project, key",
            PATH_FACET_SCOPES,
        )
        if is_dangling_facet(r[2], r[3])
    ]
    return {
        "phantoms": phantoms,
        "stale_aliases": stale_aliases,
        "unevidenced_integrations": unevidenced,
        "dangling_facets": dangling,
    }


def _owned_rows(con: sqlite3.Connection, name: str) -> dict[str, int]:
    """Rows ``name`` owns that a retire would re-key, unevidenced integrations left out."""
    from gaia.store.workspace_retire import _OWNED_TABLES
    from gaia.store.writer import UNEVIDENCED_INTEGRATION_SQL

    counts = {}
    for table, _ in _OWNED_TABLES:
        where = "workspace = ?"
        if table == "integrations":
            where += f" AND NOT {UNEVIDENCED_INTEGRATION_SQL}"
        counts[table] = _count(con, table, where, (name,))
    counts["memory"] = _count(con, "memory", "workspace = ? AND deleted_at IS NULL", (name,))
    return {t: n for t, n in counts.items() if n}


def _history_rows(con: sqlite3.Connection, name: str) -> dict[str, int]:
    from gaia.store.workspace_retire import HISTORY_TABLES

    counts = {t: _count(con, t, "workspace = ?", (name,)) for t in HISTORY_TABLES}
    return {t: n for t, n in counts.items() if n}


def _owner(con: sqlite3.Connection, name: str, roots: dict[Path, str], into: dict[str, str]) -> tuple[str | None, str]:
    """The declared workspace that owns phantom ``name``, and how it was found."""
    from gaia.install_root import owning_root
    from gaia.store.workspace_retire import alias_target

    declared = set(roots.values())
    if name in into:
        return into[name], "--into"
    target = alias_target(con, name)
    if target != name and target in declared:
        return target, "retire alias"
    owners = set()
    for (path,) in con.execute(
        "SELECT path FROM projects WHERE workspace = ? AND path IS NOT NULL", (name,)
    ):
        root = owning_root(Path(path).resolve(), roots)
        owners.add(roots[root] if root else None)
    if len(owners) == 1 and None not in owners:
        return owners.pop(), "project paths"
    if Path(name).is_absolute():
        root = owning_root(Path(name).resolve(), roots)
        if root is not None:
            return roots[root], "name is a path"
    holders = {
        r[0] for r in con.execute(
            "SELECT DISTINCT workspace FROM projects WHERE name = ? AND workspace != ?",
            (name, name),
        )
        if r[0] in declared
    }
    if len(holders) == 1:
        return holders.pop(), "project of that name"
    return None, ""


def plan_curation(*, into: dict[str, str] | None = None, db_path: Path | None = None) -> dict:
    """Report, writing nothing, what :func:`apply_curation` would change.

    ``into`` maps a phantom to the declared workspace that owns it when the
    owner cannot be found from its alias, its projects' paths or its name.
    """
    from gaia.install_root import registered_roots
    from gaia.paths import db_path as _db_path
    from gaia.store.workspace_retire import WorkspaceRetireError, plan_retire
    from gaia.store.writer import _connect

    into = into or {}
    db_file = Path(db_path) if db_path is not None else _db_path()
    roots = registered_roots(db_file)
    con = _connect(db_file)
    try:
        found = find_conditions(con)
        hide, retire, unresolved = [], [], []
        for name in found["phantoms"]:
            owned = _owned_rows(con, name)
            history = _history_rows(con, name)
            owner, via = _owner(con, name, roots, into)
            if owner is not None and owner not in roots.values():
                unresolved.append({"workspace": name, "owned": owned, "history": history,
                                   "reason": f"{owner!r} is not a declared workspace"})
            elif owner is None and not owned:
                hide.append({"workspace": name, "history": history})
            elif owner is None:
                unresolved.append({"workspace": name, "owned": owned, "history": history,
                                   "reason": "no declared owner found; pass --into NAME=TARGET"})
            elif not owned and not history:
                hide.append({"workspace": name, "history": history})
            else:
                retire.append({"workspace": name, "into": owner, "via": via,
                               "owned": owned, "history": history})
    finally:
        con.close()

    for item in retire:
        try:
            report = plan_retire(item["workspace"], item["into"],
                                 on_conflict=_INTEGRATIONS_ON_CONFLICT, db_path=db_file)
        except WorkspaceRetireError as exc:
            item["refused"] = str(exc)
            continue
        dropped = sum(1 for f in found["dangling_facets"] if f[0] == item["workspace"])
        if dropped:
            report["tables"]["project_facets"]["move"] -= dropped
        item["tables"] = {t: c for t, c in report["tables"].items() if any(c.values())}
        if report["collisions"]:
            item["refused"] = "unresolved collisions: " + "; ".join(
                f"{t}: {', '.join(keys)}" for t, keys in report["collisions"].items()
            )

    plan = {
        "hide": hide,
        "retire": retire,
        "unresolved": unresolved,
        "drop_aliases": [{"alias": a, "target": t} for a, t in found["stale_aliases"]],
        "drop_facets": [
            {"workspace": w, "project": p, "scope": s, "key": k}
            for w, p, s, k in found["dangling_facets"]
        ],
        "hidden_integrations": len(found["unevidenced_integrations"]),
    }
    pending = plan["hide"] or plan["drop_aliases"] or plan["drop_facets"] or any(
        "refused" not in r for r in plan["retire"]
    )
    plan["mode"] = "dry-run" if pending else "noop"
    return plan


def apply_curation(*, into: dict[str, str] | None = None, db_path: Path | None = None) -> dict:
    """Apply :func:`plan_curation`; return its report with ``mode='applied'`` and the backup path.

    The database is backed up first. Each retire runs through
    :func:`gaia.store.workspace_retire.apply_retire`, so it writes its own
    backup and undo ledger; a retire the plan marks ``refused`` is skipped.
    """
    from gaia.paths import db_path as _db_path
    from gaia.store.workspace_retire import _backup, apply_retire
    from gaia.store.writer import _connect

    db_file = Path(db_path) if db_path is not None else _db_path()
    plan = plan_curation(into=into, db_path=db_file)
    if plan["mode"] == "noop":
        return plan

    con = _connect(db_file)
    try:
        plan["backup"] = str(_backup(con, db_file, "curate"))
    finally:
        con.close()

    # Facets are dropped before the retires, which would re-key them away from
    # the (workspace, project) the plan names.
    _write(db_file, [
        ("DELETE FROM workspace_aliases WHERE alias = ?", (alias["alias"],))
        for alias in plan["drop_aliases"]
    ] + [
        ("DELETE FROM project_facets "
         "WHERE workspace = ? AND project = ? AND scope = ? AND key = ?",
         (facet["workspace"], facet["project"], facet["scope"], facet["key"]))
        for facet in plan["drop_facets"]
    ])
    retired = [item for item in plan["retire"] if "refused" not in item]
    for item in retired:
        item["ledger"] = apply_retire(item["workspace"], item["into"],
                                      on_conflict=_INTEGRATIONS_ON_CONFLICT,
                                      db_path=db_file).get("ledger")
    # A retire applied before v66 left its source active, and re-running it is
    # a noop that never reaches the status.
    _write(db_file, [
        ("UPDATE workspaces SET status = ? WHERE name = ?", (RETIRED, item["workspace"]))
        for item in plan["hide"] + retired
    ])
    plan["mode"] = "applied"
    return plan


def _write(db_file: Path, statements: list[tuple[str, tuple]]) -> None:
    """Run ``statements`` in one transaction."""
    from gaia.store.writer import _connect

    con = _connect(db_file)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            for sql, params in statements:
                con.execute(sql, params)
            con.commit()
        except BaseException:
            con.rollback()
            raise
    finally:
        con.close()
