"""References that open what Gaia stores: parse, format and resolve ``KIND:SCOPE/ID#FIELD``."""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Optional

KINDS = ("contract", "memory", "context", "brief", "plan", "task", "evidence", "approval", "notification")
SAFE_REF = re.compile(r"[A-Za-z0-9_.:/@-][A-Za-z0-9_.:/#@-]*\Z")
_SCOPED_KINDS = frozenset({"memory", "context", "brief"})
_SCOPE_ALIASES = {"user": "_gaia_user", "gaia": "_gaia_host"}
_SCOPE_SPELLINGS = {workspace: alias for alias, workspace in _SCOPE_ALIASES.items()}
_NEAREST = 3


class RefError(ValueError):
    """A reference that is malformed, unsafe to print, or opens nothing."""


@dataclass(frozen=True)
class Ref:
    """One reference; ``scope`` and ``field`` are empty when it carries none."""

    kind: str
    scope: str
    id: str
    field: str = ""


@dataclass
class Resolution:
    """What a reference opened: its canonical form, labelled sibling ids, the per-kind verb and the row."""

    ref: Ref
    ids: dict
    verb: str
    why: str
    document: Any
    notes: list = dataclass_field(default_factory=list)


def parse_ref(text: str) -> Ref:
    """Parse ``KIND:SCOPE/ID#FIELD``; only memory, context and brief read a scope before the first slash."""
    text = text.strip()
    kind, sep, rest = text.partition(":")
    kind = kind.lower()
    if not sep or kind not in KINDS:
        raise RefError(f"{text!r} is not KIND:ID with KIND one of {', '.join(KINDS)}")
    if not SAFE_REF.match(text):
        raise RefError(f"{text!r} holds characters outside [A-Za-z0-9_.:/#@-]")
    rest, _, field = rest.partition("#")
    scope, ident = "", rest
    if kind in _SCOPED_KINDS and "/" in rest:
        scope, _, ident = rest.partition("/")
    if not ident:
        raise RefError(f"{text!r} names no id after {kind}:")
    return Ref(kind, scope, ident, field)


def format_ref(ref: Ref) -> str:
    """The printable reference, refused when the shell guards would not pass it verbatim."""
    body = f"{ref.scope}/{ref.id}" if ref.scope else ref.id
    text = f"{ref.kind}:{body}" + (f"#{ref.field}" if ref.field else "")
    if not ref.id or "#" in ref.scope + ref.id + ref.field or not SAFE_REF.match(text):
        raise RefError(f"{text!r} is not a shell-safe reference")
    return text


def select_field(document: Any, field: str) -> Any:
    """The value at the dotted ``field`` inside ``document``; the error names the keys that do exist."""
    current = document
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            present = ", ".join(sorted(current)) if isinstance(current, dict) else "(not an object)"
            raise RefError(f"no field {field!r}: {part!r} is not among {present}")
        current = current[part]
    return current


def resolve(ref: Ref, db_path: Optional[Path] = None) -> Resolution:
    """Open ``ref`` on a query-only connection; RefError names the nearest match when it opens nothing."""
    from gaia.store.reader import _ro_connect

    con = _ro_connect(db_path)
    try:
        return _READERS[ref.kind](con, ref)
    finally:
        con.close()


def _rows(con: sqlite3.Connection, sql: str, *params: Any) -> list:
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def _workspace(con: sqlite3.Connection, scope: str) -> str:
    name = _SCOPE_ALIASES.get(scope, scope)
    alias = con.execute("SELECT target FROM workspace_aliases WHERE alias = ?", (name,)).fetchone()
    return alias[0] if alias else name


def _spelled(workspace: str) -> str:
    return _SCOPE_SPELLINGS.get(workspace, workspace)


def _not_found(ref: Ref, nearest: list) -> RefError:
    hint = f"; nearest: {', '.join(nearest)}" if nearest else ""
    return RefError(f"{format_ref(ref)} opens nothing{hint}")


def _nearest_names(wanted: str, rows: list) -> list:
    names = [row["name"] for row in rows]
    prefixed = [name for name in names if name.startswith(wanted)]
    close = difflib.get_close_matches(wanted, names, n=_NEAREST, cutoff=0.6)
    picked = list(dict.fromkeys(prefixed + close))[:_NEAREST]
    return [row for name in picked for row in rows if row["name"] == name]


