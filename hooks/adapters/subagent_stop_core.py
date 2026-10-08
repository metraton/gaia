"""Host-neutral SubagentStop close: the contract gate and everything that follows it.

A host adapter translates its stop event into ``hook_data`` plus an
``AgentCompletion``, hands them to :func:`run_subagent_stop` with a
:class:`SubagentStopHost`, and formats the returned
:class:`SubagentStopOutcome` for its own wire. The gate, the rejection circuit
and its cut, the episode write, the workflow audit, the agent's approval
cleanup and the user_facing_summary relay all run here, so every host closes a
turn by the same rules.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# Semantic stop-reason classes. Mapping a host's raw stop reason onto them is
# the host's judgment (SubagentStopHost.classify_stop_reason); the portable
# contract core never sees a stop reason at all.
STOP_REASON_TRUNCATION = "truncation"
STOP_REASON_VIOLATION = "violation"
STOP_REASON_UNKNOWN = "unknown"


# The full-verdict gate is the default; an explicit falsy value of this
# variable restores the legacy three-case gate as the one-variable rollback.
GATE_RAMP_ENV_VAR = "GAIA_CONTRACT_FULL_VERDICT_GATE"
_GATE_FALSE_VALUES = {"0", "false", "no", "off"}

GATE_MODE_THREE_CASE = "three_case"
GATE_MODE_FULL_VERDICT = "full_verdict"

# A rejection is observable only where the gate runs: a repaired turn comes
# back closed and an unrepaired one looks like a harness cut. Severity error is
# load-bearing -- the triage reader
# (gaia.store.reader._query_orchestrator_defects) selects by severity threshold.
CONTRACT_REJECTED_EVENT = "agent.contract_rejected"

_REJECTION_PREVIEW_CHARS = 400


def full_verdict_gate_enabled() -> bool:
    """Whether the full-verdict gate is active: True unless
    ``GAIA_CONTRACT_FULL_VERDICT_GATE`` is an explicit falsy token."""
    return (
        os.environ.get(GATE_RAMP_ENV_VAR, "").strip().lower()
        not in _GATE_FALSE_VALUES
    )


@dataclass(frozen=True)
class ContractGateVerdict:
    """Outcome of the SubagentStop contract gate for one turn.

    Attributes:
        rejected: True -> the turn is sent back to the subagent for repair.
        rejection_reason: the repair message delivered to the subagent; empty
            when not rejected.
        anomalies: one anomaly per distinct invalidity, typed off the named
            FormErrorCode / CrossCheckErrorCode. Empty in three-case mode, whose
            anomalies stay on the legacy validate_contract path.
        mode: GATE_MODE_THREE_CASE or GATE_MODE_FULL_VERDICT.
        salvaged_truncation: the envelope was invalid or absent but the turn
            was cut by the host, so it is not rejected -- the truncation
            salvage and the handoff backstop already capture a degraded row.
    """

    rejected: bool
    rejection_reason: str
    anomalies: Tuple[Dict[str, Any], ...]
    mode: str
    salvaged_truncation: bool = False


def _gate_anomaly(agent_type: str, code: str, field: str, detail: str) -> Dict[str, Any]:
    """One anomaly per invalidity, keyed on the named core error code."""
    loc = f" [{field}]" if field else ""
    return {
        "type": "contract_gate_violation",
        "code": code,
        "field": field,
        "severity": "critical",
        "message": f"Contract invalid for {agent_type} {code}{loc}: {detail}".rstrip(),
    }


def _blind_verification_required(
    agent_state: str, plan_task_id: Optional[int]
) -> Optional[str]:
    """Return a rejection reason iff a COMPLETE turn is bound to a plan task.

    A turn whose dispatch binding carries a ``plan_task_id`` may not
    self-COMPLETE: it proposes NEEDS_VERIFICATION for an independent verifier.
    An unbound turn may self-COMPLETE. The decision is a pure function of
    ``(agent_state, plan_task_id)`` -- never of the agent's role or the turn's
    kind -- and it stays out of the portable shape core, which must remain
    binding-blind (tests/contract/test_validator_portable.py).
    """
    if agent_state != "COMPLETE":
        return None
    if plan_task_id is None:
        return None
    return (
        f"agent_status.agent_state is COMPLETE, but this turn is bound to "
        f"plan_task_id={plan_task_id}: a plan-task-bound producer turn may not "
        "self-COMPLETE. Set agent_state to NEEDS_VERIFICATION and propose "
        "evidence_report.verification.result for an independent verifier to "
        "confirm, or stay IN_PROGRESS. (A turn with no plan_task_id -- "
        "investigation / memory -- may self-COMPLETE.)"
    )


def _three_case_verdict(
    parsed_contract: Any,
    agent_type: str,
    plan_task_id: Optional[int] = None,
) -> ContractGateVerdict:
    """The legacy gate: reject the three structural cases (no envelope, no
    agent_status, invalid agent_state) plus a blind-verification violation,
    so the rollback path is not a bypass of blind verification."""
    from modules.agents.contract_validator import _resolve_status
    from modules.agents.response_contract import VALID_PLAN_STATUSES

    if parsed_contract is None:
        reason = (
            "[CONTRACT REJECTED] This turn's persisted contract row carries no "
            "parseable envelope: agent_contract_handoffs.raw_handoff_json did "
            "not parse as JSON (it is read with json.loads).\n"
            "The response text is not consulted by this gate, so re-emitting a "
            "fenced block will not change this verdict. Rebuild the contract via "
            "'gaia contract set/add/fill' and close it with "
            "'gaia contract finalize --draft-id <contract_id>'."
        )
        return ContractGateVerdict(True, reason, (), GATE_MODE_THREE_CASE)

    agent_status = parsed_contract.get("agent_status")
    if not agent_status or not isinstance(agent_status, dict):
        reason = (
            "[CONTRACT REJECTED] agent_status block missing from this turn's "
            "contract envelope.\n"
            "The envelope must include an agent_status object with "
            "agent_state, agent_id, pending_steps, and next_action. Write it "
            "with `gaia contract set` and close with `gaia contract finalize`."
        )
        return ContractGateVerdict(True, reason, (), GATE_MODE_THREE_CASE)

    normalized = _resolve_status(agent_status)
    raw_agent_state = agent_status.get("agent_state", "")
    if not normalized or normalized not in VALID_PLAN_STATUSES:
        valid_list = ", ".join(sorted(VALID_PLAN_STATUSES))
        reason = (
            f"[CONTRACT REJECTED] agent_state is missing or invalid: "
            f"'{raw_agent_state}'.\n"
            f"Valid statuses: {valid_list}.\n"
            f"Set agent_state to one of these values in agent_status."
        )
        return ContractGateVerdict(True, reason, (), GATE_MODE_THREE_CASE)

    violation = _blind_verification_required(normalized, plan_task_id)
    if violation:
        return ContractGateVerdict(
            True, f"[CONTRACT REJECTED]\n{violation}", (), GATE_MODE_THREE_CASE
        )

    return ContractGateVerdict(False, "", (), GATE_MODE_THREE_CASE)


def _contract_gate_verdict(
    parsed_contract: Any,
    *,
    agent_type: str = "unknown",
    plan_task_id: Optional[int] = None,
    stop_reason_classification: str = STOP_REASON_UNKNOWN,
    ramp_enabled: Optional[bool] = None,
    db_path: Optional[str] = None,
    envelope_source: str = "declaration",
) -> ContractGateVerdict:
    """Compute the gate verdict for one envelope; writes nothing.

    Args:
        parsed_contract: the parsed envelope dict, or None.
        agent_type: the emitting agent, for anomaly messages.
        plan_task_id: the plan task the dispatch binding carries, or None.
        stop_reason_classification: an already-resolved STOP_REASON_* class;
            a truncation softens an invalid envelope to salvaged_truncation.
        ramp_enabled: None reads the ramp variable; False selects the
            three-case gate, True the full verdict of the portable core.
        db_path: optional gaia.db path for the layer-2 cross-check.
        envelope_source: forwarded to ``gaia.contract.crosscheck.validate`` so
            an unparseable row is reported as the row failing. The SubagentStop
            gate passes ``"row"``; the CLI's validate-on-write keeps the
            ``"declaration"`` default.
    """
    if ramp_enabled is None:
        ramp_enabled = full_verdict_gate_enabled()

    if not ramp_enabled:
        return _three_case_verdict(parsed_contract, agent_type, plan_task_id)

    from gaia.contract.crosscheck import validate as _core_validate

    _db = Path(db_path) if db_path else None
    result = _core_validate(parsed_contract, db_path=_db, source=envelope_source)
    if result.ok:
        agent_status = parsed_contract.get("agent_status") if isinstance(parsed_contract, dict) else None
        agent_state = ""
        if isinstance(agent_status, dict):
            from modules.agents.contract_validator import _resolve_status
            agent_state = _resolve_status(agent_status)
        violation = _blind_verification_required(agent_state, plan_task_id)
        if violation:
            anomaly = _gate_anomaly(agent_type, "BLIND_VERIFICATION_REQUIRED", "agent_status.agent_state", violation)
            return ContractGateVerdict(
                True, f"[CONTRACT REJECTED]\n{violation}", (anomaly,), GATE_MODE_FULL_VERDICT
            )
        return ContractGateVerdict(False, "", (), GATE_MODE_FULL_VERDICT)

    if stop_reason_classification == STOP_REASON_TRUNCATION:
        return ContractGateVerdict(
            False, "", (), GATE_MODE_FULL_VERDICT, salvaged_truncation=True
        )

    anomalies = tuple(
        _gate_anomaly(
            agent_type,
            err.code.value,
            getattr(err, "field", ""),
            getattr(err, "detail", ""),
        )
        for err in result.errors
    )

    repair = result.form.repair_message
    if result.crosscheck.repair_message:
        repair = f"{repair}\n\n{result.crosscheck.repair_message}"

    # Missing fields and wrong values are listed apart; each line keeps the raw
    # error code as a substring, which assertions and log scrapers match on.
    # Everything that is not MISSING_FIELD is "invalid", so a future code is
    # never dropped.
    from gaia.contract.validator import FormErrorCode as _FormErrorCode

    _missing_code = _FormErrorCode.MISSING_FIELD.value
    _missing = [e for e in result.errors if e.code.value == _missing_code]
    _invalid = [e for e in result.errors if e.code.value != _missing_code]
    _summary_lines: list[str] = []
    if _missing:
        _summary_lines.append("Faltan: " + "; ".join(str(e) for e in _missing))
    if _invalid:
        _summary_lines.append("Inválidos: " + "; ".join(str(e) for e in _invalid))
    grouped_summary = "\n".join(_summary_lines) if _summary_lines else result.error_summary()

    reason = f"[CONTRACT REJECTED]\n{grouped_summary}\n\n{repair}"

    if any(
        e.code.value == _FormErrorCode.VERIFICATION_RESULT.value
        for e in result.errors
    ):
        reason = (
            f"{reason}\n\n"
            "Si la verificación falló de verdad, NO emitas COMPLETE — quédate en "
            "IN_PROGRESS (reintento) o BLOCKED y registra "
            "evidence_report.verification.result='fail'. COMPLETE afirma éxito."
        )

    return ContractGateVerdict(True, reason, anomalies, GATE_MODE_FULL_VERDICT)


def _record_contract_rejection_defect(
    verdict: ContractGateVerdict,
    *,
    agent_type: str,
    plan_task_id: Optional[int],
) -> None:
    """Record a gate rejection in harness_events. Never raises.

    The harness_events channel takes no episode_id, so a rejection on a turn
    whose episode does not exist yet never needs a fabricated parent row.
    """
    try:
        from modules.events.event_writer import EventWriter

        codes = [
            str(a.get("code", ""))
            for a in verdict.anomalies
            if isinstance(a, dict) and a.get("code")
        ]
        summary = verdict.rejection_reason.replace("\n", " ").strip()
        meta: Dict[str, Any] = {
            "gate_mode": verdict.mode,
            "agent": agent_type,
            "codes": codes,
            "reason_preview": summary[:_REJECTION_PREVIEW_CHARS],
        }
        if plan_task_id is not None:
            meta["plan_task_id"] = plan_task_id

        detail = f": {', '.join(codes)}" if codes else ""
        EventWriter().write_event(
            CONTRACT_REJECTED_EVENT,
            "hook",
            agent_type,
            f"contract gate rejected {agent_type}'s turn ({verdict.mode}){detail}",
            severity="error",
            meta=meta,
        )
    except Exception as exc:  # pragma: no cover - telemetry must never block
        logger.debug("contract rejection event write failed (non-fatal): %s", exc)


def evaluate_contract_gate(
    parsed_contract: Any,
    *,
    agent_type: str = "unknown",
    plan_task_id: Optional[int] = None,
    stop_reason_classification: str = STOP_REASON_UNKNOWN,
    ramp_enabled: Optional[bool] = None,
    db_path: Optional[str] = None,
    envelope_source: str = "declaration",
) -> ContractGateVerdict:
    """Return :func:`_contract_gate_verdict`'s verdict, recording a rejection
    as a ``CONTRACT_REJECTED_EVENT`` defect."""
    verdict = _contract_gate_verdict(
        parsed_contract,
        agent_type=agent_type,
        plan_task_id=plan_task_id,
        stop_reason_classification=stop_reason_classification,
        ramp_enabled=ramp_enabled,
        db_path=db_path,
        envelope_source=envelope_source,
    )
    if verdict.rejected:
        _record_contract_rejection_defect(
            verdict, agent_type=agent_type, plan_task_id=plan_task_id
        )
    return verdict


# The persisted row is the gate's only input. A fenced envelope in the final
# message never supplied work, only a last-second closing claim for a turn that
# wrote none, and it is the first thing a truncated message loses.
GATE_SOURCE_ROW = "row"
GATE_SOURCE_ROW_UNFINALIZED = "row_unfinalized"
GATE_SOURCE_ROW_MISSING = "row_missing"


def _row_gate_candidate(
    *, bound_dispatch_row: Optional[dict], db_path: Optional[Path] = None,
) -> Optional[dict]:
    """The full ``agent_contract_handoffs`` row for this turn's dispatch
    binding, or None when no binding resolved.

    The binding from the host's ``resolve_dispatch_row`` may be a projection
    without ``raw_handoff_json`` and ``cut_reason``, so the row is re-read by
    its unique ``contract_id``.
    """
    if not bound_dispatch_row:
        return None
    contract_id = bound_dispatch_row.get("contract_id")
    if not contract_id:
        return None
    from gaia.store.writer import list_agent_contract_handoffs

    rows = list_agent_contract_handoffs(contract_id=contract_id, limit=1, db_path=db_path)
    return rows[0] if rows else None


def _row_cleanly_finalized(full_row: Dict[str, Any]) -> bool:
    """True iff the agent's own ``gaia contract finalize`` closed the row.

    Birth stamps ``cut_reason='never_finalized'`` and only a finalize without
    a cut reason clears it (schema.sql, agent_contract_handoffs.cut_reason);
    the writer sets ``agent_state`` in the same upsert.
    """
    return (
        full_row.get("agent_state") != "DISPATCHED"
        and full_row.get("cut_reason") is None
    )


def _parse_row_envelope(full_row: Dict[str, Any]) -> Any:
    """The row's parsed ``raw_handoff_json``, or None when missing or
    unparseable -- which the core then rejects as a missing envelope."""
    try:
        return json.loads(full_row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        return None


def _row_unfinalized_verdict(
    agent_type: str, full_row: Dict[str, Any], gate_mode: str,
) -> ContractGateVerdict:
    """Reject a turn whose own dispatch row exists but was never cleanly
    closed by ``gaia contract finalize``."""
    contract_id = full_row.get("contract_id")
    cut_reason = full_row.get("cut_reason") or "unknown"
    row_state = full_row.get("agent_state") or "unknown"
    reason = (
        f"[CONTRACT REJECTED] This turn's own dispatch row (contract_id="
        f"{contract_id!r}) exists but was never cleanly closed by "
        f"'gaia contract finalize' (row agent_state={row_state!r}, "
        f"cut_reason={cut_reason!r}). The persisted row is the ONLY source of "
        f"truth for this gate -- a fenced agent_contract_handoff block in "
        f"your response text is not consulted here, however complete. "
        f"Run 'gaia contract finalize --draft-id {contract_id}' with your "
        f"real closing state (COMPLETE, NEEDS_VERIFICATION, BLOCKED, "
        f"NEEDS_INPUT, or APPROVAL_REQUEST) before ending the turn."
    )
    anomalies: Tuple[Dict[str, Any], ...] = ()
    if gate_mode == GATE_MODE_FULL_VERDICT:
        anomalies = (
            _gate_anomaly(agent_type, "ROW_NOT_FINALIZED", "dispatch_row", reason),
        )
    return ContractGateVerdict(True, reason, anomalies, gate_mode)


def _row_missing_verdict(agent_type: str, gate_mode: str) -> ContractGateVerdict:
    """Reject a turn with no reachable dispatch row.

    Repairable: finalizing the draft the kernel named converges the born row,
    which the host's resolve_dispatch_row reaches on the retry.
    """
    reason = (
        "[CONTRACT REJECTED] No persisted contract row could be located for "
        "this turn, so there is no record that it closed. The persisted "
        "agent_contract_handoffs row is the ONLY source of truth for this "
        "gate -- a fenced agent_contract_handoff block in your response text "
        "is not consulted here. Run 'gaia contract finalize --draft-id "
        "<contract_id>' on the draft your dispatch kernel named (# Your "
        "Contract), with your real closing state (COMPLETE, "
        "NEEDS_VERIFICATION, BLOCKED, NEEDS_INPUT, or APPROVAL_REQUEST). If "
        "no contract was ever opened for you, run 'gaia contract init' first."
    )
    anomalies: Tuple[Dict[str, Any], ...] = ()
    if gate_mode == GATE_MODE_FULL_VERDICT:
        anomalies = (
            _gate_anomaly(agent_type, "ROW_NOT_FOUND", "dispatch_row", reason),
        )
    return ContractGateVerdict(True, reason, anomalies, gate_mode)


def _select_gate_source(
    *,
    bound_dispatch_row: Optional[dict],
    db_path: Optional[Path] = None,
) -> Tuple[str, Optional[dict]]:
    """Classify this turn's persisted row and fetch it once.

    Returns ``(source, full_row)``: one of the GATE_SOURCE_* values and the
    fetched row, None for GATE_SOURCE_ROW_MISSING.
    """
    full_row = _row_gate_candidate(bound_dispatch_row=bound_dispatch_row, db_path=db_path)
    if full_row is None:
        return GATE_SOURCE_ROW_MISSING, None
    if _row_cleanly_finalized(full_row):
        return GATE_SOURCE_ROW, full_row
    return GATE_SOURCE_ROW_UNFINALIZED, full_row


def _resolve_subagent_stop_gate_full(
    *,
    agent_type: str = "unknown",
    plan_task_id: Optional[int] = None,
    stop_reason_classification: str = STOP_REASON_UNKNOWN,
    ramp_enabled: Optional[bool] = None,
    bound_dispatch_row: Optional[dict] = None,
    db_path: Optional[str] = None,
) -> Tuple[ContractGateVerdict, str, Any]:
    """The row-only gate. Returns ``(verdict, source, envelope_used)``.

    A cleanly finalized row has its envelope validated by
    :func:`evaluate_contract_gate`; an unfinalized row or no row rejects. A
    host truncation softens the latter two to a salvaged_truncation verdict,
    as the core already does for an invalid envelope. ``envelope_used`` is the
    row's parsed envelope (None without a row) so nonce preservation and the
    summary relay read the exact envelope the verdict did.
    """
    if ramp_enabled is None:
        ramp_enabled = full_verdict_gate_enabled()
    gate_mode = GATE_MODE_FULL_VERDICT if ramp_enabled else GATE_MODE_THREE_CASE

    db_path_obj = Path(db_path) if db_path else None
    source, full_row = _select_gate_source(
        bound_dispatch_row=bound_dispatch_row,
        db_path=db_path_obj,
    )

    if source == GATE_SOURCE_ROW:
        row_envelope = _parse_row_envelope(full_row)
        verdict = evaluate_contract_gate(
            row_envelope,
            agent_type=agent_type,
            plan_task_id=plan_task_id,
            stop_reason_classification=stop_reason_classification,
            ramp_enabled=ramp_enabled,
            db_path=db_path,
            envelope_source="row",
        )
        return verdict, GATE_SOURCE_ROW, row_envelope

    row_envelope = _parse_row_envelope(full_row) if full_row is not None else None

    if stop_reason_classification == STOP_REASON_TRUNCATION:
        return (
            ContractGateVerdict(False, "", (), gate_mode, salvaged_truncation=True),
            source,
            row_envelope,
        )

    if source == GATE_SOURCE_ROW_UNFINALIZED:
        verdict = _row_unfinalized_verdict(agent_type, full_row, gate_mode)
    else:
        verdict = _row_missing_verdict(agent_type, gate_mode)
    _record_contract_rejection_defect(
        verdict, agent_type=agent_type, plan_task_id=plan_task_id
    )
    return verdict, source, row_envelope


def resolve_subagent_stop_gate(
    *,
    agent_type: str = "unknown",
    plan_task_id: Optional[int] = None,
    stop_reason_classification: str = STOP_REASON_UNKNOWN,
    ramp_enabled: Optional[bool] = None,
    bound_dispatch_row: Optional[dict] = None,
    db_path: Optional[str] = None,
) -> Tuple[ContractGateVerdict, str]:
    """``(verdict, source)`` of :func:`_resolve_subagent_stop_gate_full`."""
    verdict, source, _envelope_used = _resolve_subagent_stop_gate_full(
        agent_type=agent_type,
        plan_task_id=plan_task_id,
        stop_reason_classification=stop_reason_classification,
        ramp_enabled=ramp_enabled,
        bound_dispatch_row=bound_dispatch_row,
        db_path=db_path,
    )
    return verdict, source


def _project_owner_workspace(
    workspace: Optional[str],
    dispatch_project: Optional[str],
    db_path: Optional[str],
) -> Optional[str]:
    """``workspace``, or the workspace owning the project the dispatch named.

    Unchanged for a dispatch that named no project, one the substrate does not
    know, or a lookup that fails.
    """
    if not workspace or not dispatch_project:
        return workspace
    try:
        from modules.core.paths import ensure_package_root_importable

        ensure_package_root_importable()
        from tools.context.context_provider import dispatch_project_workspace

        return dispatch_project_workspace(
            workspace, dispatch_project, Path(db_path) if db_path else None,
        ) or workspace
    except Exception:
        logger.warning(
            "Project workspace lookup failed for %r; keeping workspace %s",
            dispatch_project, workspace, exc_info=True,
        )
        return workspace


def resolve_dispatch_row(
    *,
    session_id: str,
    agent_type: str,
    task_info: dict,
    parsed_contract: Any,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    """The born-at-dispatch row of the turn that is ending, or None (unbound).

    Lanes, most exact first:

      1. harness -- ``harness_agent_id`` stamped at the SubagentStart claim,
         the one coordinate the runtime writes for this exact dispatch. It
         comes first because the inexact lanes do not merely miss: they can hit
         a contention row the backstop stamped with the same value (measured:
         the gate rejected a correctly recorded turn). Ambiguity is declined.
      2. adopted -- the turn's own minted ``agent_id``, looked up regardless of
         state, since its finalize already converged the born row.
      3. legacy -- rows born carrying the agent NAME as ``agent_id``.
      4. unadopted -- the dispatched NAME in the birth envelope; refuses to
         choose between concurrent same-name dispatches.

    A full miss is logged, so it is distinguishable from a genuinely unbound
    turn.
    """
    from gaia.store.writer import (
        dispatch_row_for_identity,
        find_dispatched_row_by_agent_name,
        find_orphaned_dispatched_handoff,
    )
    from modules.agents.handoff_persister import (
        dispatch_row_by_harness_id,
        resolve_minted_agent_id,
    )

    harness_agent_id = task_info.get("agent_id")
    harness_row = dispatch_row_by_harness_id(
        task_info, session_id=session_id, db_path=db_path,
    )
    if harness_row is not None:
        return harness_row

    minted = resolve_minted_agent_id(
        parsed_contract, task_info, session_id=session_id,
    )
    if minted and str(minted) != str(agent_type):
        row = dispatch_row_for_identity(
            session_id, str(minted), db_path=db_path,
        )
        if row is not None:
            return row

    legacy = find_orphaned_dispatched_handoff(
        session_id, [agent_type], db_path=db_path,
    )
    if legacy is not None:
        return legacy

    unadopted = find_dispatched_row_by_agent_name(
        session_id, agent_type, db_path=db_path,
    )
    if unadopted is not None:
        return unadopted

    logger.warning(
        "Dispatch-row resolution: NO lane resolved a row for agent=%s "
        "session=%s harness_agent_id=%s minted=%s. The turn will be treated "
        "as unbound -- no plan-task binding and no row for the gate to read.",
        agent_type, session_id, harness_agent_id, minted,
    )
    return None


def reconstruct_contract_from_finalized_draft(
    *,
    task_info: dict,
    parsed_contract: Any,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Rebuild a fenceless turn's envelope from its finalized row, or None.

    Only the row is read: the draft file becomes collectable precisely once
    the row is durable, so a reader reaching for the file after finalize can
    find it gone. The draft only helps locate the contract id -- minted handle
    against the drafts directory, then against the rows, then the
    ``harness_agent_id`` bridge for a turn born with its draft open. A row that
    is not finalized is salvage/backstop territory and is left alone. Every
    miss logs: a silent miss once hid a dropped ``update_contracts`` proposal
    (handoff row 11304). Never raises.
    """
    if isinstance(parsed_contract, dict) and isinstance(
        parsed_contract.get("agent_status"), dict
    ):
        return None
    try:
        from gaia.contract.drafts import resolve_draft_id
        from gaia.store import writer as _writer
        from modules.agents.handoff_persister import (
            dispatch_row_by_harness_id,
            resolve_minted_agent_id,
        )
    except Exception as exc:
        logger.debug("M4 reconstruction: core import failed (non-fatal): %s", exc)
        return None

    try:
        db_path_str = task_info.get("db_path")
        db_path = Path(db_path_str) if db_path_str else None
        minted_agent_id = resolve_minted_agent_id(
            parsed_contract, task_info, session_id=session_id,
        )
        contract_id = None
        if minted_agent_id:
            contract_id = resolve_draft_id(
                explicit=None, agent_id=str(minted_agent_id)
            )
            if not contract_id:
                handle_row = _writer.find_finalized_handoff_by_agent_id(
                    str(minted_agent_id), db_path=db_path
                )
                contract_id = (handle_row or {}).get("contract_id")
        if not contract_id:
            dispatch_row = dispatch_row_by_harness_id(task_info, session_id)
            contract_id = (dispatch_row or {}).get("contract_id")
        if not contract_id:
            logger.warning(
                "M4 reconstruction: fence missing AND no contract id "
                "resolvable (agent=%s session=%s) -- neither a draft nor a "
                "dispatch row locates this turn, so a finalized turn's "
                "envelope (including any update_contracts it carried) is "
                "NOT recovered.",
                task_info.get("agent"), session_id,
            )
            return None
        # A nascent DISPATCHED row exists from birth, so "finalized" is the
        # terminal-row check, never "any row exists".
        if not _writer.agent_contract_handoff_finalized(contract_id, db_path=db_path):
            logger.info(
                "M4 reconstruction: contract %s exists but its row is not "
                "finalized -- salvage/backstop territory, not a completed "
                "turn missing only its fence.",
                contract_id,
            )
            return None
        envelope = _writer.agent_contract_handoff_envelope(
            contract_id, db_path=db_path
        )
        if not isinstance(envelope, dict) or not isinstance(
            envelope.get("agent_status"), dict
        ):
            logger.warning(
                "M4 reconstruction: row for contract %s is finalized but its "
                "raw_handoff_json is unusable (%s) -- cannot rebuild the fence.",
                contract_id, type(envelope).__name__,
            )
            return None
        recon = dict(envelope)
        recon["_contract_tag"] = "agent_contract_handoff"
        recon["reconstructed_from_finalized_draft"] = contract_id
        logger.info(
            "M4 reconstruction: fence missing but finalized contract %s found; "
            "envelope reconstructed from its row so the gate parses the "
            "completed contract.",
            contract_id,
        )
        return recon
    except Exception as exc:
        logger.warning("M4 reconstruction: rebuild failed (non-fatal): %s", exc)
        return None


