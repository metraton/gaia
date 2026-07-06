"""
Handoff persistence helper (M4 / T4.2).

Shared module used by both the production adapter path
(adapters/claude_code.py -> adapt_subagent_stop) and the legacy test-entry
path (subagent_stop.py -> subagent_stop_hook).

Moved here from subagent_stop.py to break the circular-import risk that would
arise if the adapter imported _persist_handoff directly from subagent_stop
(which itself imports from the adapter's dependency tree).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def persist_handoff(
    parsed_contract,
    agent_output: str,
    task_info: dict,
    session_id: str,
) -> None:
    """Write an agent_contract_handoffs row (and optional approvals row).

    Called synchronously inside the SubagentStop hook lifecycle.  Failures are
    suppressed so a DB write error never interrupts hook processing.

    Logic:
    1. Resolve fields from parsed_contract (preferred) or from task_info +
       agent_output (fallback when the contract is missing/malformed).
    2. Write the handoff row via writer.insert_agent_contract_handoff.
    3. If the envelope carried an approval_request with an approval_id, look
       up the grant status in approval_grants and write an approvals row.
    """
    import json as _json
    import os as _os
    import pathlib as _pl
    import sys as _sys

    agent_id = task_info.get("agent_id") or task_info.get("agent") or "unknown"

    try:
        # Prefer a sibling gaia package if installed; fall back to the repo
        # layout where gaia/ lives two levels above hooks/.
        try:
            from gaia.store import writer as _writer
        except ImportError:
            _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
            _sys.path.insert(0, str(_repo_root))
            from gaia.store import writer as _writer

        workspace = task_info.get("workspace") or _os.environ.get("GAIA_WORKSPACE") or "global"
        db_path_str = task_info.get("db_path")
        db_path = _pl.Path(db_path_str) if db_path_str else None

        # Resolve task_status and brief_id from the parsed contract envelope.
        if parsed_contract is not None:
            envelope = parsed_contract if isinstance(parsed_contract, dict) else {}
            agent_status = envelope.get("agent_status", {})
            task_status = agent_status.get("plan_status") or "COMPLETE"
            # brief_id may be carried by the envelope (extension point)
            brief_id = envelope.get("brief_id")
            if not brief_id:
                # Fallback: extract brief_id from update_contracts evidence payloads (Fase B envelope shape)
                for entry in envelope.get("update_contracts", []) or []:
                    if isinstance(entry, dict) and isinstance(entry.get("payload"), dict):
                        candidate = entry["payload"].get("brief_id")
                        if candidate:
                            brief_id = candidate
                            break
            brief_id = brief_id or None
            if isinstance(brief_id, str):
                try:
                    brief_id = int(brief_id)
                except (ValueError, TypeError):
                    brief_id = None
            raw_handoff_json = _json.dumps(envelope)
        else:
            # Fallback: no parsed contract available
            task_status = "COMPLETE"
            brief_id = None
            raw_handoff_json = _json.dumps({
                "fallback": True,
                "agent_output_preview": agent_output[:200] if agent_output else "",
            })

        handoff_id = _writer.insert_agent_contract_handoff(
            agent_id=agent_id,
            workspace=workspace,
            task_status=task_status,
            raw_handoff_json=raw_handoff_json,
            session_id=session_id,
            brief_id=brief_id,
            db_path=db_path,
        )

        # If the envelope had an approval_request, record the approval decision.
        if parsed_contract is not None:
            envelope = parsed_contract if isinstance(parsed_contract, dict) else {}
            approval_req = envelope.get("approval_request")
            if approval_req and isinstance(approval_req, dict):
                # The approval_id is the one the subagent relayed (the hook-block
                # / singular path, or a compound-command COMMAND_SET minted by
                # bash_validator). It points at the pending row the
                # handoff_approvals audit row should link to.
                approval_id = approval_req.get("approval_id")

                if approval_id:
                    # Look up the grant to determine the decision at stop time.
                    try:
                        grants = _writer.list_approval_grants(
                            session_id=session_id,
                        )
                        decision = "APPROVED"
                        decided_at_val = _writer._now_iso()
                        for g in grants:
                            if g.get("approval_id") == approval_id:
                                grant_status = g.get("status", "PENDING")
                                if grant_status == "CONSUMED":
                                    decision = "APPROVED"
                                elif grant_status == "REVOKED":
                                    decision = "REVOKED"
                                elif grant_status == "EXPIRED":
                                    decision = "EXPIRED"
                                else:
                                    decision = "APPROVED"  # PENDING treated as granted
                                decided_at_val = (
                                    g.get("consumed_at")
                                    or g.get("revoked_at")
                                    or decided_at_val
                                )
                                break

                        _writer.insert_handoff_approval(
                            handoff_id=handoff_id,
                            approval_id=approval_id,
                            decision=decision,
                            decided_at=decided_at_val,
                            db_path=db_path,
                        )
                    except Exception as _approval_exc:
                        logger.warning(
                            "M4: approval row write failed for handoff_id=%s: %s",
                            handoff_id, _approval_exc,
                        )

    except Exception as _exc:
        logger.error(
            "M4: handoff persistence failed (non-blocking): %s",
            _exc, exc_info=True,
        )
