"""Open gaps that recur across a project's clean contract rows, proposed as threads."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Optional

RECURRENCE_THRESHOLD = 2
RECENT_ROWS = 200
SAME_GAP_SIMILARITY = 0.75

_STOPWORDS = frozenset(
    "a an and are as at be by does do for from in into is it its no not of on or the "
    "this that to was with yet still".split())
_INFLECTION = re.compile(r"(?<=\w{3})(ing|ed|es|e|(?<!s)s)$")


def gap_tokens(text: str) -> frozenset:
    """Content words of ``text``, whole and without inflection, so wire, wired and wiring are one."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(_INFLECTION.sub("", word) for word in words if word not in _STOPWORDS)


def same_gap(left: frozenset, right: frozenset) -> bool:
    """Whether two token sets name one gap: their shared words are most of all the words either uses."""
    return bool(left | right) and len(left & right) / len(left | right) >= SAME_GAP_SIMILARITY


def _open_gaps(envelope: Any) -> list:
    if not isinstance(envelope, dict):
        return []
    gaps = (envelope.get("evidence_report") or {}).get("open_gaps") or []
    return [gap for gap in gaps if isinstance(gap, str) and gap.strip()]


def _clean_rows(con: sqlite3.Connection, handoff_id: int, workspace: str,
                project: Optional[str]) -> list:
    rows = con.execute(
        "SELECT id, raw_handoff_json FROM agent_contract_handoffs "
        "WHERE workspace = ? AND dispatch_project IS ? AND cut_reason IS NULL "
        "AND agent_state != 'DISPATCHED' AND id != ? ORDER BY id DESC LIMIT ?",
        (workspace, project, handoff_id, RECENT_ROWS),
    ).fetchall()
    found = []
    for row_id, raw in rows:
        try:
            gaps = _open_gaps(json.loads(raw or "null"))
        except ValueError:
            continue
        found.append((row_id, [gap_tokens(gap) for gap in gaps]))
    return found


def _already_held(con: sqlite3.Connection, workspace: str, handoff_id: int) -> list:
    threads = con.execute(
        "SELECT COALESCE(description, '') || ' ' || name FROM memory "
        "WHERE workspace = ? AND class = 'thread' AND deleted_at IS NULL", (workspace,),
    ).fetchall()
    proposed = con.execute(
        "SELECT payload FROM proposals WHERE kind = 'thread' AND handoff_id != ?", (handoff_id,),
    ).fetchall()
    return ([gap_tokens(text) for (text,) in threads]
            + [gap_tokens(json.loads(payload).get("gap", "")) for (payload,) in proposed])


def thread_candidates(con: sqlite3.Connection, handoff_id: int, envelope: Any) -> list:
    """Thread payloads for each open gap of a clean row that ``RECURRENCE_THRESHOLD`` clean rows share.

    A gap already held by a thread row, or by another row's thread proposal in any status,
    is not proposed again; a cut row neither proposes nor counts.
    """
    row = con.execute(
        "SELECT workspace, dispatch_project, cut_reason FROM agent_contract_handoffs WHERE id = ?",
        (handoff_id,),
    ).fetchone()
    gaps = _open_gaps(envelope)
    if row is None or row[2] is not None or not gaps:
        return []
    workspace, project = row[0], row[1]
    history = _clean_rows(con, handoff_id, workspace, project)
    held = _already_held(con, workspace, handoff_id)
    candidates = []
    for gap in gaps:
        tokens = gap_tokens(gap)
        if any(same_gap(tokens, other) for other in held):
            continue
        matching = [row_id for row_id, row_gaps in history
                    if any(same_gap(tokens, other) for other in row_gaps)]
        if len(matching) + 1 < RECURRENCE_THRESHOLD:
            continue
        candidates.append({"gap": gap, "handoff_ids": sorted(matching + [handoff_id])})
        held.append(tokens)
    return candidates
