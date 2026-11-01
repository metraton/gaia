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
_WAITING_STATES = frozenset({"NEEDS_INPUT", "APPROVAL_REQUEST", "BLOCKED"})
_RUNNING_STATES = frozenset({"DISPATCHED", "IN_PROGRESS"})
_BIRTH_CUT = "never_finalized"
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


_CHAIN_COLUMNS = (
    "h.id, h.contract_id, h.agent_id, h.agent_state, h.cut_reason, h.dispatch_description, "
    "h.harness_agent_id, h.created_at, "
    "MAX(h.created_at, COALESCE(h.claimed_at, h.created_at)) AS last_activity, "
    "h.continues_handoff_id, COALESCE(h.plan_task_id, parent.plan_task_id) AS plan_task_id, "
    "t.status AS task_status, "
    "CASE WHEN json_valid(h.raw_handoff_json) "
    "THEN json_extract(h.raw_handoff_json, '$.approval_request.approval_id') END AS approval_id "
    "FROM agent_contract_handoffs h "
    "LEFT JOIN agent_contract_handoffs parent ON parent.id = h.parent_handoff_id "
    "LEFT JOIN tasks t ON t.id = COALESCE(h.plan_task_id, parent.plan_task_id) "
)


def _ago(seconds: float) -> str:
    return time.strftime(_ISO, time.gmtime(time.time() - seconds))


def _chain(row: dict[str, Any], by_id: dict[int, dict[str, Any]], con) -> list[dict[str, Any]]:
    """*row* and every link it continues, newest first; a link outside the session is read by id."""
    links, seen = [row], {row["id"]}
    parent = row["continues_handoff_id"]
    while parent is not None and parent not in seen:
        link = by_id.get(parent)
        if link is None:
            found = con.execute(f"SELECT {_CHAIN_COLUMNS}WHERE h.id = ?", (parent,)).fetchone()
            if found is None:
                break
            link = dict(found)
        seen.add(parent)
        links.append(link)
        parent = link["continues_handoff_id"]
    return links


def _open_contracts(con, session_id: str) -> list[dict[str, Any]]:
    """This session's open chains, newest first, each its latest link titled by the chain's dispatch description.

    An unbound link stays open only while it runs or waits; an unbound
    NEEDS_VERIFICATION or a cut turn is closed here and left to ``gaia contract list``.
    """
    from gaia.approvals.reading import pending_ttl
    from gaia.store.writer import STALE_TURN_SECONDS, harness_agents_active_since

    rows = [dict(r) for r in con.execute(f"SELECT {_CHAIN_COLUMNS}WHERE h.session_id = ? ORDER BY h.id", (session_id,))]
    by_id = {r["id"]: r for r in rows}
    continued = {r["continues_handoff_id"] for r in rows if r["continues_handoff_id"] is not None}
    latest_of_task = {r["plan_task_id"]: r["id"] for r in rows if r["plan_task_id"] is not None}
    running_cutoff, waiting_cutoff = _ago(STALE_TURN_SECONDS), _ago(pending_ttl().total_seconds())
    active: set | None = None
    open_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["id"] in continued or row["agent_state"] == "COMPLETE":
            continue
        chain = _chain(row, by_id, con)
        if row["plan_task_id"] is not None:
            listed = row["task_status"] == "pending" and latest_of_task[row["plan_task_id"]] == row["id"]
        elif row["agent_state"] in _RUNNING_STATES and row["cut_reason"] in (None, _BIRTH_CUT):
            if active is None:
                active = harness_agents_active_since(con, running_cutoff)
            harness = next((link["harness_agent_id"] for link in chain if link["harness_agent_id"]), None)
            listed = harness in active or (row["last_activity"] or "") >= running_cutoff
        else:
            listed = (row["agent_state"] in _WAITING_STATES and row["cut_reason"] is None
                      and (row["last_activity"] or "") >= waiting_cutoff)
        if listed:
            title = next((link["dispatch_description"] for link in chain if (link["dispatch_description"] or "").strip()), "")
            open_rows.append({**row, "title": title.strip(), "chain_contract_ids": [link["contract_id"] for link in chain]})
    return sorted(open_rows, key=lambda r: r["id"], reverse=True)