def salvage_truncated_draft(
    *,
    parsed_contract: Any,
    task_info: dict,
    session_id: str,
    plan_task_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Finalize a truncated turn's partial draft as a degraded row.

    Keys on the same contract id the handoff backstop uses, so the two
    converge on one row through ``ON CONFLICT(contract_id) DO NOTHING``. The
    row is ``degraded``, marked ``salvaged="truncation"`` and cut-reasoned,
    never a clean close. Returns ``{"contract_id", "resume_hint", "created"}``
    or None; never raises.
    """
    try:
        from gaia.contract.drafts import load_draft, resolve_draft_id
        from gaia.contract.view import render_resume_hint
        from gaia.state import (
            CUT_REASON_SALVAGED_TRUNCATION,
            VALID_PLAN_STATUSES,
        )
        from gaia.store import writer as _writer
    except Exception as exc:
        logger.debug("T11 salvage: core import failed (non-fatal): %s", exc)
        return None

    # The shared resolver, so salvage, the backstop and the reconstruction all
    # land on the same draft.
    from modules.agents.handoff_persister import resolve_minted_agent_id
    minted_agent_id = resolve_minted_agent_id(
        parsed_contract, task_info, session_id=session_id,
    )
    if not minted_agent_id:
        return None

    try:
        draft_id = resolve_draft_id(explicit=None, agent_id=str(minted_agent_id))
        if not draft_id:
            return None
        envelope = load_draft(draft_id)
        if not isinstance(envelope, dict):
            return None

        db_path_str = task_info.get("db_path")
        db_path = Path(db_path_str) if db_path_str else None
        workspace = (
            task_info.get("workspace")
            or os.environ.get("GAIA_WORKSPACE")
            or "global"
        )
        agent_id_col = minted_agent_id or task_info.get("agent") or "unknown"

        from modules.agents.handoff_persister import clean_rescue_envelope

        cleaning_log: list = []
        salvaged = dict(clean_rescue_envelope(envelope, log=cleaning_log))
        if cleaning_log:
            logger.debug(
                "T11 salvage: cleaned draft %s: %s",
                draft_id, "; ".join(str(line) for line in cleaning_log),
            )
        # The state is read from the raw draft, as the backstop does:
        # canonicalizing it would change what a rescued turn is recorded as.
        agent_status = envelope.get("agent_status")
        agent_state = (
            agent_status.get("agent_state")
            if isinstance(agent_status, dict)
            else None
        )
        agent_state = (
            agent_state if agent_state in VALID_PLAN_STATUSES else "IN_PROGRESS"
        )
        # A truncated turn never reached its own finalize, so its COMPLETE is a
        # claim; recording it would satisfy the briefs invariant "plan closed
        # => a COMPLETE handoff row exists" (gaia/briefs/store.py) falsely. The
        # claim survives in the envelope beside the `salvaged` marker.
        if agent_state == "COMPLETE":
            agent_state = "IN_PROGRESS"
        salvaged["degraded"] = True
        salvaged["auto_captured"] = True
        salvaged["salvaged"] = "truncation"

        outcome = _writer.finalize_agent_contract_handoff(
            contract_id=draft_id,
            agent_id=str(agent_id_col),
            workspace=workspace,
            agent_state=agent_state,
            raw_handoff_json=json.dumps(salvaged),
            session_id=session_id,
            plan_task_id=plan_task_id,
            brief_id=None,
            cut_reason=CUT_REASON_SALVAGED_TRUNCATION,
            db_path=db_path,
        )

        try:
            resume_hint = render_resume_hint(draft_id, envelope)
        except Exception:
            resume_hint = None

        logger.info(
            "T11 salvage: truncated draft %s finalized degraded (created=%s)",
            draft_id, outcome.get("created"),
        )
        return {
            "contract_id": draft_id,
            "resume_hint": resume_hint,
            "created": bool(outcome.get("created")),
        }
    except Exception as exc:
        logger.warning("T11 salvage: rescue failed (non-fatal): %s", exc)
        return None


def gaia_agent_roster() -> Set[str]:
    """Names of the Gaia-managed agents, unioned over every lane that resolves.

    An empty set means the roster did not resolve, never that Gaia has no
    agents, so the caller grants the native-agent bypass only on a non-empty
    result.
    """
    from modules.security.protected_paths import declared_hook_tree_roots

    # Two lanes because the first is a function of the deployment layout:
    # the directory beside the running module is not the agents directory
    # once the hooks are materialised away from their checkout. The registry
    # lane is the identity declared outside any deployment.
    candidates = [Path(__file__).resolve().parent.parent.parent / "agents"]
    candidates.extend(
        Path(root).parent / "agents" for root in declared_hook_tree_roots()
    )

    names: Set[str] = set()
    for agents_dir in candidates:
        try:
            if not agents_dir.is_dir():
                continue
            names.update(
                f.stem
                for f in agents_dir.iterdir()
                if f.suffix == ".md" and f.is_file()
            )
        except OSError:
            # A lane that cannot be read declines to CONTRIBUTE names; it
            # never removes what another lane already found.
            continue
    return names


@dataclass(frozen=True)
class SubagentStopHost:
    """What the close needs from the host that delivered the stop event.

    Attributes:
        agent_roster: names of Gaia's own agents; empty when none resolved.
        classify_stop_reason: the host's raw stop reason -> STOP_REASON_*.
        resume_map_dir: where the host records a per-session resume mapping;
            None for a host that resumes a child natively and records none.
        resolve_dispatch_row, reconstruct_contract, salvage_truncated_draft:
            the host-neutral lookups above; a host overrides them only to
            substitute its own entry points.
    """

    agent_roster: Callable[[], Set[str]]
    classify_stop_reason: Callable[[Optional[str]], str]
    resume_map_dir: Optional[Path]
    resolve_dispatch_row: Callable[..., Optional[dict]] = resolve_dispatch_row
    reconstruct_contract: Callable[..., Optional[dict]] = (
        reconstruct_contract_from_finalized_draft
    )
    salvage_truncated_draft: Callable[..., Optional[dict]] = salvage_truncated_draft


@dataclass(frozen=True)
class SubagentStopOutcome:
    """The close's result for the host to format.

    Attributes:
        result: the hook output, free of any host channel key.
        rejected: the turn goes back to the subagent for repair.
        user_message: text for the host's user-facing channel -- the circuit
            cut notice or the relayed user_facing_summary. It must never travel
            on a channel that feeds back into the subagent: that resumes the
            turn being closed.
    """

    result: Dict[str, Any]
    rejected: bool
    user_message: Optional[str] = None


def run_subagent_stop(
    host: SubagentStopHost,
    hook_data: Dict[str, Any],
    completion: Any,
    *,
    event_session_id: Optional[str],
) -> SubagentStopOutcome:
    """Close one subagent turn: gate its persisted contract and run every
    post-close step. ``completion`` is the host's ``AgentCompletion``."""
    from modules.agents.contract_validator import (
        extract_commands_executed,
        parse_contract,
        validate as validate_contract,
        validate_approval_request,
        validate_verbatim_outputs_consistency,
    )
    from modules.agents.response_contract import (
        save_validation_result,
        validate_response_contract,
        resolve_agent_id,
    )
    from modules.agents.task_info_builder import build_task_info_from_hook_data
    from modules.agents.transcript_reader import read_transcript, read_full_transcript_text
    from modules.audit.workflow_auditor import audit as audit_workflow, signal_gaia_analysis
    from modules.audit.workflow_recorder import record as record_workflow
    from modules.context.context_writer import process_update_contracts
    from modules.memory.episode_writer import write as write_episode
    from modules.security.approval_cleanup import cleanup as cleanup_approval
    from modules.session.session_manager import get_or_create_session_id

    logger.info(
        "Hook event: %s, agent: %s",
        hook_data.get("hook_event_name"),
        hook_data.get("agent_type", "unknown"),
    )

    transcript_analysis = None
    try:
        from modules.agents.transcript_analyzer import analyze as analyze_transcript
        if completion.transcript_path:
            transcript_analysis = analyze_transcript(completion.transcript_path)
            logger.info(
                "Transcript analysis: %d tool calls, %d API calls, model=%s",
                transcript_analysis.tool_call_count,
                transcript_analysis.api_call_count,
                transcript_analysis.model,
            )
    except Exception as exc:
        logger.debug("Transcript analysis failed (non-fatal): %s", exc)

    agent_output = completion.last_message
    if not agent_output:
        transcript_path = completion.transcript_path
        agent_output = read_transcript(transcript_path) if transcript_path else ""
        logger.info("Agent output: %d chars from transcript (fallback)", len(agent_output))
    else:
        logger.info("Agent output: %d chars from last_assistant_message", len(agent_output))

    task_info = build_task_info_from_hook_data(hook_data, agent_output)

    # A non-Gaia agent emits no contract, so gating it would reject every turn.
    # Bypassing needs a resolved roster AND an identified agent: with no roster
    # every agent would read as native, so that case fails closed and the
    # circuit bounds the cost.
    _native_agent_type = task_info.get("agent", "unknown")
    _gaia_agents = host.agent_roster()
    _agent_identified = _native_agent_type not in ("", "unknown")
    if _gaia_agents and _agent_identified and _native_agent_type not in _gaia_agents:
        logger.info(
            "Native agent '%s' — skipping contract validation (gaia agents: %s)",
            _native_agent_type, _gaia_agents,
        )
        return SubagentStopOutcome(
            result={"success": True, "native_agent": True, "agent": _native_agent_type},
            rejected=False,
        )
    if not _gaia_agents:
        logger.error(
            "Agent roster did not resolve on any lane; gating '%s' instead "
            "of treating it as native. Contract enforcement is NOT "
            "disabled, but this deployment cannot see its own agents.",
            _native_agent_type,
        )

    # The rejection reaches the outcome through this latch, not through
    # `result`: the exception handler below rebuilds that dict, which would
    # otherwise close a rejected turn as accepted.
    _rejection_latched = False
    _latched_rejection_reason = ""
    user_message: Optional[str] = None

    try:
        from datetime import datetime as _dt
        # The event's session id first: the env-derived fallback never matches
        # the pendings persisted under the real session, so cleanup would miss.
        session_id = event_session_id or get_or_create_session_id()
        agent_type = task_info.get("agent", "unknown")

        parsed_contract = parse_contract(agent_output)

        # A turn that finalized its draft but emitted no fence would read as
        # blank to every descriptive reader below (agent_state, episode,
        # key_outputs, update_contracts). The gate does not use this.
        if not (
            isinstance(parsed_contract, dict)
            and isinstance(parsed_contract.get("agent_status"), dict)
        ):
            _reconstructed = host.reconstruct_contract(
                task_info=task_info,
                parsed_contract=parsed_contract,
                session_id=session_id,
            )
            if _reconstructed is not None:
                parsed_contract = _reconstructed

        contract_result = validate_contract(agent_output, task_info)
        if not contract_result.is_valid:
            logger.warning(
                "Contract validation failed for %s: %s",
                agent_type, contract_result.error_message,
            )
            _validation_anomalies = []
            for _m in (contract_result.missing or []):
                _validation_anomalies.append({
                    "type": "contract_validation_failure",
                    "severity": "warning",
                    "message": f"Contract validation failed for {agent_type}: missing={_m}",
                })
        else:
            _validation_anomalies = []

        from modules.agents.contract_validator import _resolve_status
        _resolved_agent_state = (
            _resolve_status(parsed_contract.get("agent_status") or {})
            if isinstance(parsed_contract, dict) else ""
        )

        # task_info["plan_status"] was derived from agent_output alone, before
        # the reconstruction above; every reader below needs the real state.
        if _resolved_agent_state:
            task_info["plan_status"] = _resolved_agent_state

        _raw_stop_reason = hook_data.get("stop_reason")
        if not _raw_stop_reason and transcript_analysis and transcript_analysis.stop_reasons:
            _raw_stop_reason = transcript_analysis.stop_reasons[-1]
        _stop_reason_classification = host.classify_stop_reason(_raw_stop_reason)

        _full_verdict = full_verdict_gate_enabled()
        _gate_mode = GATE_MODE_FULL_VERDICT if _full_verdict else GATE_MODE_THREE_CASE
        # The same resolved row feeds the blind-verification binding and the
        # gate's row lookup, so the two never disagree on which row is this
        # turn's. An unresolvable binding is treated as unbound.
        _bound_plan_task_id: Optional[int] = None
        _bound_dispatch_row: Optional[dict] = None
        try:
            _db_for_binding = task_info.get("db_path")
            _bound_dispatch_row = host.resolve_dispatch_row(
                session_id=session_id,
                agent_type=agent_type,
                task_info=task_info,
                parsed_contract=parsed_contract,
                db_path=Path(_db_for_binding) if _db_for_binding else None,
            )
            if _bound_dispatch_row is not None:
                _bound_plan_task_id = _bound_dispatch_row.get("plan_task_id")
                if _bound_plan_task_id is None:
                    # The row may be a continuation link minted without the
                    # binding; reading it as unbound would let a plan-bound
                    # producer self-COMPLETE through the resumption.
                    from gaia.store.writer import (
                        dispatched_binding_plan_task_id_by_contract,
                    )

                    _bound_plan_task_id = (
                        dispatched_binding_plan_task_id_by_contract(
                            _bound_dispatch_row.get("contract_id"),
                            db_path=Path(_db_for_binding)
                            if _db_for_binding
                            else None,
                        )
                    )
        except Exception:
            logger.debug(
                "Could not resolve dispatch binding plan_task_id for %s "
                "(session=%s) -- treating turn as unbound.",
                agent_type, session_id, exc_info=True,
            )
        _gate_source = GATE_SOURCE_ROW_MISSING
        _authoritative_envelope: Any = None
        # A raising gate must not take down the approval cleanup below; it
        # degrades to a non-rejecting verdict and is logged loudly.
        try:
            _gate, _gate_source, _authoritative_envelope = _resolve_subagent_stop_gate_full(
                agent_type=agent_type,
                plan_task_id=_bound_plan_task_id,
                stop_reason_classification=_stop_reason_classification,
                ramp_enabled=_full_verdict,
                bound_dispatch_row=_bound_dispatch_row,
                db_path=task_info.get("db_path"),
            )
        except Exception:
            logger.exception(
                "Contract gate raised for %s (mode=%s) -- falling back to a "
                "non-rejecting verdict so SubagentStop cleanup still runs.",
                agent_type, _gate_mode,
            )
            _gate = ContractGateVerdict(False, "", (), _gate_mode)

        # Rejection circuit: counting here, where the verdict is known, numbers
        # each retry and lets the limit end the turn instead of looping. A
        # failure inside it leaves `_circuit = None` (no ceiling) and never
        # erases a rejection.
        _circuit = None
        _turn_key = None
        _circuit_key = None
        try:
            from modules.agents import rejection_circuit

            # Resolved for every verdict: the accepted branch resets the counter.
            _turn_key = rejection_circuit.counter_key(session_id, task_info)
            _circuit_key = _turn_key.key
            if _gate.rejected:
                _current_codes = [
                    str(a.get("code", ""))
                    for a in _gate.anomalies
                    if isinstance(a, dict) and a.get("code")
                ]
                _circuit = rejection_circuit.record_rejection(
                    _circuit_key,
                    codes=_current_codes,
                    shared=not _turn_key.per_turn,
                )
                if not _turn_key.per_turn:
                    logger.warning(
                        "Rejection circuit: %s (session=%s) carried no "
                        "per-dispatch identity, so it is counted under a "
                        "ceiling shared with every other unidentified turn "
                        "of this session.",
                        agent_type, session_id,
                    )
        except Exception as _circuit_exc:
            logger.warning(
                "Rejection circuit failed for %s (non-fatal); the retry "
                "ceiling is NOT in force this turn: %s",
                agent_type, _circuit_exc,
            )
        _circuit_tripped = bool(_circuit is not None and _circuit.tripped)

        # The last point the circuit can lower the verdict; nothing below may.
        if _gate.rejected and not _circuit_tripped:
            _rejection_latched = True
            _latched_rejection_reason = _gate.rejection_reason

        # Keep alive a pending approval the gate's own envelope still requests.
        # Cleanup only expires pendings past their 24h TTL, so reading the row
        # rather than the fence risks no nonce minted this turn.
        preserved_nonces: set = set()
        if isinstance(_authoritative_envelope, dict):
            _nonce_agent_status = _authoritative_envelope.get("agent_status") or {}
            _nonce_agent_state = (
                _resolve_status(_nonce_agent_status)
                if isinstance(_nonce_agent_status, dict) else ""
            )
            _approval_req = _authoritative_envelope.get("approval_request") or {}
            _nonce = _approval_req.get("approval_id") if isinstance(_approval_req, dict) else None
            if _nonce_agent_state == "APPROVAL_REQUEST" and _nonce:
                preserved_nonces.add(_nonce)

        # Grants are not swept here: they are consumed at the match in
        # PreToolUse and an unpresented one expires on its own short TTL.
        cleanup_approval(
            agent_type,
            session_id=session_id,
            preserve_nonces=preserved_nonces if preserved_nonces else None,
        )

        # The row carries commands checkpointed during the turn, the fence what
        # the final message declared; each alone loses turns the other keeps.
        commands_executed = extract_commands_executed(
            agent_output=agent_output, row_envelope=_authoritative_envelope,
        )

        context_update_result = None
        _update_contracts_refused: list = []
        if isinstance(parsed_contract, dict):
            # The hook's cwd can resolve to a workspace other than the
            # dispatch's, so the born row's column decides -- unless the row
            # names a project that another workspace owns.
            _update_contracts_workspace = (
                (_bound_dispatch_row or {}).get("workspace")
                or task_info.get("workspace")
            )
            _update_contracts_workspace = _project_owner_workspace(
                _update_contracts_workspace,
                (_bound_dispatch_row or {}).get("dispatch_project"),
                task_info.get("db_path"),
            )
            _update_contracts_task_info = {
                "agent": agent_type,
                "db_path": task_info.get("db_path"),
                "cloud_scope": task_info.get("cloud_scope"),
                "workspace": _update_contracts_workspace,
            }
            _update_contracts_result = process_update_contracts(
                parsed_contract, _update_contracts_task_info
            )
            if _update_contracts_result.get("updated"):
                context_update_result = {
                    "updated": True,
                    "contract": ", ".join(_update_contracts_result.get("contracts", [])),
                }
            _update_contracts_refused = list(_update_contracts_result.get("errors", []))
            if _update_contracts_refused:
                logger.warning(
                    "update_contracts not applied for %s: %s",
                    agent_type, _update_contracts_refused,
                )

        anchor_hits = None
        try:
            from modules.context.anchor_tracker import (
                cleanup_anchors,
                compute_anchor_hits,
                extract_tool_calls_from_transcript,
                load_anchors,
            )
            transcript_path = task_info.get("agent_transcript_path", "")
            # The "unknown" placeholder would key every unidentified dispatch
            # on the same anchors.
            dispatch_agent_id = task_info.get("agent_id", "")
            if dispatch_agent_id == "unknown":
                dispatch_agent_id = ""
            anchors = load_anchors(session_id, agent_type, dispatch_agent_id)
            if anchors and transcript_path:
                tool_calls = extract_tool_calls_from_transcript(transcript_path)
                # No tool calls is no observation, not a 0.0 hit rate.
                if tool_calls:
                    anchor_hits = compute_anchor_hits(tool_calls, anchors)
                    logger.info(
                        "Anchor hits for %s: %d/%d (%.0f%%)",
                        agent_type,
                        anchor_hits.get("hits", 0),
                        anchor_hits.get("total_checked", 0),
                        anchor_hits.get("hit_rate", 0) * 100,
                    )
                else:
                    logger.debug(
                        "No trackable tool calls to check anchors against "
                        "for %s (anchors=%d) -- leaving anchor_hits unmeasured",
                        agent_type, len(anchors),
                    )
                cleanup_anchors(session_id, agent_type, dispatch_agent_id)
        except Exception as exc:
            logger.debug("Anchor hit tracking failed (non-fatal): %s", exc)

        session_context = {
            "timestamp": _dt.now().isoformat(),
            "session_id": session_id,
            "task_id": task_info.get("task_id", "unknown"),
            "agent_id": task_info.get("agent_id", "unknown"),
            "agent": agent_type,
        }
        workflow_metrics = record_workflow(
            task_info,
            agent_output,
            session_context,
            commands_executed=commands_executed,
            context_update_result=context_update_result,
            anchor_hits=anchor_hits,
            transcript_analysis=transcript_analysis,
        )

        response_contract = validate_response_contract(
            agent_output,
            task_agent_id=resolve_agent_id(task_info),
            consolidation_required=False,
            parsed_contract=parsed_contract,
        )
        save_validation_result(task_info, response_contract)

        anomalies = audit_workflow(
            workflow_metrics,
            task_info,
            rejected_sections=(context_update_result or {}).get("rejected", []),
            transcript_analysis=transcript_analysis,
            row_envelope=_authoritative_envelope,
        )
        # Full verdict signals exactly one anomaly per invalidity from the gate
        # and suppresses the two legacy anomalies that re-report it.
        if _full_verdict:
            anomalies.extend(_gate.anomalies)
        else:
            if _validation_anomalies:
                anomalies.extend(_validation_anomalies)
            if not response_contract.valid:
                missing = ", ".join(response_contract.missing) or "none"
                invalid = ", ".join(response_contract.invalid) or "none"
                anomalies.append({
                    "type": "response_contract_violation",
                    "severity": "critical",
                    "message": (
                        f"Agent response contract invalid for {task_info.get('agent', 'unknown')}: "
                        f"missing=[{missing}] invalid=[{invalid}]"
                    ),
                })

        # Circuit anomalies go in before write_episode so they reach the
        # persisted floor `gaia defects` reads; later appends reach only the
        # returned dict.
        if _circuit is not None:
            try:
                from modules.agents import rejection_circuit

                if _turn_key is not None and not _turn_key.per_turn:
                    anomalies.append(
                        rejection_circuit.shared_ceiling_anomaly(
                            agent_type, _circuit,
                        )
                    )
                if _circuit.tripped:
                    anomalies.append(
                        rejection_circuit.circuit_anomaly(agent_type, _circuit)
                    )
                elif _circuit.error:
                    anomalies.append(
                        rejection_circuit.counter_error_anomaly(agent_type, _circuit)
                    )
            except Exception as _circuit_anom_exc:
                logger.warning(
                    "Rejection circuit anomaly not recorded for %s: %s",
                    agent_type, _circuit_anom_exc,
                )

        compliance_result = None
        try:
            from modules.agents.transcript_analyzer import compute_compliance_score
            if transcript_analysis is not None:
                _contract_valid = contract_result.is_valid
                _has_scope_escalation = any(
                    a.get("type") == "scope_escalation"
                    for a in anomalies
                ) if anomalies else False
                _anchor_hit_rate = (
                    anchor_hits.get("hit_rate", 0.0)
                    if anchor_hits else 0.0
                )
                compliance_result = compute_compliance_score(
                    transcript_analysis,
                    contract_valid=_contract_valid,
                    has_scope_escalation=_has_scope_escalation,
                    anchor_hit_rate=_anchor_hit_rate,
                )
                logger.info(
                    "Compliance score for %s: %d (%s)",
                    agent_type, compliance_result.total, compliance_result.grade,
                )
                workflow_metrics["compliance_score"] = {
                    "total": compliance_result.total,
                    "grade": compliance_result.grade,
                    "factors": compliance_result.factors,
                    "deductions": compliance_result.deductions,
                }
        except Exception as exc:
            logger.debug("Compliance score computation failed (non-fatal): %s", exc)

        # A contract-reported failure_report lands on the persisted defect
        # floor, so it must be appended before write_episode.
        try:
            from modules.agents.defect_capture import build_defect_anomaly
            _defect_anomaly = build_defect_anomaly(parsed_contract, agent=agent_type)
            if _defect_anomaly:
                anomalies.append(_defect_anomaly)
                logger.info(
                    "Contract-reported defect captured for %s (severity=%s)",
                    agent_type, _defect_anomaly.get("severity"),
                )
        except Exception as _defect_exc:
            logger.debug("Defect capture skipped (non-fatal): %s", _defect_exc)

        if anomalies:
            logger.warning("%d anomalies detected in workflow", len(anomalies))
            signal_gaia_analysis(anomalies, workflow_metrics)

        workflow_metrics["anomalies_detected"] = len(anomalies)
        workflow_metrics["anomaly_types"] = [a.get("type", "") for a in anomalies]

        # The episode derives its outcome from what the turn claimed, so a cut
        # turn declaring COMPLETE would be stored as a success.
        episode_id = write_episode(
            workflow_metrics,
            anomalies=anomalies if anomalies else None,
            commands_executed=commands_executed,
            outcome_override="failed" if _circuit_tripped else None,
        )

        # Salvage runs before the handoff backstop and keys on the same
        # contract_id, so the salvaged row wins and the backstop's
        # ON CONFLICT DO NOTHING leaves one row.
        _salvage = None
        if _stop_reason_classification == STOP_REASON_TRUNCATION:
            _salvage = host.salvage_truncated_draft(
                parsed_contract=parsed_contract,
                task_info=task_info,
                session_id=session_id,
                plan_task_id=_bound_plan_task_id,
            )

        _captured_contract_id = None
        try:
            from modules.agents.handoff_persister import persist_handoff
            _capture = persist_handoff(
                parsed_contract=parsed_contract,
                agent_output=agent_output,
                task_info=task_info,
                session_id=session_id,
                # The CLI finalize path cannot read the dispatch binding, so
                # attribution is supplied here.
                plan_task_id=_bound_plan_task_id,
            )
            if isinstance(_capture, dict):
                _captured_contract_id = _capture.get("contract_id")
        except Exception as _handoff_exc:
            logger.warning(
                "M4: handoff persistence call failed (non-blocking): %s",
                _handoff_exc,
            )

        try:
            from modules.events.event_writer import EventWriter, AGENT_COMPLETE
            _plan = _resolved_agent_state
            _key_outputs = []
            if parsed_contract and isinstance(parsed_contract.get("evidence_report"), dict):
                _key_outputs = parsed_contract["evidence_report"].get("key_outputs", [])
            _summary = "; ".join(str(o) for o in _key_outputs[:2]) if _key_outputs else ""
            EventWriter().write_event(
                AGENT_COMPLETE, "hook", agent_type,
                _plan or "completed",
                meta={"episode_id": episode_id, "summary": _summary[:200]},
            )
        except Exception:
            pass  # Events are non-critical

        contract_attempts = _circuit.attempt if _circuit is not None else 0

        verbatim_check = validate_verbatim_outputs_consistency(parsed_contract)
        if verbatim_check:
            anomalies.append(verbatim_check)
            logger.info(
                "Verbatim outputs consistency warning for %s: %s",
                agent_type, verbatim_check.get("message", ""),
            )

        _agent_state = _resolved_agent_state

        try:
            from modules.agents.state_tracker import track_transition
            _agent_id = resolve_agent_id(task_info)
            # A per-session resume mapping marks the orchestrator continuing
            # this agent across messages, which must not trip the retry cap.
            _is_resume = host.resume_map_dir is not None and (
                host.resume_map_dir / f"{session_id}.json"
            ).is_file()
            if _agent_state and _agent_id:
                transition_result = track_transition(
                    _agent_id,
                    _agent_state,
                    has_review_phase=False,
                    is_resume=_is_resume,
                )
                if not transition_result.valid:
                    anomalies.append({
                        "type": "illegal_state_transition",
                        "severity": "warning",
                        "message": transition_result.error,
                    })
                    logger.warning(
                        "State transition rejected for %s: %s",
                        agent_type, transition_result.error,
                    )
                elif transition_result.warning:
                    anomalies.append({
                        "type": "state_transition_warning",
                        "severity": "info",
                        "message": transition_result.warning,
                    })
                    logger.info(
                        "State transition warning for %s: %s",
                        agent_type, transition_result.warning,
                    )
        except Exception as exc:
            logger.debug("State transition tracking failed (non-fatal): %s", exc)

        if parsed_contract is not None:
            approval_check = validate_approval_request(parsed_contract, _agent_state)
            if approval_check:
                anomalies.append(approval_check)
                logger.info(
                    "Approval request validation for %s: %s",
                    agent_type, approval_check.get("detail", ""),
                )

        try:
            from modules.agents.skill_injection_verifier import verify_skill_injection
            from modules.audit.workflow_recorder import load_agent_runtime_profile
            agent_profile = load_agent_runtime_profile(agent_type)
            declared_skills = agent_profile.get("skills", [])
            written_paths = [
                tc.arguments.get("file_path", "")
                for tc in (transcript_analysis.tool_sequence if transcript_analysis else [])
                if tc.tool_name in ("Write", "Edit")
            ]
            written_paths = [p for p in written_paths if p]
            # A skill's fingerprint lands in an earlier tool result, which the
            # last message never carries.
            full_transcript_text = (
                read_full_transcript_text(completion.transcript_path)
                if completion.transcript_path
                else agent_output
            )
            if (declared_skills or written_paths) and full_transcript_text:
                skill_check = verify_skill_injection(
                    agent_type, full_transcript_text, declared_skills,
                    written_paths=written_paths,
                )
                if skill_check:
                    anomalies.append(skill_check)
                    logger.info(
                        "Skill injection gap for %s: %s",
                        agent_type, skill_check.get("message", ""),
                    )
        except Exception as exc:
            logger.debug("Skill injection verification failed (non-fatal): %s", exc)

        contract_rejected = _gate.rejected
        contract_rejection_reason = _gate.rejection_reason

        # The cut: not raising the rejection is what ends the retry loop. It
        # certifies nothing -- and the backstop may already have written the
        # fence's self-declared COMPLETE onto a cut-marked row, which the
        # demotion reconciles. A row the agent finalized itself is out of its
        # reach.
        if _circuit_tripped:
            from modules.agents import rejection_circuit

            contract_rejected = False
            _degraded_reason = rejection_circuit.degraded_close_reason(
                _circuit, contract_rejection_reason,
            )
            logger.error(
                "Contract rejection circuit OPEN for %s after %d rejections "
                "(limit %d): closing the turn degraded. The contract is NOT "
                "complete and its row stays unfinalized.",
                agent_type, _circuit.attempt, _circuit.limit,
            )
            try:
                from gaia.store.writer import demote_uncertified_completion

                _demoted = demote_uncertified_completion(
                    _captured_contract_id,
                    db_path=Path(task_info["db_path"])
                    if task_info.get("db_path") else None,
                )
                if _demoted.get("status") == "applied":
                    logger.error(
                        "Rejection circuit: demoted the uncertified COMPLETE "
                        "row %s for %s -- the turn was cut, not completed.",
                        _captured_contract_id, agent_type,
                    )
            except Exception as _demote_exc:
                _demoted = {"status": "error", "reason": str(_demote_exc)}
                logger.error(
                    "Rejection circuit: could NOT reconcile the contract row "
                    "for %s (%s); a row claiming COMPLETE may survive this "
                    "cut.", agent_type, _demote_exc,
                )

        result = {
            "success": True,
            "session_id": session_id,
            "status": "metrics_captured",
            "metrics_captured": True,
            "anomalies_detected": len(anomalies) if anomalies else 0,
            "episode_id": episode_id,
            "context_updated": context_update_result.get("updated", False) if context_update_result else False,
            "response_contract": response_contract.to_dict(),
            "contract_validated": contract_result.is_valid,
            "contract_attempts": contract_attempts,
            "stop_reason": _raw_stop_reason,
            "stop_reason_classification": _stop_reason_classification,
            "contract_gate_source": _gate_source,
        }

        if _salvage:
            result["truncation_salvaged"] = True
            result["salvage_contract_id"] = _salvage.get("contract_id")
            result["salvage_resume_hint"] = _salvage.get("resume_hint")

        if contract_rejected:
            result["contract_rejected"] = True
            result["contract_rejection_reason"] = contract_rejection_reason
            logger.warning(
                "Contract rejected for %s: %s",
                agent_type, contract_rejection_reason.split("\n")[0],
            )

        # `contract_complete: False` is stated because an absent
        # contract_rejected reads like a turn never rejected at all.
        if _circuit_tripped:
            result["status"] = "contract_circuit_open"
            result["contract_circuit_open"] = True
            result["contract_closed_degraded"] = True
            result["contract_complete"] = False
            result["contract_rejection_count"] = _circuit.attempt
            result["contract_rejection_limit"] = _circuit.limit
            result["contract_degraded_close_reason"] = _degraded_reason
            result["contract_row_reconciled"] = _demoted.get("status")
            # The gate's last verdict stays off this notice: it already travels
            # on contract_degraded_close_reason, and handing it back to the
            # subagent is what resumed the cut turn.
            user_message = (
                f"El turno de {agent_type} fue CORTADO por el circuito de "
                f"rechazos tras {_circuit.attempt} rechazos de contrato "
                f"(limite {_circuit.limit}). El turno TERMINO DEGRADADO: no "
                "esta certificado, el contrato NO quedo completo y su fila "
                "persistida no fue finalizada. No trates su ultimo mensaje "
                "como un cierre valido, aunque declare COMPLETE."
            )

        # The summary comes off the envelope the gate judged, so what the user
        # reads and the verdict share one artifact. A rejected or cut turn is
        # excluded: its work is not a valid close. How many subagents ran is
        # unknown here, so the relay-at-one rule stays the orchestrator's.
        if (
            not contract_rejected
            and not _circuit_tripped
            and isinstance(_authoritative_envelope, dict)
        ):
            try:
                from modules.agents.contract_validator import (
                    parse_user_facing_summary,
                )

                _user_summary = parse_user_facing_summary(_authoritative_envelope)
            except Exception as _summary_exc:
                _user_summary = None
                logger.debug(
                    "user_facing_summary relay skipped (non-fatal): %s",
                    _summary_exc,
                )
            if _user_summary:
                result["user_facing_summary"] = _user_summary
                user_message = (
                    f"Resumen de {agent_type} para el usuario: {_user_summary}"
                )

        if _update_contracts_refused:
            result["update_contracts_refused"] = _update_contracts_refused
            _refusal_notice = (
                f"update_contracts from {agent_type} was NOT applied: "
                + "; ".join(_update_contracts_refused)
            )
            user_message = (
                f"{user_message}\n{_refusal_notice}" if user_message else _refusal_notice
            )

        # The repair message replaces the rejected one in everything the
        # orchestrator receives, so the rejected turn's work is preserved and
        # handed back (modules/agents/rejected_turn_relay.py). The branch keys
        # on the gate, not on contract_rejected: a cut turn was rejected, and
        # the accepted-path cleanup would delete its preserved evidence.
        try:
            from modules.agents import rejected_turn_relay
            from modules.agents import rejection_circuit
            _relay_key = rejected_turn_relay.preservation_key(session_id, task_info)
            if _gate.rejected:
                _attempt = _circuit.attempt if _circuit is not None else 1
                _relay = rejected_turn_relay.on_rejection(
                    agent_output,
                    key=_relay_key,
                    rejection_reason=contract_rejection_reason,
                    max_inline_chars=rejection_circuit.inline_budget(
                        _attempt, rejected_turn_relay._MAX_INLINE_CHARS,
                    ),
                )
                if _relay["chars"]:
                    result["preserved_output_path"] = _relay["path"]
                    result["preserved_output_chars"] = _relay["chars"]
                    result["preserved_output_carried_forward"] = _relay["carried_forward"]
                    result["preserved_output_inline_truncated"] = _relay["inline_truncated"]
                if contract_rejected:
                    contract_rejection_reason = _relay["reason"]
                    if _circuit is not None and not _circuit.error:
                        contract_rejection_reason += rejection_circuit.retry_notice(
                            _circuit,
                            dispatch_prompt=(
                                _bound_dispatch_row.get("dispatch_prompt")
                                if _bound_dispatch_row else None
                            ),
                        )
                    result["contract_rejection_reason"] = contract_rejection_reason
                elif _circuit_tripped and _relay["path"]:
                    result["contract_degraded_close_reason"] += (
                        f"\nEvidencia preservada en: {_relay['path']}"
                    )
            else:
                _closed = rejected_turn_relay.on_accepted(agent_output, key=_relay_key)
                if _circuit_key:
                    rejection_circuit.reset(_circuit_key)
                if _closed:
                    result["preserved_output_relayed"] = _closed["relayed"]
                    result["preserved_output_chars"] = _closed["chars"]
        except Exception as exc:
            logger.warning("Rejected-turn relay failed (non-fatal): %s", exc)

        if _circuit_tripped:
            from modules.agents import rejection_circuit

            rejection_circuit.record_circuit_event(
                agent_type,
                _circuit,
                session_id=session_id,
                episode_id=episode_id,
                gate_source=_gate_source,
                preserved_output_path=result.get("preserved_output_path"),
            )

    except Exception as e:
        logger.error("Error in adapt_subagent_stop: %s", e, exc_info=True)
        result = {
            "success": False,
            "error": str(e),
            "status": "partial_update",
        }
        user_message = None

    if _rejection_latched and not result.get("contract_rejected"):
        result["contract_rejected"] = True
        result["contract_rejection_reason"] = _latched_rejection_reason
        logger.error(
            "Contract rejection restored from the latch: the result dict "
            "lost it, so exit_code=2 is taken from the verdict itself.",
        )

    return SubagentStopOutcome(
        result=result,
        rejected=bool(result.get("contract_rejected")),
        user_message=user_message,
    )
