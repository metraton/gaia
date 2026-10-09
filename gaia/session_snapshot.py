"""Host-neutral, session-scoped snapshot of the work a compaction must not lose.

One session's open contracts, pending signatures, active brief/plan/task and
the orchestrator's last written resume point, rendered for both hosts'
compaction deliveries and printed by ``gaia session snapshot``.
"""

from __future__ import annotations

import re
from typing import Any

from gaia.paths.resolver import data_dir

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_RESUME_DIR = "session_resume"
_EMPTY_BLOCK = (
    "## Session Snapshot\n"
    "No open contracts, pending signatures, active task or resume point is "
    "recorded for this session."
)


def _resume_path(session_id: str):
    if not _SESSION_ID_RE.match(session_id or ""):
        raise ValueError(f"invalid session id: {session_id!r}")
    return data_dir() / _RESUME_DIR / f"{session_id}.txt"


def write_resume_point(session_id: str, text: str) -> str:
    """Replace the session's resume point with *text* and return the file written."""
    path = _resume_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return str(path)


def read_resume_point(session_id: str) -> str | None:
    """The session's last written resume point, or None when it has none."""
    try:
        text = _resume_path(session_id).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return text or None


def _open_contracts(con, session_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT contract_id, agent_id, agent_state, kind, plan_task_id "
        "FROM agent_contract_handoffs "
        "WHERE session_id = ? AND agent_state <> 'COMPLETE' ORDER BY id",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _pending_signatures(con, session_id: str) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT id, agent_id, created_at FROM approvals "
        "WHERE session_id = ? AND status = 'pending' ORDER BY created_at",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _active_task(con, session_id: str) -> dict[str, Any] | None:
    """The plan task of this session's latest task-bound contract.

    briefs, plans and tasks carry no session key, so the binding comes from
    the contracts' plan_task_id.
    """
    row = con.execute(
        "SELECT b.name AS brief, b.workspace AS workspace, p.id AS plan_id, "
        "t.id AS task_id, t.order_num AS task_order, t.goal AS goal "
        "FROM agent_contract_handoffs h "
        "JOIN tasks t ON t.id = h.plan_task_id "
        "JOIN plans p ON p.id = t.plan_id "
        "JOIN briefs b ON b.id = p.brief_id "
        "WHERE h.session_id = ? ORDER BY h.id DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def build_snapshot(session_id: str) -> dict[str, Any]:
    """The snapshot of *session_id*; a source that fails contributes its empty value."""
    snapshot: dict[str, Any] = {
        "session_id": session_id,
        "open_contracts": [],
        "pending_signatures": [],
        "active_task": None,
        "resume_point": read_resume_point(session_id),
    }
    if not session_id:
        return snapshot
    try:
        from gaia.store.reader import _connect

        con = _connect()
    except Exception:
        return snapshot
    try:
        for key, query in (
            ("open_contracts", _open_contracts),
            ("pending_signatures", _pending_signatures),
            ("active_task", _active_task),
        ):
            try:
                snapshot[key] = query(con, session_id)
            except Exception:
                pass
    finally:
        con.close()
    return snapshot


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """The markdown block a compaction delivers; a minimal block when the snapshot is empty."""
    sections: list[str] = []
    task = snapshot["active_task"]
    if task:
        goal = (task["goal"] or "").strip().splitlines()
        sections.append(
            f"Active: brief {task['brief']} ({task['workspace']}), plan {task['plan_id']}, "
            f"task {task['task_id']} (order {task['task_order']})"
            + (f" -- {goal[0][:160]}" if goal else "")
        )
    if snapshot["open_contracts"]:
        lines = [
            f"- {c['contract_id'] or c['agent_id']} [{c['agent_state']}]"
            + (f" kind={c['kind']}" if c["kind"] else "")
            for c in snapshot["open_contracts"]
        ]
        sections.append("Open contracts:\n" + "\n".join(lines))
    if snapshot["pending_signatures"]:
        lines = [
            f"- {a['id']} (agent {a['agent_id'] or 'unknown'}, {a['created_at']})"
            for a in snapshot["pending_signatures"]
        ]
        sections.append("Pending signatures:\n" + "\n".join(lines))
    if snapshot["resume_point"]:
        sections.append("Resume point:\n" + snapshot["resume_point"])
    if not sections:
        return _EMPTY_BLOCK
    return "## Session Snapshot\n" + "\n\n".join(sections)
