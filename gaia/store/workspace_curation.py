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

import json
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


class WorkspaceCurationError(ValueError):
    """An ``--into`` names no phantom or no declared target; nothing was written."""


def _check_into(into: dict[str, str], phantoms: list[str], declared: set[str]) -> None:
    for name, target in into.items():
        if name not in phantoms:
            raise WorkspaceCurationError(f"--into {name}={target}: {name!r} is not a phantom workspace")
        if target not in declared:
            raise WorkspaceCurationError(f"--into {name}={target}: {target!r} is not a declared workspace")


def _curate(
    con: sqlite3.Connection, roots: dict[Path, str], into: dict[str, str], backup: Path,
) -> tuple[dict, list[tuple[dict, Path, dict]]]:
    """Write the whole curation inside ``con``'s open transaction.

    Each retire is planned against what the writes before it left, so two
    phantoms folding the same project or contract into one owner refuse the
    second. Returns the report and, per applied retire, its item, ledger path
    and unsaved ledger, which name ``backup``.
    """
    from gaia.store.workspace_retire import WorkspaceRetireError, _safe, retire_in_transaction

    con.execute("PRAGMA defer_foreign_keys = ON")
    found = find_conditions(con)
    _check_into(into, found["phantoms"], set(roots.values()))
    hide, retire, unresolved = [], [], []
    for name in found["phantoms"]:
        owned = _owned_rows(con, name)
        history = _history_rows(con, name)
        owner, via = _owner(con, name, roots, into)
        if owner is None and owned:
            unresolved.append({"workspace": name, "owned": owned, "history": history,
                               "reason": "no declared owner found; pass --into NAME=TARGET"})
        elif owner is None or not (owned or history):
            hide.append({"workspace": name, "history": history})
        else:
            retire.append({"workspace": name, "into": owner, "via": via,
                           "owned": owned, "history": history})

    # Facets go before the retires, which would re-key them away from the
    # (workspace, project) the report names.
    for alias, _ in found["stale_aliases"]:
        con.execute("DELETE FROM workspace_aliases WHERE alias = ?", (alias,))
    for facet in found["dangling_facets"]:
        con.execute(
            "DELETE FROM project_facets WHERE workspace = ? AND project = ? AND scope = ? AND key = ?",
            facet,
        )
    ledgers = []
    for item in retire:
        ledger_path = backup.with_name(
            f"{backup.stem}-retire-{_safe(item['workspace'])}-into-{_safe(item['into'])}-ledger.json"
        )
        try:
            report, ledger = retire_in_transaction(
                con, item["workspace"], item["into"], on_conflict=_INTEGRATIONS_ON_CONFLICT,
                ledger_path=ledger_path, backup=backup,
            )
        except WorkspaceRetireError as exc:
            item["refused"] = str(exc)
            report, ledger = exc.report, None
        item["tables"] = {t: c for t, c in (report.get("tables") or {}).items() if any(c.values())}
        if ledger is not None:
            ledgers.append((item, ledger_path, ledger))
    # A retire applied before v66 left its source active, and re-running it is
    # a noop that never reaches the status.
    for item in hide + [r for r in retire if "refused" not in r]:
        con.execute("UPDATE workspaces SET status = ? WHERE name = ?", (RETIRED, item["workspace"]))

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
    return plan, ledgers


def plan_curation(*, into: dict[str, str] | None = None, db_path: Path | None = None) -> dict:
    """Report, writing nothing, what :func:`apply_curation` would change.

    The batch runs in a transaction that is rolled back, so the report is the
    one :func:`apply_curation` commits, each retire planned against the ones
    before it. ``into`` maps a phantom to the declared workspace that owns it
    when the owner cannot be found from its alias, its projects' paths or its name.

    Raises:
        WorkspaceCurationError: an ``into`` key that is not a phantom, or a
            target that is not declared.
    """
    from gaia.install_root import registered_roots
    from gaia.paths import db_path as _db_path
    from gaia.store.writer import _connect

    db_file = Path(db_path) if db_path is not None else _db_path()
    roots = registered_roots(db_file)
    con = _connect(db_file)
    try:
        con.execute("BEGIN")
        try:
            # The ledgers that would name db_file as their backup are never saved.
            plan, _ = _curate(con, roots, into or {}, backup=db_file)
        finally:
            con.rollback()
    finally:
        con.close()
    return plan


def apply_curation(*, into: dict[str, str] | None = None, db_path: Path | None = None) -> dict:
    """Apply :func:`plan_curation` in one transaction; return its report with the backup path.

    The database is backed up first, and every retire's undo ledger names that
    backup. A retire the plan marks ``refused`` is skipped; any failure rolls
    the whole batch back and removes the ledgers it saved.

    Raises:
        WorkspaceCurationError: as :func:`plan_curation`, before any write.
    """
    from gaia.install_root import registered_roots
    from gaia.paths import db_path as _db_path
    from gaia.store.workspace_retire import _backup
    from gaia.store.writer import _connect

    db_file = Path(db_path) if db_path is not None else _db_path()
    plan = plan_curation(into=into, db_path=db_file)
    if plan["mode"] == "noop":
        return plan

    roots = registered_roots(db_file)
    con = _connect(db_file)
    saved: list[Path] = []
    try:
        backup = _backup(con, db_file, "curate")
        con.execute("BEGIN IMMEDIATE")
        try:
            plan, ledgers = _curate(con, roots, into or {}, backup=backup)
            for item, path, ledger in ledgers:
                path.write_text(json.dumps(ledger, indent=2, default=str), encoding="utf-8")
                saved.append(path)
                item["ledger"] = str(path)
            con.commit()
        except BaseException:
            con.rollback()
            for path in saved:
                path.unlink(missing_ok=True)
            raise
    finally:
        con.close()
    plan["backup"] = str(backup)
    plan["mode"] = "applied"
    return plan
