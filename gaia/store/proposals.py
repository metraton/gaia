"""Memory and project-context proposals of closed turns, pending until the orchestrator decides."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

DECISIONS = ("accepted", "dismissed")


def _hook_parsers():
    hooks_root = str(Path(__file__).resolve().parents[2] / "hooks")
    if hooks_root not in sys.path:
        sys.path.insert(0, hooks_root)
    from modules.agents.response_contract import parse_memory_delta
    from modules.context.context_writer import proposed_entries

    return parse_memory_delta, proposed_entries


_SLUG = re.compile(r"[a-z0-9][a-z0-9_.-]*")


def _unwritable(proposal: dict) -> Optional[str]:
    if proposal.get("operation") != "create":
        return None
    from gaia.store.memory_placement import MEMORY_KINDS, placement_refusal
    from gaia.store.writer import VALID_MEMORY_TYPES, _validate_curated_slug

    create = proposal.get("create") or {}
    slug, mem_type = str(create.get("slug") or ""), create.get("type")
    kind = create.get("kind")
    if mem_type not in VALID_MEMORY_TYPES:
        return f"type {mem_type!r} is not one of {list(VALID_MEMORY_TYPES)}"
    if not _SLUG.fullmatch(slug):
        return f"slug {slug!r} is not lowercase letters, digits and _ . -"
    if kind not in MEMORY_KINDS:
        return f"kind {kind!r} is required and is one of {', '.join(MEMORY_KINDS)}"
    try:
        _validate_curated_slug(slug, mem_type)
    except ValueError as exc:
        return str(exc)
    # Accepting writes a non-user proposal onto its contract's project.
    owner = "user" if mem_type == "user" else "project"
    return placement_refusal(owner, kind, create.get("until"))


def _refusal(item: dict, reason: str) -> str:
    return f"memory proposal {(item.get('create') or {}).get('slug')!r} not recorded: {reason}"


def _split_memory(envelope: Any) -> tuple:
    """``(accepted, refused)``: the envelope's memory proposals `gaia memory add` takes, and ``[(proposal, reason)]`` for the rest."""
    if not isinstance(envelope, dict):
        return [], []
    parse_memory_delta, _entries = _hook_parsers()
    accepted, refused = [], []
    for item in parse_memory_delta("", parsed_contract=envelope).proposals:
        reason = _unwritable(item)
        if reason is None:
            accepted.append(item)
        else:
            refused.append((item, reason))
    return accepted, refused


def refusal_message(envelope: Any) -> Optional[str]:
    """The write-time refusal for an envelope holding a proposal `gaia memory add` would reject, or None."""
    from gaia.store.writer import VALID_MEMORY_TYPES

    _accepted, refused = _split_memory(envelope)
    if not refused:
        return None
    lines = [f"memory_delta refused, {_refusal(item, reason)}" for item, reason in refused]
    lines.append(f"accepted types: {', '.join(VALID_MEMORY_TYPES)}")
    lines.append("fix: replace memory_delta with a corrected value (gaia contract set memory_delta '<json>')")
    return "\n".join(lines)


def extract_proposals(envelope: Any) -> list:
    """``[(kind, payload)]``: each memory proposal `gaia memory add` accepts, then each context entry; the rest go to stderr."""
    if not isinstance(envelope, dict):
        return []
    _parse, proposed_entries = _hook_parsers()
    memory, refused = _split_memory(envelope)
    for item, reason in refused:
        print(f"gaia: {_refusal(item, reason)}", file=sys.stderr)
    context, _note = proposed_entries(envelope.get("update_contracts"))
    return [("memory", item) for item in memory] + [("context", item) for item in context]


def _record_refusals(con: sqlite3.Connection, handoff_id: int, envelope: dict) -> None:
    """Add each refused memory proposal to the row's ``evidence_report.open_gaps``."""
    gaps = envelope.setdefault("evidence_report", {}).setdefault("open_gaps", [])
    new = [_refusal(item, reason) for item, reason in _split_memory(envelope)[1]]
    new = [text for text in new if text not in gaps]
    if new:
        gaps.extend(new)
        con.execute("UPDATE agent_contract_handoffs SET raw_handoff_json = ? WHERE id = ?",
                    (json.dumps(envelope), handoff_id))


def _row_project(con: sqlite3.Connection, handoff_id: int) -> Optional[str]:
    row = con.execute(
        "SELECT dispatch_project, workspace FROM agent_contract_handoffs WHERE id = ?",
        (handoff_id,),
    ).fetchone()
    if row is None or not row[0]:
        return None
    from gaia.store.project_context import resolve_project

    owner = resolve_project(con, row[0], row[1])
    return owner["identity"] if owner else row[0]


def _insert(con: sqlite3.Connection, handoff_id: int, found: list) -> int:
    if not found:
        return 0
    project = _row_project(con, handoff_id)
    inserted = 0
    for index, (kind, payload) in enumerate(found):
        cur = con.execute(
            "INSERT OR IGNORE INTO proposals (handoff_id, item_index, kind, project, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (handoff_id, index, kind, project, json.dumps(payload, sort_keys=True)),
        )
        inserted += cur.rowcount
    return inserted


def record_proposals(con: sqlite3.Connection, handoff_id: int, envelope: Any) -> int:
    """Record ``envelope``'s proposals and recurring-gap threads for ``handoff_id``; a re-close replaces only what is still pending."""
    from gaia.store.recurring_gaps import thread_candidates

    con.execute("DELETE FROM proposals WHERE handoff_id = ? AND status = 'pending'", (handoff_id,))
    threads = [("thread", payload) for payload in thread_candidates(con, handoff_id, envelope)]
    return _insert(con, handoff_id, extract_proposals(envelope) + threads)