def _in_scope(con: sqlite3.Connection, ref: Ref, table: str, columns: str, live: str = "1") -> tuple:
    rows = _rows(con, f"SELECT {columns} FROM {table} WHERE {live} AND name = ?", ref.id)
    asked = _workspace(con, ref.scope) if ref.scope else ""
    here = [row for row in rows if row["workspace"] == asked]
    if here:
        return here[0], []
    if len(rows) == 1:
        lives = _spelled(rows[0]["workspace"])
        moved = [f"moved: {format_ref(ref)} now lives in {lives}"] if ref.scope else []
        return rows[0], moved
    if rows:
        held = ", ".join(format_ref(Ref(ref.kind, _spelled(r["workspace"]), r["name"])) for r in rows)
        raise RefError(f"{format_ref(ref)} is held by several workspaces: {held}")
    pool = _rows(con, f"SELECT workspace, name FROM {table} WHERE {live}")
    nearest = [Ref(ref.kind, _spelled(r["workspace"]), r["name"]) for r in _nearest_names(ref.id, pool)]
    raise _not_found(ref, [format_ref(r) for r in nearest])


def _first_sentence(text: Any) -> str:
    text = str(text or "").strip()
    return re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0] if text else ""


_HANDOFF_SELECT = (
    "SELECT id, contract_id, agent_id, harness_agent_id, session_id, plan_task_id, agent_state, "
    "continues_handoff_id, created_at, dispatch_description, raw_handoff_json "
    "FROM agent_contract_handoffs WHERE "
)


def _contract(con: sqlite3.Connection, ref: Ref) -> Resolution:
    from gaia.contract.validator import envelope_as_read
    from gaia.store.writer import collapse_continuation_chains

    ident = ref.id
    if ident.isdigit():
        how, rows = "handoff id", _rows(con, _HANDOFF_SELECT + "id = ?", int(ident))
    elif "." in ident:
        how, rows = "contract_id", _rows(con, _HANDOFF_SELECT + "contract_id = ?", ident)
    else:
        how, rows = "harness id", _rows(con, _HANDOFF_SELECT + "harness_agent_id = ?", ident)
        if not rows:
            how, rows = "agent_id", _rows(con, _HANDOFF_SELECT + "agent_id = ?", ident)
    rows.sort(key=lambda row: (row["created_at"] or "", row["id"]))
    live = collapse_continuation_chains(rows)
    if not live:
        stem = ident.split(".")[0]
        nearest = _rows(
            con, "SELECT contract_id FROM agent_contract_handoffs WHERE contract_id LIKE ? LIMIT ?",
            f"{stem}%", _NEAREST,
        ) if not ident.isdigit() else []
        raise _not_found(ref, [f"contract:{row['contract_id']}" for row in nearest])
    if len(live) > 1:
        held = ", ".join(f"contract:{row['contract_id'] or row['id']}" for row in live)
        raise RefError(f"{format_ref(ref)} matches unrelated turns: {held}")
    row = live[-1]
    contract_id = row["contract_id"] or str(row["id"])
    notes = [] if how == "contract_id" else [f"{ident} matched as the {how} of handoff {row['id']}"]
    if len(rows) > 1:
        notes.append(f"the {how} names a chain of {len(rows)} turns; this is its live link")
    try:
        envelope = envelope_as_read(json.loads(row["raw_handoff_json"] or "null")) or {}
    except ValueError:
        envelope = {}
    ids = {
        "handoff": str(row["id"]),
        "contract": row["contract_id"],
        "agent_id": row["agent_id"],
        "resume": row["harness_agent_id"],
        "session": row["session_id"],
        "plan_task": f"task:{row['plan_task_id']}" if row["plan_task_id"] else None,
    }
    if row["contract_id"]:
        verb = f"gaia contract view --contract-id {row['contract_id']}"
    else:
        verb = f"gaia contract view --harness-id {row['harness_agent_id']}" if row["harness_agent_id"] else ""
    if verb and ref.field:
        verb += f" --field {ref.field}"
    conclusion = envelope.get("conclusion") if isinstance(envelope, dict) else None
    why = ": ".join(part for part in (row["dispatch_description"], _first_sentence(conclusion)) if part)
    return Resolution(
        Ref("contract", "", contract_id, ref.field), _present(ids), verb,
        why or f"Turn {row['id']} ({row['agent_state']})", envelope, notes,
    )


