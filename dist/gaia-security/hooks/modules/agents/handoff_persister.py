"""
Handoff persistence helper (M4 / T4.2).

Shared module used by both the production adapter path
(adapters/claude_code.py -> adapt_subagent_stop) and the legacy test-entry
path (subagent_stop.py -> subagent_stop_hook).

Moved here from subagent_stop.py to break the circular-import risk that would
arise if the adapter imported _persist_handoff directly from subagent_stop
(which itself imports from the adapter's dependency tree).
"""

import logging

logger = logging.getLogger(__name__)


def _normalize_command_set(raw) -> list:
    """Coerce a raw ``command_set`` into the canonical ``[{command, rationale}]``.

    Mirrors the normalization in ``bash_validator._build_sealed_payload`` and
    ``approval_grants.activate_db_pending_by_prefix`` so the intake writes the
    exact shape the activation/consume sides expect. Items without a non-empty
    ``command`` are dropped; ``rationale`` defaults to "".
    """
    out: list = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("command"):
                out.append(
                    {
                        "command": item["command"],
                        "rationale": item.get("rationale", ""),
                    }
                )
    return out


def _filter_mutative_command_set(items: list) -> list:
    """Keep only the command_set items whose command is mutative/T3.

    The consume side (``bash_validator._validate_single_command``) gates the
    whole COMMAND_SET match path on ``detect_mutative_command(command).is_mutative``:
    a command that the matcher does not see as mutative NEVER reaches
    ``match_command_set_grant`` and its index is therefore NEVER consumed. If
    such a command is included in the grant's ``command_set``, ``len(consumed)``
    can never reach ``len(command_set)`` and the grant is stuck PENDING forever
    (it never flips to CONSUMED). To stay in lockstep with the consume gate, the
    intake filters with the EXACT same predicate, dropping non-mutative commands
    (e.g. ``touch``, ``ls``, ``cat``) before the grant is ever minted.

    Items that fail to classify (import error, unexpected exception) are kept --
    failing open here is safer than silently dropping a command from a consent
    batch the user is about to approve.
    """
    try:
        from modules.security.mutative_verbs import detect_mutative_command
    except ImportError:
        import pathlib as _pl
        import sys as _sys

        _hooks_root = _pl.Path(__file__).resolve().parent.parent.parent
        _sys.path.insert(0, str(_hooks_root))
        from modules.security.mutative_verbs import detect_mutative_command

    kept: list = []
    for item in items:
        command = item.get("command", "")
        try:
            if detect_mutative_command(command).is_mutative:
                kept.append(item)
        except Exception:
            # Fail open: if classification raises, keep the item rather than
            # silently dropping a command from the user's consent batch.
            kept.append(item)
    return kept