def record_close(con: sqlite3.Connection, handoff_id: int, raw_handoff_json: str) -> None:
    """Record a closing row's proposals in the caller's transaction, never failing the close."""
    try:
        envelope = json.loads(raw_handoff_json)
    except (TypeError, ValueError):
        return
    con.execute("SAVEPOINT record_proposals")
    try:
        record_proposals(con, handoff_id, envelope)
        if isinstance(envelope, dict):
            _record_refusals(con, handoff_id, envelope)
    except Exception as exc:
        con.execute("ROLLBACK TO SAVEPOINT record_proposals")
        print(f"gaia: proposals of handoff {handoff_id} not recorded: {exc}", file=sys.stderr)
    finally:
        con.execute("RELEASE SAVEPOINT record_proposals")
    con.execute("SAVEPOINT auto_apply")
    try:
        from gaia.context_drift import auto_apply

        auto_apply(con, handoff_id, envelope)
    except Exception as exc:
        con.execute("ROLLBACK TO SAVEPOINT auto_apply")
        print(f"gaia: context proposals of handoff {handoff_id} not auto-applied: {exc}", file=sys.stderr)
    finally:
        con.execute("RELEASE SAVEPOINT auto_apply")


def _decode(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["payload"] = json.loads(item["payload"])
    return item


_SELECT = (
    "SELECT p.*, h.contract_id, h.agent_id FROM proposals p "
    "JOIN agent_contract_handoffs h ON h.id = p.handoff_id"
)


def list_proposals(
    *, status: Optional[str] = "pending", kind: Optional[str] = None,
    contract_id: Optional[str] = None, db_path: Optional[Path] = None,
) -> list:
    """Proposals oldest first, filtered by status (None for every status), kind and proposing contract."""
    from gaia.store.writer import _connect

    clauses, params = [], []
    for column, value in (("p.status", status), ("p.kind", kind), ("h.contract_id", contract_id)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    con = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        return [_decode(row) for row in con.execute(f"{_SELECT}{where} ORDER BY p.id", params)]
    finally:
        con.close()


def get_proposal(proposal_id: int, *, db_path: Optional[Path] = None) -> Optional[dict]:
    """One proposal with its proposing contract, or None."""
    from gaia.store.writer import _connect

    con = _connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        row = con.execute(f"{_SELECT} WHERE p.id = ?", (proposal_id,)).fetchone()
        return _decode(row) if row else None
    finally:
        con.close()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def decide_proposal(
    proposal_id: int, decision: str, *, decided_by: str, project: Optional[str] = None,
    note: Optional[str] = None, db_path: Optional[Path] = None,
) -> bool:
    """Mark a pending proposal accepted or dismissed; False when it is not pending."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    from gaia.store.writer import _connect

    con = _connect(db_path)
    try:
        cur = con.execute(
            "UPDATE proposals SET status = ?, project = COALESCE(?, project), decided_at = ?, "
            "decided_by = ?, decision_note = ? WHERE id = ? AND status = 'pending'",
            (decision, project, _now(), decided_by, note, proposal_id),
        )
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def _update_one(sql: str, params: tuple, db_path: Optional[Path]) -> bool:
    from gaia.store.writer import _connect

    con = _connect(db_path)
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur.rowcount == 1
    finally:
        con.close()


def note_decision(proposal_id: int, note: str, *, db_path: Optional[Path] = None) -> bool:
    """Record what a decided proposal's acceptance wrote; False when it is still pending."""
    return _update_one("UPDATE proposals SET decision_note = ? WHERE id = ? AND status != 'pending'",
                       (note, proposal_id), db_path)


def reopen_proposal(
    proposal_id: int, *, project: Optional[str], db_path: Optional[Path] = None,
) -> bool:
    """Return an accepted proposal whose write failed to pending, restoring its ``project``."""
    return _update_one(
        "UPDATE proposals SET status = 'pending', project = ?, decided_at = NULL, "
        "decided_by = NULL, decision_note = NULL WHERE id = ? AND status = 'accepted'",
        (project, proposal_id), db_path)


def backfill_proposals(
    *, since_days: int = 30, apply: bool = False, db_path: Optional[Path] = None,
) -> dict:
    """Count, or with ``apply`` insert, the proposals of rows closed in the last ``since_days``."""
    from gaia.store.writer import _connect

    cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    report = {"since": cutoff, "rows_scanned": 0, "rows_with_proposals": 0,
              "proposals": 0, "inserted": 0, "applied": apply}
    con = _connect(db_path)
    try:
        rows = con.execute(
            "SELECT id, raw_handoff_json FROM agent_contract_handoffs "
            "WHERE created_at >= ? AND agent_state != 'DISPATCHED' ORDER BY id", (cutoff,),
        ).fetchall()
        for handoff_id, raw in rows:
            report["rows_scanned"] += 1
            try:
                found = extract_proposals(json.loads(raw or "null"))
            except ValueError:
                continue
            if not found:
                continue
            report["rows_with_proposals"] += 1
            report["proposals"] += len(found)
            if apply:
                report["inserted"] += _insert(con, handoff_id, found)
        if apply:
            con.commit()
        return report
    finally:
        con.close()
