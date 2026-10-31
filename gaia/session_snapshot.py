"""Host-neutral, session-scoped snapshot of the work a compaction must not lose.

Derived from the database at birth -- contracts, signatures, plan and task, pending
proposals, memory the session wrote -- beside the hand note, aged and marked stale
once a newer contract exists; rendered at birth and by ``gaia session snapshot``.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from gaia.paths.resolver import data_dir

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_RESUME_DIR = "session_resume"
_RESUME_MAX_AGE_SECONDS = 30 * 24 * 3600
RESUME_POINT_MAX_CHARS = 2000
_GOAL_MAX_CHARS = 160
_MAX_LISTED = 10
_UNBOUND_WINDOW_SECONDS = 24 * 3600
_WAITING_STATES = frozenset({"NEEDS_INPUT", "APPROVAL_REQUEST", "BLOCKED"})
_BLOCK_MAX_CHARS = 1500
_TRUNCATED = "\n... (snapshot truncated; `gaia session snapshot --json` has the rest)"
_ISO = "%Y-%m-%dT%H:%M:%SZ"
_PROPOSALS_VERB = "gaia contract proposals list"
_UNAVAILABLE = "unavailable (the query failed)"


def _resume_path(session_id: str):
    if not _SESSION_ID_RE.match(session_id or ""):
        raise ValueError(f"invalid session id: {session_id!r}")
    return data_dir() / _RESUME_DIR / f"{session_id}.txt"


def _prune_resume_points(directory, keep) -> None:
    cutoff = time.time() - _RESUME_MAX_AGE_SECONDS
    for stale in directory.glob("*.txt"):
        try:
            if stale != keep and stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue


def write_resume_point(session_id: str, text: str) -> str:
    """Replace the session's resume point with *text* (at most 2000 characters), drop resume points older than 30 days, and return the file written."""
    text = text.strip()
    if len(text) > RESUME_POINT_MAX_CHARS:
        raise ValueError(f"resume point is {len(text)} characters; the limit is {RESUME_POINT_MAX_CHARS}")
    path = _resume_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    _prune_resume_points(path.parent, path)
    return str(path)


def read_resume_point(session_id: str) -> str | None:
    """The session's last written resume point, or None when it has none."""
    try:
        text = _resume_path(session_id).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return text or None


def resume_point_written_at(session_id: str) -> str | None:
    """When the session's resume point was last written, as ISO-8601 UTC, or None when it has none."""
    try:
        return time.strftime(_ISO, time.gmtime(_resume_path(session_id).stat().st_mtime))
    except (OSError, ValueError):
        return None


