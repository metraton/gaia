"""The orchestrator's hand note for one session, and which of that session's contracts are still open."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from gaia.paths.resolver import data_dir

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_RESUME_DIR = "session_resume"
_RESUME_MAX_AGE_SECONDS = 30 * 24 * 3600
RESUME_POINT_MAX_CHARS = 2000
_UNBOUND_WINDOW_SECONDS = 24 * 3600
_WAITING_STATES = frozenset({"NEEDS_INPUT", "APPROVAL_REQUEST", "BLOCKED"})
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _resume_path(session_id: str):
    if not _SESSION_ID_RE.match(session_id or ""):
        raise ValueError(f"invalid session id: {session_id!r}")
    return data_dir() / _RESUME_DIR / f"{session_id}.json"


def _prune_resume_points(directory, keep) -> None:
    cutoff = time.time() - _RESUME_MAX_AGE_SECONDS
    for stale in [*directory.glob("*.json"), *directory.glob("*.txt")]:
        try:
            if stale != keep and stale.stat().st_mtime < cutoff:
                stale.unlink()
        except OSError:
            continue


def write_resume_point(session_id: str, text: str, written_by: str = "") -> str:
    """Replace *session_id*'s hand note (at most 2000 characters) and return the file; a *written_by* other than *session_id* is refused."""
    text = text.strip()
    if len(text) > RESUME_POINT_MAX_CHARS:
        raise ValueError(f"resume point is {len(text)} characters; the limit is {RESUME_POINT_MAX_CHARS}")
    if written_by and written_by != session_id:
        raise ValueError(f"session {written_by} cannot write the resume point of session {session_id}")
    path = _resume_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"text": text, "written_by": written_by or session_id}) + "\n", encoding="utf-8")
    _prune_resume_points(path.parent, path)
    return str(path)


def read_resume_point(session_id: str) -> str | None:
    """The hand note *session_id* itself wrote, or None when it has none or the stored writer is another session."""
    try:
        stored = json.loads(_resume_path(session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict) or stored.get("written_by") != session_id:
        return None
    return str(stored.get("text") or "").strip() or None


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
            "SELECT h.id, h.contract_id, h.agent_id, h.agent_state, h.kind, h.dispatch_description, "
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