def _memory(con: sqlite3.Connection, ref: Ref) -> Resolution:
    row, notes = _in_scope(
        con, ref, "memory", "workspace, name, type, class, status, description, body, updated_at",
        live="deleted_at IS NULL",
    )
    workspace = row["workspace"]
    return Resolution(
        Ref("memory", _spelled(workspace), row["name"], ref.field), {},
        f"gaia memory show {row['name']} --workspace {workspace}",
        row["description"] or f"{row['type']} row {row['name']}", row, notes,
    )


def _brief(con: sqlite3.Connection, ref: Ref) -> Resolution:
    row, notes = _in_scope(con, ref, "briefs", "id, workspace, name, status, title, objective, project")
    plan = con.execute("SELECT id FROM plans WHERE brief_id = ?", (row["id"],)).fetchone()
    return Resolution(
        Ref("brief", _spelled(row["workspace"]), row["name"], ref.field),
        _present({"plan": f"plan:{plan[0]}" if plan else None}),
        f"gaia brief show {row['name']} --workspace {row['workspace']}",
        f"Brief {row['name']} ({row['status']})" + (f": {row['title']}" if row["title"] else ""), row, notes,
    )


_PLAN_SELECT = (
    "SELECT p.id, p.status, p.content, p.paused_at, b.workspace, b.name AS brief "
    "FROM plans p JOIN briefs b ON b.id = p.brief_id WHERE "
)


def _plan(con: sqlite3.Connection, ref: Ref) -> Resolution:
    if ref.id.isdigit():
        rows = _rows(con, _PLAN_SELECT + "p.id = ?", int(ref.id))
    else:
        scope, _, brief = ref.id.rpartition("/")
        rows = _rows(con, _PLAN_SELECT + "b.name = ?", brief)
        if scope:
            rows = [row for row in rows if row["workspace"] == _workspace(con, scope)]
    if len(rows) != 1:
        held = ", ".join(f"plan:{row['id']}" for row in rows)
        raise RefError(f"{format_ref(ref)} matches several plans: {held}") if rows else _not_found(ref, [])
    row = rows[0]
    brief = format_ref(Ref("brief", _spelled(row["workspace"]), row["brief"]))
    return Resolution(
        Ref("plan", "", str(row["id"]), ref.field), {"brief": brief},
        f"gaia plan show {row['brief']} --workspace {row['workspace']}",
        f"Plan {row['id']} of brief {row['brief']} ({row['status']})", row,
    )


_TASK_SELECT = (
    "SELECT t.id, t.order_num, t.goal, t.status, t.plan_id, b.workspace, b.name AS brief "
    "FROM tasks t JOIN plans p ON p.id = t.plan_id JOIN briefs b ON b.id = p.brief_id WHERE "
)


def _task(con: sqlite3.Connection, ref: Ref) -> Resolution:
    if ref.id.isdigit():
        rows = _rows(con, _TASK_SELECT + "t.id = ?", int(ref.id))
    else:
        brief, _, order = ref.id.rpartition("/")
        scope, _, brief = brief.rpartition("/")
        if not (brief and order.isdigit()):
            raise RefError(f"{format_ref(ref)} is neither task:ID nor task:BRIEF/ORDER")
        rows = _rows(con, _TASK_SELECT + "b.name = ? AND t.order_num = ?", brief, int(order))
        if scope:
            rows = [row for row in rows if row["workspace"] == _workspace(con, scope)]
    if len(rows) != 1:
        held = ", ".join(f"task:{row['id']}" for row in rows)
        raise RefError(f"{format_ref(ref)} matches several tasks: {held}") if rows else _not_found(ref, [])
    row = rows[0]
    ids = {
        "order": str(row["order_num"]),
        "plan": f"plan:{row['plan_id']}",
        "brief": format_ref(Ref("brief", _spelled(row["workspace"]), row["brief"])),
    }
    return Resolution(
        Ref("task", "", str(row["id"]), ref.field), ids, f"gaia task show --id {row['id']}",
        f"Task {row['order_num']} of brief {row['brief']}: {_first_sentence(row['goal'])}", row,
    )