def _open_contracts(con, session_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """This session's unsettled contracts to list and the unbound ones gone quiet, both newest first.

    A row is settled when another row continues it or it is COMPLETE. A row
    bound to a plan task (its own binding, or a verifier's through its
    producer) stays open only while that task is pending and the row is the
    task's latest; the task's status, not the row, says whether the work is
    done. An unbound row has no such authority: one waiting on the
    orchestrator (input, approval, a block) is always listed, any other only
    while its last recorded activity is under 24 hours old.
    """
    rows = [
        dict(r)
        for r in con.execute(
            "SELECT h.id, h.contract_id, h.agent_id, h.agent_state, h.kind, "
            "MAX(h.created_at, COALESCE(h.claimed_at, h.created_at)) AS last_activity, "
            "h.continues_handoff_id, COALESCE(h.plan_task_id, parent.plan_task_id) AS plan_task_id, "
            "t.status AS task_status "
            "FROM agent_contract_handoffs h "
            "LEFT JOIN agent_contract_handoffs parent ON parent.id = h.parent_handoff_id "
            "LEFT JOIN tasks t ON t.id = COALESCE(h.plan_task_id, parent.plan_task_id) "
            "WHERE h.session_id = ? ORDER BY h.id",
            (session_id,),
        )
    ]
    continued = {r["continues_handoff_id"] for r in rows if r["continues_handoff_id"] is not None}
    latest_of_task = {r["plan_task_id"]: r["id"] for r in rows if r["plan_task_id"] is not None}
    cutoff = time.strftime(_ISO, time.gmtime(time.time() - _UNBOUND_WINDOW_SECONDS))
    open_rows: list[dict[str, Any]] = []
    quiet_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["id"] in continued or row["agent_state"] == "COMPLETE":
            continue
        if row["plan_task_id"] is not None:
            if row["task_status"] == "pending" and latest_of_task[row["plan_task_id"]] == row["id"]:
                open_rows.append(row)
        elif row["agent_state"] in _WAITING_STATES or (row["last_activity"] or "") >= cutoff:
            open_rows.append(row)
        else:
            quiet_rows.append(row)
    return (
        sorted(open_rows, key=lambda r: r["id"], reverse=True),
        sorted(quiet_rows, key=lambda r: r["id"], reverse=True),
    )


def _pending_signatures(con, session_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT id, agent_id, created_at FROM approvals "
        "WHERE session_id = ? AND status = 'pending' ORDER BY created_at DESC",
        (session_id,),
    ).fetchall()
    from gaia.approvals.reading import PENDING, live_sessions, read_on

    readings = read_on(con, (r["id"] for r in rows), live=live_sessions())
    return [dict(r) for r in rows if readings.get(r["id"], {}).get("state") == PENDING]


def _active_task(con, session_id: str) -> dict[str, Any] | None:
    """The pending plan task of this session's latest task-bound contract.

    briefs, plans and tasks carry no session key, so the binding comes from
    the contracts' plan_task_id, or a verifier's producer's.
    """
    row = con.execute(
        "SELECT b.name AS brief, b.workspace AS workspace, p.id AS plan_id, "
        "t.id AS task_id, t.order_num AS task_order, t.goal AS goal "
        "FROM agent_contract_handoffs h "
        "LEFT JOIN agent_contract_handoffs parent ON parent.id = h.parent_handoff_id "
        "JOIN tasks t ON t.id = COALESCE(h.plan_task_id, parent.plan_task_id) "
        "JOIN plans p ON p.id = t.plan_id "
        "JOIN briefs b ON b.id = p.brief_id "
        "WHERE h.session_id = ? AND t.status = 'pending' ORDER BY h.id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def _active_plan(con, _session_id: str) -> dict[str, Any] | None:
    """The newest unpaused active plan across sessions, since plans carry no session key, with every workspace's active-plan count."""
    plans = con.execute(
        "SELECT b.name AS brief, b.workspace AS workspace, p.id AS plan_id FROM plans p "
        "JOIN briefs b ON b.id = p.brief_id WHERE p.status = 'active' AND p.paused_at IS NULL "
        "ORDER BY p.updated_at DESC, p.id DESC"
    ).fetchall()
    if not plans:
        return None
    plan = dict(plans[0])
    task = con.execute(
        "SELECT id, order_num, goal FROM tasks WHERE plan_id = ? AND status = 'pending' "
        "ORDER BY order_num, id LIMIT 1",
        (plan["plan_id"],),
    ).fetchone()
    plan["task_id"], plan["task_order"], plan["goal"] = tuple(task) if task else (None, None, None)
    plan["active_by_workspace"] = [
        list(row)
        for row in con.execute(
            "SELECT b.workspace, COUNT(*) FROM plans p JOIN briefs b ON b.id = p.brief_id "
            "WHERE p.status = 'active' GROUP BY b.workspace ORDER BY b.workspace"
        )
    ]
    return plan


def _pending_proposals(con, _session_id: str) -> int:
    """Every pending proposal, the set `gaia contract proposals list` shows; proposals carry no session key."""
    return con.execute("SELECT COUNT(*) FROM proposals WHERE status = 'pending'").fetchone()[0]


def _memory_touched(con, session_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT workspace, name, type, updated_at FROM memory "
        "WHERE origin_session_id = ? AND deleted_at IS NULL ORDER BY updated_at DESC, name",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _newest_contract_at(con, session_id: str) -> str | None:
    """The latest birth or claim among this session's contracts; the handoff row records no finalize time."""
    return con.execute(
        "SELECT MAX(MAX(created_at, COALESCE(claimed_at, created_at))) "
        "FROM agent_contract_handoffs WHERE session_id = ?",
        (session_id,),
    ).fetchone()[0]


def build_snapshot(session_id: str) -> dict[str, Any]:
    """The snapshot of *session_id*; a source that fails contributes its empty value and is named in ``unavailable``."""
    snapshot: dict[str, Any] = {
        "session_id": session_id,
        "unavailable": [],
        "open_contracts": [],
        "older_unbound": [],
        "pending_signatures": [],
        "active_task": None,
        "active_plan": None,
        "pending_proposals": 0,
        "memory_touched": [],
        "newest_contract_at": None,
        "resume_point": read_resume_point(session_id),
        "resume_point_written_at": resume_point_written_at(session_id),
    }
    if not session_id:
        return snapshot
    try:
        from gaia.store.reader import _connect

        con = _connect()
    except Exception:
        return snapshot
    try:
        try:
            snapshot["open_contracts"], snapshot["older_unbound"] = _open_contracts(con, session_id)
        except Exception:
            snapshot["unavailable"].append("open_contracts")
        for key, query in (
            ("pending_signatures", _pending_signatures),
            ("active_task", _active_task),
            ("active_plan", _active_plan),
            ("pending_proposals", _pending_proposals),
            ("memory_touched", _memory_touched),
            ("newest_contract_at", _newest_contract_at),
        ):
            try:
                snapshot[key] = query(con, session_id)
            except Exception:
                snapshot["unavailable"].append(key)
    finally:
        con.close()
    return snapshot


def _clip(text: str, limit: int) -> str:
    """*text* cut to at most *limit* characters on a word boundary, ending in '...' when cut."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    if not text[limit].isspace() and not head[-1].isspace():
        words = head.rsplit(maxsplit=1)
        if len(words) == 2:
            head = words[0]
    return head.rstrip() + "..."


def _listed(title: str, lines: list[str], trailer: str = "") -> str:
    shown = lines[:_MAX_LISTED]
    if len(lines) > _MAX_LISTED:
        shown.append(f"+{len(lines) - _MAX_LISTED} more")
    if trailer:
        shown.append(trailer)
    return f"{title}:\n" + "\n".join(shown) if shown else f"{title}: none"


def _goal(task: dict[str, Any]) -> str:
    goal = (task["goal"] or "").strip().splitlines()
    return f" -- {_clip(goal[0], _GOAL_MAX_CHARS)}" if goal else ""


def _active_line(task: dict[str, Any] | None, plan: dict[str, Any] | None = None) -> str:
    if task:
        return (
            f"Active: brief {task['brief']} ({task['workspace']}), plan {task['plan_id']}, "
            f"task {task['task_id']} (order {task['task_order']})" + _goal(task)
        )
    if not plan:
        return "Active: none"
    line = f"Active plan, no contract of this session bound: brief {plan['brief']} ({plan['workspace']}), plan {plan['plan_id']}"
    if plan["task_id"] is not None:
        line += f", next pending task {plan['task_id']} (order {plan['task_order']})" + _goal(plan)
    counts = plan.get("active_by_workspace") or []
    if sum(count for _, count in counts) > 1:
        line += "\nActive plans: " + ", ".join(
            f"{workspace} {count} (`gaia plan list --status active --workspace {workspace}`)"
            for workspace, count in counts
        )
    return line


def _proposals_line(snapshot: dict[str, Any]) -> str:
    count = snapshot.get("pending_proposals") or 0
    return f"Pending proposals: {count} (`{_PROPOSALS_VERB}`)" if count else ""


def _memory_line(snapshot: dict[str, Any], counts_only: bool) -> str:
    names = [row["name"] for row in snapshot.get("memory_touched") or []]
    if not names:
        return ""
    if counts_only:
        return f"Memory touched this session: {len(names)}"
    more = f", +{len(names) - _MAX_LISTED} more" if len(names) > _MAX_LISTED else ""
    return "Memory touched this session: " + ", ".join(names[:_MAX_LISTED]) + more


def _age(written_at: str) -> str:
    seconds = time.time() - datetime.strptime(written_at, _ISO).replace(tzinfo=timezone.utc).timestamp()
    if seconds < 3600:
        return f"{max(int(seconds // 60), 0)}m"
    if seconds < 48 * 3600:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _note_age(snapshot: dict[str, Any]) -> str:
    written = snapshot.get("resume_point_written_at")
    if not written:
        return ""
    newest = snapshot.get("newest_contract_at")
    stale = f" -- STALE: this session has a newer contract ({newest})" if newest and written < newest else ""
    return f"written {_age(written)} ago ({written}){stale}"


def _failed(snapshot: dict[str, Any], key: str) -> bool:
    return key in snapshot.get("unavailable", ())


def _list_sections(snapshot: dict[str, Any]) -> list[str]:
    quiet = len(snapshot.get("older_unbound", []))
    waiting_first = sorted(snapshot["open_contracts"], key=lambda c: c["agent_state"] not in _WAITING_STATES)
    return [
        f"Pending signatures: {_UNAVAILABLE}" if _failed(snapshot, "pending_signatures") else _listed("Pending signatures", [
            f"- {a['id']} (agent {a['agent_id'] or 'unknown'}, {a['created_at']})"
            for a in snapshot["pending_signatures"]
        ]),
        f"Open contracts: {_UNAVAILABLE}" if _failed(snapshot, "open_contracts") else _listed("Open contracts", [
            f"- {c['contract_id'] or c['agent_id']} [{c['agent_state']}]"
            + (f" kind={c['kind']}" if c["kind"] else "")
            + (f" task={c['plan_task_id']}" if c.get("plan_task_id") else "")
            for c in waiting_first
        ], trailer=(
            f"+{quiet} unbound, quiet for 24h (`gaia session snapshot --session-id {snapshot['session_id']} --json`)"
            if quiet else ""
        )),
    ]


def _count_sections(snapshot: dict[str, Any]) -> list[str]:
    quiet = len(snapshot.get("older_unbound", []))

    def count(key: str) -> str:
        return _UNAVAILABLE if _failed(snapshot, key) else str(len(snapshot[key]))

    return [
        f"Pending signatures: {count('pending_signatures')}\n"
        f"Open contracts: {count('open_contracts')}"
        + (f" (+{quiet} unbound, quiet for 24h)" if quiet else "")
        + f"\nList them with `gaia session snapshot --session-id {snapshot['session_id']}`"
    ]


def _cap(block: str, resume: str | None) -> str:
    limit = _BLOCK_MAX_CHARS + len(resume or "")
    if len(block) > limit:
        block = block[: limit - len(_TRUNCATED)].rsplit("\n", 1)[0] + _TRUNCATED
    return block


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """The markdown block `gaia session snapshot` prints: every section, 'none' when empty, the resume point whole, and at most 1500 further characters cut on line boundaries."""
    resume = snapshot["resume_point"]
    sections = [
        _active_line(snapshot["active_task"], snapshot.get("active_plan"))
        + f"\nPending proposals: {snapshot.get('pending_proposals') or 0}"
        + (f"\nResume point {_note_age(snapshot)}" if resume else ""),
        "Resume point:\n" + resume if resume else "Resume point: none",
        *_list_sections(snapshot),
        _memory_line(snapshot, counts_only=False) or "Memory touched this session: none",
    ]
    return _cap("## Session Snapshot\n" + "\n\n".join(sections), resume)


def render_resume_section(snapshot: dict[str, Any], *, counts_only: bool = False) -> str:
    """The birth block's resume section, derived lines first and the hand note whole with its age; "" when nothing is derived and no note exists."""
    resume = snapshot["resume_point"]
    has_lists = (
        snapshot["pending_signatures"] or snapshot["open_contracts"] or snapshot.get("older_unbound")
        or _failed(snapshot, "pending_signatures") or _failed(snapshot, "open_contracts")
    )
    active = snapshot["active_task"] or snapshot.get("active_plan")
    head = "\n".join(filter(None, [
        _active_line(snapshot["active_task"], snapshot.get("active_plan")) if active else "",
        _proposals_line(snapshot),
    ]))
    sections = [
        head,
        f"Hand note, {_note_age(snapshot)}:\n{resume}" if resume else "",
        *((_count_sections(snapshot) if counts_only else _list_sections(snapshot)) if has_lists else []),
        _memory_line(snapshot, counts_only),
    ]
    body = "\n\n".join(s for s in sections if s)
    return _cap("## Resume point\n" + body, resume) if body else ""
