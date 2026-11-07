"""
Dispatch lifecycle -- host-neutral facades over the dispatch row: the claim
-> stamp -> render sequence for a starting subagent turn
(``claim_dispatch_kernel``), the reaper of rows with no liveness evidence
(``reap_stale_turn``) and the close of a row from a session signal
(``resolve_close``). The claim ladder itself lives in ``claim_dispatch_row``;
``claim_dispatch_kernel`` only forwards what it is given.

This is the CLAIM seam, not the birth one (see ``dispatch_binding``): the
nascent ``agent_contract_handoffs`` row was already born earlier in the
turn's dispatch. When a host reports that a dispatched turn has actually
started, the host holds its own per-run identifier for that turn while the
born row only knows the CLI-minted ``contract_id`` -- the claim is where the
two identifier spaces first meet, which is also where the recovery join used
after an unfinalized cut is stamped.

The claim is kept free of host-specific vocabulary on purpose: a caller
translates its own event shape into the five neutral arguments below and
reads back either the rendered kernel text or ``None``. That translation -- and everything that
knows what a particular host's start event looks like -- stays entirely on
the caller's side.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def claim_dispatch_kernel(
    *,
    agent_name: Optional[str],
    dispatch_prompt_id: Optional[str],
    dispatch_description: Optional[str],
    host_agent_id: Optional[str],
    dispatch_tool_use_id: Optional[str] = None,
) -> Optional[str]:
    """Claim the born row a turn-start correlates to, stamp it, render its kernel.

    ``dispatch_prompt_id`` / ``dispatch_description`` are the correlation keys
    against the row's own columns of the same name, scoped by ``agent_name``;
    ``dispatch_tool_use_id`` is the exact-callID layer 0 key, forwarded
    unchanged; a caller with no such coordinate omits it.
    ``claim_dispatch_row`` owns the ladder and the divergent-signature guard.

    ``host_agent_id`` is stamped onto the claimed row as its
    ``harness_agent_id`` right after the claim resolves, because the claim is
    where both identifier spaces first meet: the row carries the CLI-minted
    ``contract_id`` and the caller carries the host-assigned run id. A later
    stop-style seam cannot substitute for this one: it never fires on a
    harness cut. The stamp runs before rendering so a render failure can never
    lose it; both the stamp and the render are best-effort and never block the
    start.

    Returns the joined kernel blocks, or ``None`` when nothing was claimed or
    rendering failed -- callers read ``None`` as "keep the legacy path",
    never as an error.
    """
    try:
        from gaia.store.writer import claim_dispatch_row, stamp_harness_agent_id
        from ..context.kernel_builder import build_kernel_context

        row = claim_dispatch_row(
            agent_name=agent_name or None,
            dispatch_prompt_id=dispatch_prompt_id or None,
            dispatch_description=dispatch_description or None,
            dispatch_tool_use_id=dispatch_tool_use_id or None,
        )
        if row is None:
            return None
        try:
            _stamp = stamp_harness_agent_id(
                row.get("contract_id") or None,
                host_agent_id or None,
            )
            if _stamp.get("status") == "applied":
                logger.info(
                    "Harness agent id stamped: contract_id=%s harness_agent_id=%s",
                    row.get("contract_id"), host_agent_id,
                )
        except Exception as exc:
            logger.debug("Harness agent id stamp failed (non-fatal): %s", exc)
        kernel = build_kernel_context(row, agent_name=agent_name)
        if kernel:
            logger.info(
                "Dispatch kernel injected (contract_id=%s, agent=%s)",
                row.get("contract_id"), agent_name or "unknown",
            )
        return kernel
    except Exception as exc:
        logger.debug("dispatch claim/kernel failed (non-fatal): %s", exc)
        return None


def reap_stale_turn(
    *,
    older_than_seconds: int,
    db_path=None,
    spare_sessions=frozenset(),
) -> dict:
    """Promote every DISPATCHED row whose host shows no liveness evidence.

    Host-neutral facade: the promotion itself is
    ``gaia.store.writer.reap_stale_dispatched_handoffs``, which knows only a
    row's age and an injected liveness predicate -- nothing about attestation
    ledgers or host runs. This function supplies that predicate from the ONE
    host-specific liveness source Gaia has today
    (``modules.security.host_attestation``), scoped by the row's OWN
    ``session_id`` column: a row cannot name its own host run, so the check
    asks whether ANY ledger still vouches for that session.

    A row is spared when its harness agent recorded a tool call inside the
    window, or a ledger modified inside it names the row's ``session_id``;
    neither is read as death, as a SIGTERM'd host leaves no other trace. A row
    of a session in ``spare_sessions`` is never reaped: its host is known alive.
    """
    from gaia.store.writer import (
        _connect,
        _stale_cutoff_iso,
        harness_agents_active_since,
        reap_stale_dispatched_handoffs,
    )
    from ..security.host_attestation import ledger_dir, ATTESTATION_SCHEME

    con = _connect(db_path)
    try:
        active = harness_agents_active_since(con, _stale_cutoff_iso(older_than_seconds, None))
    finally:
        con.close()

    def _is_alive(row: dict) -> bool:
        return (
            row.get("session_id") in spare_sessions
            or row.get("harness_agent_id") in active
            or _session_has_live_attestation(row)
        )

    def _session_has_live_attestation(row: dict) -> bool:
        session_id = row.get("session_id")
        if not session_id:
            return False
        try:
            entries = list(ledger_dir().glob("*.json"))
        except OSError:
            return False
        import json as _json
        import os as _os
        import time as _time

        cutoff = _time.time() - older_than_seconds
        for path in entries:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                raw = _json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            records = raw.get("records") if isinstance(raw, dict) else None
            if not isinstance(records, dict):
                continue
            for token, value in records.items():
                if (
                    isinstance(value, dict)
                    and value.get("session_id") == session_id
                    and token.startswith(ATTESTATION_SCHEME)
                ):
                    return True
        return False

    return reap_stale_dispatched_handoffs(
        older_than_seconds=older_than_seconds,
        liveness_check=_is_alive,
        db_path=db_path,
    )


def resolve_close(
    *,
    harness_agent_id: Optional[str],
    session_id: Optional[str] = None,
    db_path=None,
) -> Optional[dict]:
    """Promote the row bound to this harness session per its own persisted draft.

    Host-neutral facade over the SAME two-way split Claude Code's own
    SubagentStop backstop makes (``hooks.modules.agents.handoff_persister.
    persist_handoff``), reached here from a session-level signal (idle,
    error, deleted) instead of a transcript. A draft that already declares a
    valid CLOSED_TURN_PLAN_STATUSES verdict closes CLEANLY -- the same state
    verbatim, cut_reason cleared. Any other draft (absent, unreadable, still
    IN_PROGRESS) closes CUT, with ``CUT_REASON_BACKSTOP_CAPTURE``.

    Idempotent by this function's own check: a row already off 'DISPATCHED'
    -- clean or cut, by this facade or the agent's own finalize -- is read as
    already closed and left untouched, so a second lifecycle signal for the
    SAME session (idle firing after error, in any order) cannot close it
    twice.

    No row bound to this harness_agent_id -- the ordinary shape of the
    PRIMARY/root session, never bound by ``bind_harness_child_session``, or
    of a dispatched child whose Task PreToolUse never landed -- resolves to
    ``{"status": "no_row"}``: nothing to close, not a violation.

    Returns ``{"status": "no_row"|"already_closed"|"closed", "contract_id":
    ..., "agent_state": ..., "cut_reason": ...}`` (the last two omitted for
    "no_row"/"already_closed"). Never raises: an unavailable store or an
    unreadable draft degrades to the CUT branch or to ``{"status": "error"}``
    rather than interrupting the session lifecycle event that triggered it.
    """
    if not harness_agent_id:
        return {"status": "no_row"}
    try:
        import json as _json

        from gaia.contract.drafts import load_draft
        from gaia.state import CLOSED_TURN_PLAN_STATUSES, CUT_REASON_BACKSTOP_CAPTURE
        from gaia.store.writer import (
            find_dispatch_row_by_harness_agent_id,
            finalize_agent_contract_handoff,
        )

        row = find_dispatch_row_by_harness_agent_id(
            harness_agent_id, session_id=session_id, db_path=db_path,
        )
        if row is None:
            return {"status": "no_row"}
        contract_id = row.get("contract_id")
        if row.get("agent_state") != "DISPATCHED":
            return {"status": "already_closed", "contract_id": contract_id}

        try:
            draft = load_draft(contract_id)
        except Exception:
            draft = None
        agent_status = draft.get("agent_status") if isinstance(draft, dict) else None
        declared_state = (
            agent_status.get("agent_state")
            if isinstance(agent_status, dict)
            else None
        )

        if declared_state in CLOSED_TURN_PLAN_STATUSES:
            agent_state, cut_reason = declared_state, None
        else:
            agent_state, cut_reason = "IN_PROGRESS", CUT_REASON_BACKSTOP_CAPTURE

        envelope = dict(draft) if isinstance(draft, dict) else {}
        envelope["degraded"] = cut_reason is not None
        envelope["backstop"] = "opencode_session_lifecycle_close"

        outcome = finalize_agent_contract_handoff(
            contract_id=contract_id,
            agent_id=row.get("agent_id"),
            workspace=row.get("workspace"),
            agent_state=agent_state,
            raw_handoff_json=_json.dumps(envelope),
            session_id=row.get("session_id") or session_id,
            plan_task_id=row.get("plan_task_id"),
            brief_id=row.get("brief_id"),
            cut_reason=cut_reason,
            db_path=db_path,
        )
        if not outcome.get("created"):
            return {"status": "already_closed", "contract_id": contract_id}
        return {
            "status": "closed",
            "contract_id": contract_id,
            "agent_state": agent_state,
            "cut_reason": cut_reason,
        }
    except Exception as exc:
        logger.debug("resolve_close failed (non-fatal): %s", exc)
        return {"status": "error"}
