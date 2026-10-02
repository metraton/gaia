"""Retire a workspace into another: re-key what it owns, alias what it wrote.

A workspace split in two (one Gaia installed twice, each root registering its
own name) leaves one person's projects, briefs and memory under two names.
Retiring the source folds it into the target without rewriting history:

- OWNERSHIP rows (projects and the scanner rows under them, briefs, context
  contracts, integrations, notifications, memory and its
  links) are re-keyed to the target. ``type='user'`` memory goes to the
  workspace-less user scope and host-scoped memory to the host scope, the
  same destinations the writer gives them.
- HISTORY rows (:data:`HISTORY_TABLES`) keep the name they were written
  under. A ``workspace_aliases`` row records ``source -> target``;
  :func:`workspace_scope` is how a reader of the target includes them.

Every apply backs the database up with the sqlite backup API and writes an
undo ledger beside the backup before committing; :func:`undo_retire` reads it
back. A collision on a unique key that ``on_conflict`` did not resolve refuses
the apply before anything is written. The source ``workspaces`` row is never
deleted: its history rows reference it and would cascade with it. Its
``root_path`` is released instead, because a scan hands every repo under a
recorded root to that root's workspace; a source that still claims a root is
pending, so re-running an applied retire releases a root it left behind.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_TABLES = ("episodes", "episode_anomalies", "harness_events", "agent_contract_handoffs")

# (table, columns that together with ``workspace`` make a row unique). The
# scanner tables reference projects(workspace, name) without ON UPDATE
# CASCADE, so they move in the same transaction under deferred foreign keys.
_OWNED_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("projects", ("name",)),
    ("apps", ("project", "name")),
    ("libraries", ("project", "name")),
    ("services", ("project", "name")),
    ("features", ("project", "name")),
    ("project_facets", ("project", "scope", "key")),
    ("tf_modules", ("project", "name")),
    ("tf_live", ("project", "name")),
    ("releases", ("project", "name")),
    ("workloads", ("project", "name")),
    ("clusters_defined", ("project", "name")),
    ("briefs", ("name",)),
    ("project_context_contracts", ("contract_name",)),
    ("integrations", ("name",)),
    ("task_notifications", ()),
)

# A collision may be resolved only where dropping the losing row cascades to
# nothing: a dropped project or brief would take its plans, tasks or scanner
# rows with it.
RESOLVABLE_TABLES = ("memory", "integrations", "project_context_contracts")
STRATEGIES = ("keep-target", "keep-source")

# Rows keyed to a workspace that the retire neither moves nor aliases as
# history; the report counts them so nothing stays behind unannounced. The
# scheduler tables are retired and unread (schema.sql), so their rows stay put.
_LEFT_BEHIND_TABLES = (
    "gaia_installations", "machines", "clusters", "scheduled_tasks", "schedule_suspensions",
)

_ALIAS_DEPTH = 8
_CHUNK = 500


class WorkspaceRetireError(ValueError):
    """The retire was refused before any write; ``report`` says why."""

    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}


# ---------------------------------------------------------------------------
# Alias reads -- shared by every reader of history
# ---------------------------------------------------------------------------

def alias_target(con: sqlite3.Connection, workspace: str) -> str:
    """Return the workspace ``workspace`` now means: itself unless retired.

    Follows a chain of retires (a -> b -> c). A database without the
    ``workspace_aliases`` table (older than v60) has no aliases.
    """
    current = workspace
    for _ in range(_ALIAS_DEPTH):
        try:
            row = con.execute(
                "SELECT target FROM workspace_aliases WHERE alias = ?", (current,)
            ).fetchone()
        except sqlite3.OperationalError:
            return current
        if row is None or row[0] == current:
            return current
        current = row[0]
    return current


def workspace_scope(con: sqlite3.Connection, workspace: str) -> list[str]:
    """Return ``workspace`` plus every retired name that now means it.

    A history reader filters ``workspace IN (...)`` over this list, so the rows
    a retired workspace wrote stay visible from the workspace it folded into.
    """
    scope = [workspace]
    frontier = [workspace]
    for _ in range(_ALIAS_DEPTH):
        if not frontier:
            break
        marks = ",".join("?" * len(frontier))
        try:
            rows = con.execute(
                f"SELECT alias FROM workspace_aliases WHERE target IN ({marks})", frontier
            ).fetchall()
        except sqlite3.OperationalError:
            return scope
        frontier = [r[0] for r in rows if r[0] not in scope]
        scope.extend(frontier)
    return scope


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _key_of(row: sqlite3.Row, columns: tuple[str, ...]) -> str:
    return "/".join(str(row[c]) for c in columns)


def _exists(con, table: str, workspace: str, columns: tuple[str, ...], row) -> int | None:
    where = " AND ".join(["workspace = ?"] + [f'"{c}" IS ?' for c in columns])
    hit = con.execute(
        f'SELECT rowid FROM "{table}" WHERE {where}',
        [workspace] + [row[c] for c in columns],
    ).fetchone()
    return hit[0] if hit else None


def _count(con, table: str, workspace: str) -> int:
    try:
        return con.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE workspace = ?', (workspace,)
        ).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _plan(con: sqlite3.Connection, source: str, target: str, on_conflict: dict[str, str]) -> dict:
    """Compute, read-only, every row the retire would move, drop or refuse on."""
    from gaia.store.writer import HOST_WORKSPACE, USER_WORKSPACE, memory_home

    tables: dict[str, dict[str, int]] = {}
    moves: dict[str, list[tuple[int, str]]] = {}
    drops: list[tuple[str, int]] = []
    collisions: dict[str, list[str]] = {}
    resolved: dict[str, list[str]] = {}

    def collide(table: str, key: str, source_rowid: int, target_rowid: int) -> bool:
        """Record a collision; True when the source row still moves."""
        strategy = on_conflict.get(table)
        if strategy == "keep-target":
            drops.append((table, source_rowid))
        elif strategy == "keep-source":
            drops.append((table, target_rowid))
        else:
            collisions.setdefault(table, []).append(key)
            return False
        resolved.setdefault(table, []).append(key)
        return strategy == "keep-source"

    for table, columns in _OWNED_TABLES:
        rows = con.execute(
            f'SELECT rowid AS rid, * FROM "{table}" WHERE workspace = ?', (source,)
        ).fetchall()
        planned = []
        for row in rows:
            hit = _exists(con, table, target, columns, row) if columns else None
            if hit is None or collide(table, _key_of(row, columns), row["rid"], hit):
                planned.append((row["rid"], target))
        moves[table] = planned
        tables[table] = {"move": len(planned), "collide": len(rows) - len(planned)}

    memory_rows = con.execute(
        "SELECT rowid AS rid, name, type, initiative, project_ref FROM memory WHERE workspace = ?",
        (source,),
    ).fetchall()
    destination_of: dict[str, str] = {}
    planned_memory = []
    for row in memory_rows:
        dest = memory_home(target, row["type"], row["initiative"])
        hit = _exists(con, "memory", dest, ("name",), row)
        if hit is None or collide("memory", f"{dest}/{row['name']}", row["rid"], hit):
            planned_memory.append((row["rid"], dest))
            destination_of[row["name"]] = dest
    moves["memory"] = planned_memory
    tables["memory"] = {
        "move": sum(1 for _, d in planned_memory if d not in (USER_WORKSPACE, HOST_WORKSPACE)),
        "collide": len(memory_rows) - len(planned_memory),
    }
    tables["memory_user"] = {"move": sum(1 for _, d in planned_memory if d == USER_WORKSPACE)}
    tables["memory_host"] = {"move": sum(1 for _, d in planned_memory if d == HOST_WORKSPACE)}

    # A link follows its endpoints. When they land in different scopes (a user
    # row linked to a project row) the edge cannot live in either and is
    # dropped into the ledger; an edge already present at its destination is a
    # duplicate and is dropped the same way.
    link_rows = con.execute(
        "SELECT rowid AS rid, src_name, dst_name, kind, dst_workspace FROM memory_links "
        "WHERE workspace = ?",
        (source,),
    ).fetchall()
    planned_links, split, duplicate = [], 0, 0
    for row in link_rows:
        dst_moves = row["dst_workspace"] in (None, source)
        ends = {
            destination_of.get(row["src_name"]),
            destination_of.get(row["dst_name"]) if dst_moves else None,
        } - {None}
        if len(ends) > 1:
            drops.append(("memory_links", row["rid"]))
            split += 1
            continue
        dest = ends.pop() if ends else target
        if _exists(con, "memory_links", dest, ("src_name", "dst_name", "kind"), row) is not None:
            drops.append(("memory_links", row["rid"]))
            duplicate += 1
            continue
        planned_links.append((row["rid"], dest))
    moves["memory_links"] = planned_links
    # A link stored under another owner (a user row superseding one written
    # here) keeps pointing at its dst row wherever that row moves.
    repoint = [
        (row["rid"], destination_of.get(row["dst_name"], target))
        for row in con.execute(
            "SELECT rowid AS rid, dst_name FROM memory_links "
            "WHERE dst_workspace = ? AND workspace != ?",
            (source, source),
        )
    ]
    tables["memory_links"] = {
        "move": len(planned_links), "split": split, "duplicate": duplicate,
        "repoint": len(repoint),
    }

    try:
        aliases_in = [
            r[0] for r in con.execute(
                "SELECT alias FROM workspace_aliases WHERE target = ?", (source,)
            )
        ]
    except sqlite3.OperationalError:
        aliases_in = []
    current_alias = alias_target(con, source)
    source_root = con.execute(
        "SELECT root_path FROM workspaces WHERE name = ?", (source,)
    ).fetchone()[0]

    return {
        "source": source,
        "target": target,
        "release_root": source_root or None,
        "tables": tables,
        "collisions": collisions,
        "resolved": resolved,
        "user_rows_to_adjudicate": [
            {"name": r["name"], "initiative": r["initiative"], "project_ref": r["project_ref"]}
            for r in memory_rows
            if r["type"] == "user" and (r["initiative"] or r["project_ref"])
        ],
        "history": {t: _count(con, t, source) for t in HISTORY_TABLES},
        "left_behind": {
            t: n for t in _LEFT_BEHIND_TABLES if (n := _count(con, t, source))
        },
        "alias": current_alias if current_alias != source else None,
        "aliases_retargeted": aliases_in,
        "_moves": moves,
        "_drops": drops,
        "_repoint": repoint,
    }


def _validate(con, source: str, target: str, on_conflict: dict[str, str]) -> None:
    from gaia.store.writer import HOST_WORKSPACE, USER_WORKSPACE

    for table, strategy in on_conflict.items():
        if table not in RESOLVABLE_TABLES or strategy not in STRATEGIES:
            raise WorkspaceRetireError(
                f"--on-conflict {table}={strategy}: resolvable tables are "
                f"{', '.join(RESOLVABLE_TABLES)} and strategies {', '.join(STRATEGIES)}"
            )
    if source == target:
        raise WorkspaceRetireError("source and target are the same workspace")
    sentinels = (USER_WORKSPACE, HOST_WORKSPACE, "global")
    if source in sentinels or target in sentinels:
        raise WorkspaceRetireError(f"{', '.join(sentinels)} are Gaia scopes, not workspaces to retire")
    for name in (source, target):
        if con.execute("SELECT 1 FROM workspaces WHERE name = ?", (name,)).fetchone() is None:
            raise WorkspaceRetireError(f"workspace {name!r} is not registered")
    resolved = alias_target(con, target)
    if resolved != target:
        raise WorkspaceRetireError(
            f"workspace {target!r} is itself retired into {resolved!r}; retire into {resolved!r}"
        )


def _public(plan: dict) -> dict:
    return {k: v for k, v in plan.items() if not k.startswith("_")}


def _pending(plan: dict) -> bool:
    return (
        bool(plan["_drops"]) or bool(plan["_repoint"]) or any(plan["_moves"].values())
        or plan["alias"] != plan["target"] or plan["release_root"] is not None
    )


def plan_retire(
    source: str, target: str, *, on_conflict: dict[str, str] | None = None,
    db_path: Path | None = None,
) -> dict:
    """Report what :func:`apply_retire` would do, writing nothing."""
    from gaia.store.writer import _connect

    on_conflict = on_conflict or {}
    con = _connect(db_path)
    try:
        _validate(con, source, target, on_conflict)
        plan = _plan(con, source, target, on_conflict)
    finally:
        con.close()
    report = _public(plan)
    report["mode"] = "dry-run" if _pending(plan) else "noop"
    return report


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def _backup(con: sqlite3.Connection, db_file: Path, label: str) -> Path:
    backup_dir = db_file.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = backup_dir / f"{db_file.stem}-pre-workspace-{label}-{stamp}.db"
    dest = sqlite3.connect(str(target))
    try:
        con.backup(dest)
    finally:
        dest.close()
    return target


def _has_integer_pk(con, table: str) -> bool:
    pk = [r for r in con.execute(f'PRAGMA table_info("{table}")') if r[5]]
    return len(pk) == 1 and (pk[0][2] or "").upper() == "INTEGER"


def _capture_and_delete(con, table: str, rowid: int) -> dict:
    row = con.execute(f'SELECT rowid AS "__rowid__", * FROM "{table}" WHERE rowid = ?', (rowid,)).fetchone()
    captured = {k: row[k] for k in row.keys()}
    con.execute(f'DELETE FROM "{table}" WHERE rowid = ?', (rowid,))
    return {"table": table, "row": captured}


def _reinsert(con, table: str, row: dict) -> None:
    values = {k: v for k, v in row.items() if k != "__rowid__"}
    if not _has_integer_pk(con, table):
        values["rowid"] = row["__rowid__"]
    columns = ", ".join(f'"{c}"' for c in values)
    marks = ", ".join("?" * len(values))
    con.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({marks})', list(values.values()))


def _rekey(con, table: str, rowids: list[int], workspace: str, *, only_from: str | None = None) -> int:
    changed = 0
    for i in range(0, len(rowids), _CHUNK):
        chunk = rowids[i:i + _CHUNK]
        sql = f'UPDATE "{table}" SET workspace = ? WHERE rowid IN ({",".join("?" * len(chunk))})'
        params: list[Any] = [workspace, *chunk]
        if only_from is not None:
            sql += " AND workspace = ?"
            params.append(only_from)
        changed += con.execute(sql, params).rowcount
    return changed


def apply_retire(
    source: str, target: str, *, on_conflict: dict[str, str] | None = None,
    db_path: Path | None = None,
) -> dict:
    """Fold ``source`` into ``target``; return the report plus backup and ledger paths.

    Raises:
        WorkspaceRetireError: invalid names, or a collision ``on_conflict``
            left unresolved -- in both cases before any write.
    """
    from gaia.paths import db_path as _db_path
    from gaia.store.writer import _connect, _ensure_workspace_row

    on_conflict = on_conflict or {}
    db_file = Path(db_path) if db_path is not None else _db_path()
    con = _connect(db_file)
    try:
        _validate(con, source, target, on_conflict)
        plan = _plan(con, source, target, on_conflict)
        report = _public(plan)
        if plan["collisions"]:
            report["mode"] = "refused"
            raise WorkspaceRetireError(
                "unresolved collisions: " + "; ".join(
                    f"{t}: {', '.join(keys)}" for t, keys in plan["collisions"].items()
                ),
                report,
            )
        if not _pending(plan):
            report["mode"] = "noop"
            return report

        backup = _backup(con, db_file, f"retire-{_safe(source)}-into-{_safe(target)}")
        ledger_path = backup.with_name(backup.stem + "-ledger.json")
        ledger: dict[str, Any] = {
            "kind": "workspace-retire",
            "source": source,
            "target": target,
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "backup": str(backup),
            "created_workspaces": [],
            "dropped": [],
            "moved": {},
            "alias_previous": None,
            "aliases_retargeted": plan["aliases_retargeted"],
            "root_path_previous": plan["release_root"],
        }

        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("PRAGMA defer_foreign_keys = ON")
            destinations = {d for rows in plan["_moves"].values() for _, d in rows}
            for workspace in sorted(destinations - {target}):
                if con.execute("SELECT 1 FROM workspaces WHERE name = ?", (workspace,)).fetchone() is None:
                    _ensure_workspace_row(con, workspace)
                    ledger["created_workspaces"].append(workspace)
            for table, rowid in plan["_drops"]:
                ledger["dropped"].append(_capture_and_delete(con, table, rowid))
            for table, rows in plan["_moves"].items():
                by_dest: dict[str, list[int]] = {}
                for rowid, dest in rows:
                    by_dest.setdefault(dest, []).append(rowid)
                for dest, rowids in by_dest.items():
                    _rekey(con, table, rowids, dest)
                    ledger["moved"].setdefault(table, {})[dest] = rowids
            for rowid, dest in plan["_repoint"]:
                con.execute(
                    "UPDATE memory_links SET dst_workspace = ? WHERE rowid = ?", (dest, rowid)
                )
            ledger["repointed"] = plan["_repoint"]
            con.execute("UPDATE workspaces SET root_path = NULL WHERE name = ?", (source,))

            previous = con.execute(
                "SELECT alias, target, created_at, ledger FROM workspace_aliases WHERE alias = ?",
                (source,),
            ).fetchone()
            ledger["alias_previous"] = dict(previous) if previous else None
            con.execute("UPDATE workspace_aliases SET target = ? WHERE target = ?", (target, source))
            con.execute(
                "INSERT OR REPLACE INTO workspace_aliases (alias, target, ledger) VALUES (?, ?, ?)",
                (source, target, str(ledger_path)),
            )
            ledger_path.write_text(json.dumps(ledger, indent=2, default=str), encoding="utf-8")
            con.commit()
        except BaseException:
            con.rollback()
            raise
    finally:
        con.close()

    report["mode"] = "applied"
    report["backup"] = str(backup)
    report["ledger"] = str(ledger_path)
    return report


# ---------------------------------------------------------------------------
# Undo
# ---------------------------------------------------------------------------

def _references(con, workspace: str) -> int:
    """Rows in any table whose ``workspace`` column names ``workspace``."""
    total = 0
    for (table,) in con.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND sql NOT LIKE 'CREATE VIRTUAL%' "
        "AND name NOT IN ('workspaces', 'workspace_aliases')"
    ).fetchall():
        columns = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
        if "workspace" in columns:
            total += _count(con, table, workspace)
    return total


def undo_retire(ledger_path: Path | str, *, dry_run: bool = False, db_path: Path | None = None) -> dict:
    """Put back what the retire recorded in ``ledger_path`` wrote.

    A row that has left the workspace the retire moved it to since then is
    not pulled back; it is reported under ``diverged``, as is a released root
    when the source's own ``root_path`` is no longer empty. Rows written since the
    retire are never deleted: they stay where they are, or, when they conflict
    with a row the undo would put back (same key, or a child of a moved
    project), the undo is refused whole.

    Raises:
        WorkspaceRetireError: the ledger is unreadable, its retire is not
            the one in force (never applied, or already undone), or rows
            written since conflict with what it would put back.
    """
    from gaia.paths import db_path as _db_path
    from gaia.store.writer import _connect

    path = Path(ledger_path)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceRetireError(f"cannot read ledger {path}: {exc}") from exc
    if ledger.get("kind") != "workspace-retire":
        raise WorkspaceRetireError(f"{path} is not a workspace retire ledger")
    source, target = ledger["source"], ledger["target"]

    db_file = Path(db_path) if db_path is not None else _db_path()
    con = _connect(db_file)
    try:
        live = con.execute(
            "SELECT 1 FROM workspace_aliases WHERE alias = ? AND ledger = ?", (source, str(path))
        ).fetchone()
        if live is None:
            raise WorkspaceRetireError(
                f"the retire of {source!r} recorded in {path} is not in force "
                f"(never applied or already undone)"
            )
        report: dict[str, Any] = {
            "source": source,
            "target": target,
            "restore": {t: sum(len(r) for r in d.values()) for t, d in ledger["moved"].items()},
            "reinsert": len(ledger["dropped"]),
            "restore_root": ledger.get("root_path_previous"),
        }
        if dry_run:
            report["mode"] = "dry-run"
            return report

        backup = _backup(con, db_file, f"undo-retire-{_safe(source)}")
        diverged: dict[str, int] = {}
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute("PRAGMA defer_foreign_keys = ON")
            for table, by_dest in ledger["moved"].items():
                for dest, rowids in by_dest.items():
                    back = _rekey(con, table, rowids, source, only_from=dest)
                    if back != len(rowids):
                        diverged[table] = diverged.get(table, 0) + len(rowids) - back
            for rowid, dest in ledger.get("repointed", []):
                con.execute(
                    "UPDATE memory_links SET dst_workspace = ? WHERE rowid = ? AND dst_workspace = ?",
                    (source, rowid, dest),
                )
            for entry in reversed(ledger["dropped"]):
                _reinsert(con, entry["table"], entry["row"])
            if report["restore_root"] and not con.execute(
                "UPDATE workspaces SET root_path = ? WHERE name = ? AND root_path IS NULL",
                (report["restore_root"], source),
            ).rowcount:
                diverged["workspaces.root_path"] = 1

            con.execute("DELETE FROM workspace_aliases WHERE alias = ?", (source,))
            previous = ledger.get("alias_previous")
            if previous:
                con.execute(
                    "INSERT INTO workspace_aliases (alias, target, created_at, ledger) "
                    "VALUES (?, ?, ?, ?)",
                    (previous["alias"], previous["target"], previous["created_at"], previous["ledger"]),
                )
            for alias in ledger.get("aliases_retargeted", []):
                con.execute(
                    "UPDATE workspace_aliases SET target = ? WHERE alias = ? AND target = ?",
                    (source, alias, target),
                )
            # A scope the retire created goes only while nothing names it: the
            # memory_history trigger keys its lines to the scope a row moved into,
            # and deleting the scope would cascade those lines away.
            for workspace in ledger.get("created_workspaces", []):
                if _references(con, workspace) == 0:
                    con.execute("DELETE FROM workspaces WHERE name = ?", (workspace,))
            con.commit()
        except sqlite3.IntegrityError as exc:
            con.rollback()
            report["mode"] = "refused"
            raise WorkspaceRetireError(
                f"cannot undo the retire of {source!r}: rows written since then conflict with "
                f"the rows it would put back ({exc}). Nothing was changed; the database "
                f"as it was is at {backup}",
                report,
            ) from exc
        except BaseException:
            con.rollback()
            raise
    finally:
        con.close()

    report["mode"] = "undone"
    report["backup"] = str(backup)
    report["diverged"] = diverged
    return report