def _evidence(con: sqlite3.Connection, ref: Ref) -> Resolution:
    rows = ref.id.isdigit() and _rows(
        con, "SELECT e.*, b.workspace, b.name AS brief FROM evidence e "
        "JOIN briefs b ON b.id = e.brief_id WHERE e.id = ?", int(ref.id),
    )
    if not rows:
        raise _not_found(ref, [])
    row = rows[0]
    ids = {"ac": row["ac_id"], "brief": format_ref(Ref("brief", _spelled(row["workspace"]), row["brief"]))}
    return Resolution(
        Ref("evidence", "", str(row["id"]), ref.field), ids, f"gaia evidence show {row['id']}",
        f"Evidence for {row['ac_id']} of brief {row['brief']} ({row['type']})", row,
    )


def _approval(con: sqlite3.Connection, ref: Ref) -> Resolution:
    wanted = ref.id if ref.id.startswith("P-") else f"P-{ref.id}"
    rows = _rows(con, "SELECT * FROM approvals WHERE substr(id, 1, ?) = ?", len(wanted), wanted)
    if len(rows) != 1:
        held = ", ".join(f"approval:{row['id']}" for row in rows)
        raise RefError(f"{format_ref(ref)} is a prefix of several approvals: {held}") if rows else _not_found(ref, [])
    row = rows[0]
    try:
        row["payload_json"] = json.loads(row["payload_json"] or "null")
    except ValueError:
        pass
    return Resolution(
        Ref("approval", "", row["id"], ref.field), _present({"agent_id": row["agent_id"]}),
        f"gaia approvals show {row['id']}", f"Approval {row['status']} requested by {row['agent_id']}", row,
    )


def _notification(con: sqlite3.Connection, ref: Ref) -> Resolution:
    rows = ref.id.isdigit() and _rows(con, "SELECT * FROM task_notifications WHERE id = ?", int(ref.id))
    if not rows:
        raise _not_found(ref, [])
    row = rows[0]
    return Resolution(
        Ref("notification", "", str(row["id"]), ref.field), {}, f"gaia notifications show {row['id']}",
        row["headline"], row,
    )


def _context(con: sqlite3.Connection, ref: Ref) -> Resolution:
    from gaia.store.project_context import project_sections, resolve_project, workspace_sections

    if not ref.scope:
        raise RefError(f"{format_ref(ref)} needs a workspace: context:WORKSPACE/[PROJECT/]SECTION")
    workspace = _workspace(con, ref.scope)
    project_ref, _, section = ref.id.rpartition("/")
    project = resolve_project(con, project_ref, workspace) if project_ref else None
    if project_ref and project is None:
        raise RefError(f"{format_ref(ref)}: no active project {project_ref!r}")
    sections = project_sections(con, project) if project else workspace_sections(con, workspace)
    found = sections.get(section)
    if found is None:
        nearest = difflib.get_close_matches(section, sorted(sections), n=_NEAREST, cutoff=0.5)
        prefix = f"{project_ref}/" if project_ref else ""
        raise _not_found(ref, [format_ref(Ref("context", ref.scope, prefix + name)) for name in nearest])
    holder = project["workspace"] if project else workspace
    notes = [f"moved: {format_ref(ref)} now lives in {_spelled(holder)}"] if _spelled(holder) != ref.scope else []
    ident = f"{project['name']}/{section}" if project else section
    verb = f"gaia context get-contract --section {section} --workspace {holder}"
    if project:
        verb += f" --project {project['name']}"
    return Resolution(
        Ref("context", _spelled(holder), ident, ref.field), {}, verb,
        f"Context section {section} of {project['name'] if project else holder}", found["payload"], notes,
    )


def _present(ids: dict) -> dict:
    return {label: value for label, value in ids.items() if value}


_READERS = {
    "contract": _contract,
    "memory": _memory,
    "context": _context,
    "brief": _brief,
    "plan": _plan,
    "task": _task,
    "evidence": _evidence,
    "approval": _approval,
    "notification": _notification,
}