def _intake_command_set_pending(
    approval_req: dict,
    *,
    agent_id,
    session_id: str,
) -> str | None:
    """INTAKE bridge: plan-first COMMAND_SET envelope -> ONE pending row.

    When a subagent emits an ``APPROVAL_REQUEST`` whose ``approval_request``
    carries a ``command_set`` of >= 2 ``{command, rationale}`` items and NO
    ``approval_id`` (plan-first: the batch is declared up-front, before any
    command was attempted/blocked), this persists exactly ONE pending approval
    whose ``payload_json`` contains the ``command_set`` key. That is the signal
    ``activate_db_pending_by_prefix`` reads (Step 3b) to branch into
    ``create_command_set_grant`` on user approval.

    Mutative filtering (Thread a): the command_set is first reduced to ONLY the
    commands the consume side will treat as mutative/T3 -- see
    ``_filter_mutative_command_set``. Non-mutative commands (``touch``, ``ls``,
    ...) never reach the bash_validator matcher, so leaving them in the grant
    would strand its ``consumed_indexes_json`` short of completion and pin the
    grant at PENDING forever. After filtering:

      * >= 2 mutative items  -> mint the COMMAND_SET over exactly those items.
      * exactly 1 mutative   -> NOT a batch. Return None; the caller falls
        through to the singular ``approval_id`` path and the lone command is
        gated by the normal hook-block / SCOPE_SEMANTIC_SIGNATURE flow when the
        agent attempts it. We deliberately do NOT degrade-to-singular here: this
        function's contract is "mint a COMMAND_SET or stand aside", and the
        singular flow is owned end-to-end by the hook block path -- minting a
        singular row from here would duplicate that ownership.
      * 0 mutative           -> nothing to approve. Return None (no pending).

    A raw ``command_set`` of <= 1 item is likewise not a batch and returns None
    before filtering, preserving the original contract (never mint for one
    command, never degrade a batch the other way) and the working plan-first
    flow for genuine multi-command mutative batches.

    Returns the minted ``approval_id`` (``P-{uuid4hex}``) on success, or None
    when this is not a plan-first command_set envelope (no action taken).
    """
    if not isinstance(approval_req, dict):
        return None
    # Plan-first is defined by command_set present AND no approval_id. A request
    # that already carries an approval_id was minted by the hook block path; it
    # is the singular flow and must not be re-intaken here.
    if approval_req.get("approval_id"):
        return None

    raw_items = _normalize_command_set(approval_req.get("command_set"))
    if len(raw_items) < 2:
        # 0 or 1 item: not a batch. Singular path owns it.
        return None

    # Reduce to the mutative/T3 commands only -- the exact predicate the consume
    # side uses to decide whether a command reaches the COMMAND_SET matcher.
    command_set_items = _filter_mutative_command_set(raw_items)
    if len(command_set_items) < 2:
        # After filtering there is no batch left: either every command was
        # non-mutative (0 -> nothing to approve) or just one mutative command
        # remained (1 -> singular path owns it). Either way, no COMMAND_SET.
        logger.info(
            "INTAKE: command_set not minted -- %d/%d items mutative after filter "
            "(need >= 2 for a batch)",
            len(command_set_items), len(raw_items),
        )
        return None

    # Build a sealed_payload that mirrors bash_validator._build_sealed_payload's
    # COMMAND_SET shape: command_set verbatim + commands listing every string.
    # Carry through the subagent's operation/risk fields when present so the
    # orchestrator's presentation has real values, falling back to neutral
    # COMMAND_SET defaults otherwise.
    first_command = command_set_items[0]["command"]
    sealed_payload = {
        "operation": approval_req.get("operation")
        or f"COMMAND_SET intercepted: {len(command_set_items)} commands under one consent",
        "exact_content": approval_req.get("exact_content") or first_command,
        "scope": approval_req.get("scope")
        or (first_command.split()[0] if first_command.strip() else "unknown"),
        "risk_level": approval_req.get("risk_level") or "medium",
        "rollback_hint": approval_req.get("rollback") or approval_req.get("rollback_hint"),
        "rationale": approval_req.get("rationale")
        or (
            f"A batch of {len(command_set_items)} related T3 commands requires user "
            "approval under one consent per the COMMAND_SET policy."
        ),
        "commands": [it["command"] for it in command_set_items],
        "command_set": command_set_items,
    }

    try:
        from gaia.approvals.store import insert_requested
    except ImportError:
        import pathlib as _pl
        import sys as _sys

        _repo_root = _pl.Path(__file__).resolve().parent.parent.parent.parent
        _sys.path.insert(0, str(_repo_root))
        from gaia.approvals.store import insert_requested

    approval_id = insert_requested(
        sealed_payload,
        agent_id=agent_id,
        session_id=session_id or None,
    )
    logger.info(
        "INTAKE: plan-first COMMAND_SET pending created approval_id=%s items=%d",
        (approval_id or "")[:16], len(command_set_items),
    )
    return approval_id


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

    # ---------------------------------------------------------------------
    # INTAKE bridge (plan-first COMMAND_SET) -- run FIRST and INDEPENDENTLY.
    #
    # Minting the pending COMMAND_SET approval is the security-critical path:
    # it is the consent the user must act on. It must not be coupled to the
    # audit handoff-row write below -- if insert_agent_contract_handoff fails
    # for any reason, the user must still get the approval to review. So the
    # intake runs in its own isolated try, before the handoff-row write.
    #
    # Only plan-first envelopes act here: command_set >= 2 items AND no
    # approval_id. A <= 1 item set or a request that already carries an
    # approval_id (hook-block / singular path) is a no-op for the intake.
    # ---------------------------------------------------------------------
    minted_command_set_id = None
    if parsed_contract is not None:
        _env = parsed_contract if isinstance(parsed_contract, dict) else {}
        _approval_req = _env.get("approval_request")
        if isinstance(_approval_req, dict):
            try:
                minted_command_set_id = _intake_command_set_pending(
                    _approval_req,
                    agent_id=agent_id,
                    session_id=session_id,
                )
            except Exception as _intake_exc:
                logger.warning(
                    "M4: COMMAND_SET intake failed (non-blocking): %s",
                    _intake_exc,
                )

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
                # The approval_id is either the one the subagent relayed (hook-block
                # / singular path) or the one the INTAKE bridge just minted for a
                # plan-first COMMAND_SET. Either way it points at the pending row
                # the handoff_approvals audit row should link to.
                approval_id = approval_req.get("approval_id") or minted_command_set_id

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
