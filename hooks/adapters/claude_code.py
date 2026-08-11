"""
Claude Code Adapter -- concrete HookAdapter for Claude Code v2.1+ hook protocol.

Translates between Claude Code's stdin JSON format and the normalized types
defined in adapters.types. Business logic modules never see Claude Code JSON
directly; they consume and produce normalized types.

Distribution channel detection:
- PLUGIN: CLAUDE_PLUGIN_ROOT env var is set
- NPM: default (symlink to node_modules or direct invocation)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .base import HookAdapter
from .types import (
    AgentCompletion,
    BootstrapResult,
    CompletionResult,
    ConsentRequest,
    ContextResult,
    HookEvent,
    HookEventType,
    HookResponse,
    HostCapability,
    HostDistribution,
    PermissionDecision,
    QualityResult,
    ToolResult,
    ValidationRequest,
    ValidationResult,
    VerificationResult,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dispatch-identity injection (fail-closed DB guards, M1)
# ---------------------------------------------------------------------------

# Environment variable the DB-side dispatch guards read to identify the writing
# agent (gaia.store.writer, gaia.state.permissions, gaia.evidence.store,
# gaia.briefs.store). Defined here as the single injection-side constant.
GAIA_DISPATCH_AGENT_ENV = "GAIA_DISPATCH_AGENT"


def build_dispatch_identity_command(command: str, agent_type: str) -> str:
    """Prefix a subagent Bash command so ``GAIA_DISPATCH_AGENT`` reaches the CLI.

    The DB guards fail OPEN when ``GAIA_DISPATCH_AGENT`` is unset (that is the
    human-CLI / orchestrator-main-session path). To make them fail CLOSED for a
    dispatched subagent, the agent's identity must reach the ``gaia`` CLI
    subprocess -- and the only lever a PreToolUse hook has over a subprocess's
    environment is the command string it executes. So we export the identity at
    the front of the command line:

        ``export GAIA_DISPATCH_AGENT=<agent>; <original command>``

    An ``export ...;`` statement is used deliberately rather than a bare
    ``VAR=x <cmd>`` word prefix: a bare prefix binds the variable to ONLY the
    first stage of a compound command, so ``GAIA_DISPATCH_AGENT=x cd /repo &&
    gaia ...`` would leave ``gaia`` without the variable. ``export`` makes it
    visible to every stage and every subprocess of that single Bash invocation
    (each tool call is a fresh shell, so the scope is exactly this one command).

    ``agent_type`` is the HARNESS-provided dispatch identity, never a value the
    agent supplies; the caller only invokes this for a real subagent. Returns
    the command unchanged when ``agent_type`` is empty (no identity to assert --
    the guards then stay fail-open for that command, the conservative fallback).

    Note (accepted limitation): a deliberately adversarial agent could append an
    inline ``GAIA_DISPATCH_AGENT=orchestrator`` assignment to a later stage of
    its own command and shadow the exported value for that stage. This injection
    closes the ACCIDENTAL over-authority gap (subagents writing as human by
    default); a forged inline override is a distinct, adversarial threat still
    covered by the T3 approval layer on the underlying mutation.
    """
    agent = (agent_type or "").strip()
    if not agent:
        return command
    return f"export {GAIA_DISPATCH_AGENT_ENV}={shlex.quote(agent)}; {command}"

# Claude Code's PreToolUse responses nest their permission fields under this
# top-level key. The literal shape is OWNED by this adapter layer: business
# logic must never index it directly. The accessors below let business modules
# read or augment an already-formatted host response without coupling to the
# key names (AC-2: hookSpecificOutput lives only in adapters/).
_HOOK_SPECIFIC_OUTPUT = "hookSpecificOutput"

# Claude Code's two distribution channels and the env var that distinguishes
# them. These host-specific names are OWNED by this adapter (Gap 2 / brief #88):
# the core carries an opaque HostDistribution and never enumerates these values
# nor reads CLAUDE_PLUGIN_ROOT. A host with a different distribution model
# declares its own channels in its own adapter, with no change to the core.
_CHANNEL_NPM = "npm"
_CHANNEL_PLUGIN = "plugin"
_PLUGIN_ROOT_ENV_VAR = "CLAUDE_PLUGIN_ROOT"


def read_permission_decision(host_output: Dict[str, Any]) -> Optional[str]:
    """Return the permissionDecision ("allow"/"deny"/"ask") from a host response.

    Reads the Claude Code ``hookSpecificOutput`` shape produced by this adapter.
    Returns None when the response is not a permission-decision response.
    """
    if not isinstance(host_output, dict):
        return None
    return host_output.get(_HOOK_SPECIFIC_OUTPUT, {}).get("permissionDecision")


def read_permission_reason(host_output: Dict[str, Any]) -> str:
    """Return the permissionDecisionReason from a host response, or "" if absent."""
    if not isinstance(host_output, dict):
        return ""
    return host_output.get(_HOOK_SPECIFIC_OUTPUT, {}).get(
        "permissionDecisionReason", ""
    )


def inject_updated_input(
    host_output: Dict[str, Any], updated_input: Dict[str, Any]
) -> Dict[str, Any]:
    """Attach ``updatedInput`` to an already-formatted host response, in place.

    Used when business logic must propagate a modified tool input (e.g. a
    footer-stripped command) through an existing block/ask response so the
    modification survives the native permission dialog. Returns the same dict
    for convenience. No-op when ``host_output`` is not a host response.
    """
    if not isinstance(host_output, dict):
        return host_output
    host_output.setdefault(_HOOK_SPECIFIC_OUTPUT, {})["updatedInput"] = updated_input
    return host_output


# ---------------------------------------------------------------------------
# stop_reason isolation (brief contract-as-managed-data-agent-contract-handoff
# -agnostico-por-cli, decision #5 / M5 / AC-11).
#
# Claude Code's model-level ``stop_reason`` ("max_tokens", "end_turn", ...) is
# a host-specific signal. Interpreting what it MEANS for a broken or
# incomplete agent_contract_handoff envelope -- "max_tokens" implies the turn
# was cut off by the token budget (not the agent's choice, a salvage
# candidate for T11's truncation rescue); "end_turn" (or anything else)
# implies the agent had room to finish and stopped anyway (a genuine
# violation) -- is host-specific judgment. It lives HERE, in the adapter,
# ONLY. The portable core (gaia.contract.validator, gaia.contract.crosscheck,
# M1) never imports this function, never sees stop_reason, and validates an
# envelope's shape/cross-check IDENTICALLY whether stop_reason is present,
# absent, or any value at all -- see tests/contract/test_stop_reason_adapter.py.
# ---------------------------------------------------------------------------
STOP_REASON_TRUNCATION = "truncation"
STOP_REASON_VIOLATION = "violation"
STOP_REASON_UNKNOWN = "unknown"

_STOP_REASON_MAX_TOKENS = "max_tokens"
_STOP_REASON_END_TURN = "end_turn"
_STOP_REASON_TOOL_USE = "tool_use"

# Reasons that mean the turn was CUT rather than finished: the model was still
# mid-work when it stopped producing. "max_tokens" is the budget cut;
# "tool_use" is the harness cut -- the last assistant message requested a tool,
# the tool result landed, and no further message ever came, so the turn never
# reached end_turn and its contract was never emitted.
_TRUNCATION_STOP_REASONS = frozenset({_STOP_REASON_MAX_TOKENS, _STOP_REASON_TOOL_USE})


def classify_stop_reason(stop_reason: Optional[str]) -> str:
    """Map a Claude Code ``stop_reason`` to its adapter-owned semantic class.

    ``"max_tokens"`` -> ``STOP_REASON_TRUNCATION``: the turn was cut off by
        the token budget, not chosen by the agent. A broken/incomplete
        contract under this reason is a salvage candidate (T11), not a hard
        violation.
    ``"tool_use"`` -> ``STOP_REASON_TRUNCATION``: the harness cut. The last
        assistant message ended requesting a tool, its result landed, and the
        model never produced another message -- so the turn never reached
        ``end_turn`` and never emitted its contract. Like the budget cut, this
        is not the agent's choice and not a violation.

        SCOPE, measured: on the observed cut path this classification changes
        nothing, because the hook does not run at all -- ``SubagentStop`` never
        fires, so no code here is reached (no ``harness_event``, no
        ``episodes`` row). What this mapping fixes is the case where the hook
        DOES run and the payload carries ``stop_reason="tool_use"``: a resumed
        or replayed turn, a host that delivers the stop reason on a later
        lifecycle event, or any future host that fires SubagentStop on a cut.
        For the cut the harness swallows entirely, the detection lives on the
        orchestrator side instead -- see
        ``modules.agents.task_result_observer``.
    ``"end_turn"`` -> ``STOP_REASON_VIOLATION``: the agent had room to finish
        and stopped anyway. A broken/incomplete contract under this reason is
        a genuine violation.
    Anything else (``None``, empty, or an unrecognized reason)
        -> ``STOP_REASON_UNKNOWN``: the conservative default. A caller that
        gates on this classification should treat "unknown" the same as a
        violation (fail closed) rather than assume a salvage-worthy
        truncation it cannot confirm.

    This function is the SOLE owner of the max_tokens/end_turn mapping
    (decision #5). ``gaia.contract.validator`` and ``gaia.contract.crosscheck``
    never import it and never branch on stop_reason themselves.
    """
    if stop_reason in _TRUNCATION_STOP_REASONS:
        return STOP_REASON_TRUNCATION
    if stop_reason == _STOP_REASON_END_TURN:
        return STOP_REASON_VIOLATION
    return STOP_REASON_UNKNOWN


# ---------------------------------------------------------------------------
# M4 full-verdict contract gate (brief contract-as-managed-data-agent-contract
# -handoff-agnostico-por-cli, T16 / AC-9).
#
# The live SubagentStop gate historically enforced only 3 structural cases
# (Option B: missing block / missing agent_status / bad agent_state) and let
# EVERYTHING else exit 0 + a bare anomaly, never delivering the rich repair
# message. T16 replaces that with a FULL-VERDICT gate driven by the SINGLE
# portable core (gaia.contract.crosscheck.validate == form + cross-check), so
# the gate, the CLI validate-on-write, and the finalize writer all agree on ONE
# verdict.
#
# It is guarded by a RAMP FLAG that now DEFAULTS ON (cutover from the original
# default-OFF staging):
#   - unset / empty / any non-falsy value -> full-verdict: a previously-exit-0
#     invalid envelope now exits 2 with the rich repair message on stderr,
#     signaling EXACTLY ONE anomaly per invalidity (one FormError /
#     CrossCheckError -> one anomaly), never the historical double
#     (contract_validation_failure + response_contract_violation).
#   - explicit falsy ({"0","false","no","off"}) -> byte-identical legacy 3-case
#     behavior (the one-env-var rollback path, T17).
#
# WHY the default flipped: in 3-case mode the ONLY thing that forces a subagent
# to repair its handoff (exit 2) is one of three structural cases (no block, no
# agent_status, bad agent_state). A handoff that PARSES but is otherwise
# incomplete or drifted (missing evidence_report keys, missing next_action,
# malformed agent_id, missing consolidation_report) produced a CRITICAL
# response_contract_violation anomaly WITHOUT forcing repair -- the turn ended
# exit 0 and recovery fell to the orchestrator via SendMessage. Full-verdict
# closes that gap; a handoff built through the `gaia contract` CLI already
# passed this SAME core at finalize, so the correct-path agent is never
# rejected -- only a genuinely broken persisted envelope is.
#
# stop_reason (T10/T11) decides salvage-vs-violation: a max_tokens truncation
# is NOT hard-rejected here (the T11 fast-path / T9 backstop already capture a
# degraded row; rejecting would treat a salvaged truncation as a violation and
# double-signal it). The core itself never sees stop_reason (decision #5).
# ---------------------------------------------------------------------------
GATE_RAMP_ENV_VAR = "GAIA_CONTRACT_FULL_VERDICT_GATE"
# Explicit falsy tokens that force the legacy 3-case gate (the rollback path).
# Everything else -- unset, empty, or any other value -- selects full-verdict.
_GATE_FALSE_VALUES = {"0", "false", "no", "off"}

GATE_MODE_THREE_CASE = "three_case"
GATE_MODE_FULL_VERDICT = "full_verdict"

# Dotted event category written to harness_events when the gate REJECTS a turn.
# A rejection is invisible everywhere else: it is not observable in PostToolUse
# (a subagent that repairs comes back with a closed row, and one that never
# repairs is indistinguishable from a harness cut, which agent.cut already
# claims), so the verdict is only knowable where the gate runs. Severity is
# error -- strictly above info -- because the triage reader
# (gaia.store.reader._query_orchestrator_defects) selects the orchestrator
# channel by a severity threshold, never by an enumeration of event types.
CONTRACT_REJECTED_EVENT = "agent.contract_rejected"

# How much of the rejection message to keep on the event payload, in characters.
# Enough to recognize WHICH rejection it was without storing the whole repair
# block, which is long by design.
_REJECTION_PREVIEW_CHARS = 400

# Dotted event category written to harness_events when a nascent-row birth is
# rejected for an ANOMALOUS reason (see _is_anomalous_dispatch_binding_rejection
# below). Severity is warning, not error: a rejected birth never blocks the
# dispatch (modules.agents.dispatch_binding.DispatchBindingError is always
# swallowed), so nothing here forces a repair the way CONTRACT_REJECTED_EVENT
# does -- it only makes an otherwise-silent misdispatch (a typo'd task_id=, a
# verifier missing its parent_handoff_id=) visible to triage instead of
# vanishing into a logger.info line no one reads by default.
DISPATCH_BINDING_REJECTED_EVENT = "dispatch.binding_rejected"

# DispatchBindingError.reason codes that are ALWAYS anomalous: each of these
# names a binding that carried SOME plan/verifier coordinate and failed to
# resolve it, never a turn that simply carried none. Mirrors the reason list
# in modules.agents.dispatch_binding.DispatchBindingError's own docstring.
_ALWAYS_ANOMALOUS_BINDING_REASONS = frozenset({
    "plan_task_id_unresolved",
    "plan_task_id_not_dispatchable",
    "verifier_requires_parent_handoff_id",
    "parent_handoff_id_unresolved",
})

# The one reason code that is CONDITIONALLY anomalous: a task_execution
# dispatch with no task_id= token at all. This is the legitimate shape of a
# free-standing turn (investigation, memory) that carries no plan_id= either --
# see _is_anomalous_dispatch_binding_rejection.
_CONDITIONAL_BINDING_REASON = "task_execution_requires_plan_task_id"


def full_verdict_gate_enabled() -> bool:
    """Whether the M4 full-verdict gate is active (DEFAULT ON).

    Reads ``GAIA_CONTRACT_FULL_VERDICT_GATE``. Unset / empty / any value that is
    NOT an explicit falsy token -> True -> the full-verdict gate, driven by the
    SINGLE portable core (gaia.contract.crosscheck) that also backs the CLI
    validate-on-write and finalize. One of
    {"0","false","no","off"} (case-insensitive) -> False -> the legacy 3-case
    Option B gate, still fully supported as the one-env-var rollback path (T17).

    The default was flipped ON to close the SubagentStop enforcement gap: in
    3-case mode a handoff that PARSES (agent_status + valid agent_state present)
    but is otherwise incomplete/drifted produced a critical
    response_contract_violation anomaly WITHOUT forcing repair (exit 0), so
    recovery fell to the orchestrator via SendMessage. Full-verdict forces the
    subagent to repair the handoff (exit 2 + rich message) before its turn ends.
    """
    return (
        os.environ.get(GATE_RAMP_ENV_VAR, "").strip().lower()
        not in _GATE_FALSE_VALUES
    )


@dataclass(frozen=True)
class ContractGateVerdict:
    """Outcome of the SubagentStop contract gate for one turn.

    Attributes:
        rejected: True -> the hook returns exit_code=2 for this turn.
        rejection_reason: the message routed to stderr (by
            ``subagent_stop._handle_subagent_stop`` via
            ``contract_rejection_reason``). In full-verdict mode this is the
            rich, canonical repair message; in 3-case mode it is the legacy
            Option B reason. Empty when not rejected.
        anomalies: one anomaly dict per DISTINCT invalidity (full-verdict mode
            only), each typed off the NAMED FormErrorCode / CrossCheckErrorCode
            enum (T1). Empty in 3-case mode -- that mode's anomalies stay on the
            legacy validate_contract / validate_response_contract path,
            unchanged.
        mode: GATE_MODE_THREE_CASE or GATE_MODE_FULL_VERDICT.
        salvaged_truncation: True when the envelope was invalid but the turn was
            a max_tokens truncation; the gate then does NOT hard-reject (the T11
            fast-path / T9 backstop already capture a degraded row).
    """

    rejected: bool
    rejection_reason: str
    anomalies: Tuple[Dict[str, Any], ...]
    mode: str
    salvaged_truncation: bool = False


def _gate_anomaly(agent_type: str, code: str, field: str, detail: str) -> Dict[str, Any]:
    """Build one anomaly per invalidity, typed off the NAMED core error code (T1).

    The anomaly carries the enum code (AGENT_ID_FORMAT, PLAN_STATUS,
    VERIFICATION_RESULT, MISSING_FIELD, APPROVAL_ID_NOT_PENDING) rather than the
    retired free-text token strings, so downstream consumers key on the stable
    enum, not on prose.
    """
    loc = f" [{field}]" if field else ""
    return {
        "type": "contract_gate_violation",
        "code": code,
        "field": field,
        "severity": "critical",
        "message": f"Contract invalid for {agent_type} {code}{loc}: {detail}".rstrip(),
    }


# ---------------------------------------------------------------------------
# Blind-verification gate (plan 34 task 7 -- finalize gate keyed on plan_task_id)
# ---------------------------------------------------------------------------
#
# Deliberately NOT part of validate_form / crosscheck.validate: those are the
# portability core (gaia.contract.validator / gaia.contract.crosscheck), shared
# by the CLI and the finalize writer -- they enforce SHAPE
# only and must never learn "who is allowed to say COMPLETE" (that would break
# the portability contract tested by tests/contract/test_validator_portable.py).
# The blind-verification check is an adapter-layer concern -- it lives HERE,
# applied identically to BOTH gate paths (ramp-ON full-verdict and ramp-OFF
# three-case) so neither is a bypass.
#
# KEYED ON plan_task_id, NOT ROLE, NOT KIND: this is the redesign's core. The
# decision is a function of the DISPATCH BINDING, not of who the emitting agent
# is or what label its turn carries:
#
#   * A turn WHOSE BINDING CARRIES A plan_task_id is a plan-task-bound producer
#     turn. It may NOT self-COMPLETE -- it is forced to NEEDS_VERIFICATION so an
#     independent (blind) verifier confirms the increment. The producer proposes
#     evidence_report.verification.result; a separate verifier turn -- which
#     binds to the producer via parent_handoff_id and therefore carries NO
#     plan_task_id of its own -- is what promotes the increment to COMPLETE.
#   * A turn with NO plan_task_id (investigation, memory, a free-standing
#     verifier turn) is NOT bound to a plan task, so blind verification does not
#     apply: it may self-COMPLETE. This is why an unbound memory turn reaches
#     COMPLETE even when the agents/ tree ships a seeded verifier -- the old
#     registry-armed role gate would have wrongly blocked it.
#
# The former verifier-registry coupling (verifier_fleet / is_verifier) is gone
# from this gate on purpose: keying on role made every non-verifier COMPLETE a
# violation the moment the registry armed, which contradicts "an unbound turn
# self-completes". The registry infrastructure still exists (skill injection,
# dispatch-side role detection); the FINALIZE gate simply no longer consults it.
#
# PROPOSE, NOT COMPLETE: this function only gates COMPLETE. A producer
# transitioning to NEEDS_VERIFICATION is never touched here -- it is not a
# violation to propose NEEDS_VERIFICATION (that is exactly the point of the
# state), and the shape core never requires evidence_report.verification on a
# non-COMPLETE status, so a proposed verification.result alongside
# NEEDS_VERIFICATION is carried through unevaluated -- the gate never accepts
# NEEDS_VERIFICATION as a completed/done state regardless of that proposed value.
def _blind_verification_required(
    agent_state: str, plan_task_id: Optional[int]
) -> Optional[str]:
    """Return a rejection reason iff a COMPLETE turn is bound to a plan task
    (its dispatch binding carries a ``plan_task_id``) and therefore must be
    blind-verified rather than self-completed. ``None`` means no violation --
    either the status is not COMPLETE, or the turn carries no ``plan_task_id``
    (an investigation / memory / free-standing turn, free to self-COMPLETE).

    The decision is a pure function of ``(agent_state, plan_task_id)``: it does
    NOT consult the emitting agent's role or the turn's ``kind``.
    """
    if agent_state != "COMPLETE":
        return None
    if plan_task_id is None:
        # UNBOUND: no plan task -- self-COMPLETE is permitted (today's behavior
        # for investigation / memory turns, now keyed explicitly on the binding).
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
    """The legacy Option B gate: reject the 3 critical structural cases, PLUS
    the blind-verification check (plan 34 task 7).

    Byte-identical to the pre-T16 inline gate for the 3 original structural
    cases, preserving today's behavior exactly there (AC-10). The ONE
    deliberate addition is ``_blind_verification_required`` below: the locked
    decision is "enforce in BOTH ramp paths -- ramp-OFF is not a bypass", so a
    plan-task-bound COMPLETE is rejected here too, not only in the full-verdict
    path. Still produces NO anomalies for the 3 original cases -- those stay on
    the legacy validate_contract / validate_response_contract path, unchanged;
    the blind-verification check reports via its own dedicated reason string.
    """
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
            "[CONTRACT REJECTED] agent_status block missing from agent_contract_handoff.\n"
            "The agent_contract_handoff block must include an agent_status object with "
            "agent_state, agent_id, pending_steps, and next_action."
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
    """Compute the SubagentStop contract gate verdict for one turn (T16 / AC-9).

    Pure decision: it reads the envelope and returns the verdict, and writes
    nothing. Telemetry for a rejection is layered on by the public
    :func:`evaluate_contract_gate` wrapper so the verdict logic stays free of
    side effects.

    Args:
        parsed_contract: the parsed agent_contract_handoff envelope dict, or
            None when no parseable block was found.
        agent_type: the emitting agent (for anomaly messages).
        plan_task_id: the plan task this turn is bound to (from the dispatch
            binding), or None for an unbound turn. Drives the blind-verification
            gate (plan 34 task 7): a bound COMPLETE is forced to
            NEEDS_VERIFICATION; an unbound turn may self-COMPLETE. Keyed on the
            binding, NOT on the agent's role or the turn's kind.
        stop_reason_classification: the ALREADY-resolved T10 classification
            (STOP_REASON_TRUNCATION / _VIOLATION / _UNKNOWN). Read, not
            recomputed -- decides salvage-vs-violation.
        ramp_enabled: None -> read the ramp flag from the environment. When
            False, returns the legacy 3-case verdict. When True, returns the
            full-verdict verdict from the single portable core.
        db_path: optional gaia.db path for the layer-2 cross-check.
        envelope_source: where ``parsed_contract`` was actually read from.
            The SubagentStop gate always passes ``"row"`` (the turn's
            persisted dispatch row) since the fence was retired as an input to
            the close; ``"declaration"`` remains the default for the CLI's
            validate-on-write, which does validate an envelope handed to it
            directly. Forwarded to ``gaia.contract.crosscheck.validate`` so a
            row-sourced envelope that fails to parse is reported as the row
            failing, not as "no fence in the response".

    Returns:
        ContractGateVerdict.
    """
    if ramp_enabled is None:
        ramp_enabled = full_verdict_gate_enabled()

    if not ramp_enabled:
        return _three_case_verdict(parsed_contract, agent_type, plan_task_id)

    # Full-verdict: the SINGLE core (form + cross-check) is the SSOT verdict.
    from gaia.contract.crosscheck import validate as _core_validate

    _db = Path(db_path) if db_path else None
    result = _core_validate(parsed_contract, db_path=_db, source=envelope_source)
    if result.ok:
        # The portability core (form + cross-check) is shape-valid. Layer the
        # blind-verification check on top (plan 34 task 7) -- deliberately
        # OUTSIDE validate_form/crosscheck.validate, which must stay role- and
        # binding-blind (the portability contract). See
        # _blind_verification_required for the plan_task_id semantics.
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

    # Salvage-vs-violation (T10/T11): a max_tokens truncation is NOT a hard
    # violation -- the turn was cut off by the token budget and the T11
    # fast-path / T9 backstop already capture a degraded row. Rejecting here
    # would treat a salvaged truncation as a violation and double-signal it.
    if stop_reason_classification == STOP_REASON_TRUNCATION:
        return ContractGateVerdict(
            False, "", (), GATE_MODE_FULL_VERDICT, salvaged_truncation=True
        )

    # One anomaly per invalidity, typed off the NAMED enum (T1). result.errors
    # is form errors first (the core already enforces "one code per invalidity")
    # then cross-check errors.
    anomalies = tuple(
        _gate_anomaly(
            agent_type,
            err.code.value,
            getattr(err, "field", ""),
            getattr(err, "detail", ""),
        )
        for err in result.errors
    )

    # The RICH repair message is delivered to stderr via contract_rejection_reason.
    # The form layer's message is ALWAYS the canonical rich block; append the
    # cross-check guidance when layer 2 was the (only) failure.
    repair = result.form.repair_message
    if result.crosscheck.repair_message:
        repair = f"{repair}\n\n{result.crosscheck.repair_message}"

    # Group the specific defects BY NATURE instead of one flat "; "-joined list,
    # so the agent reads WHAT is wrong at a glance. MISSING_FIELD errors are
    # fields left out ("Faltan:"); the value-shape codes (AGENT_ID_FORMAT,
    # PLAN_STATUS, VERIFICATION_RESULT, APPROVAL_ID_NOT_PENDING) are fields
    # present but wrong ("Inválidos:"). Partition is MISSING vs. everything-else
    # so a future code is never silently dropped. An empty group is omitted, and
    # each rendered line still carries the raw code string as a substring
    # (e.g. "AGENT_ID_FORMAT"), which downstream assertions and log scrapers rely
    # on. Falls back to the flat summary if partitioning yields nothing.
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

    # Honest-failure signpost: a VERIFICATION_RESULT defect means a COMPLETE was
    # emitted without a genuine pass. Point at the honest path -- retry or block
    # and record the failure -- rather than nudging toward faking a "pass".
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
    """Record a gate rejection as a defect in harness_events. Never raises.

    Writes through ``EventWriter().write_event`` -- the same append-only
    ``harness_events`` path ``agent.cut`` uses. That channel is deliberate: it
    takes no ``episode_id``, so recording a rejection never needs a parent
    ``episodes`` row and never invents one. A rejection typically happens on a
    turn whose episode does not exist yet, and fabricating a turn to satisfy a
    foreign key would falsify the history the triage reads.

    Strictly observational: the caller ignores the outcome and every failure is
    swallowed, so the gate's verdict and the ``exit 2`` that forces the subagent
    to repair are unaffected by anything that happens here.
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
    """Evaluate the SubagentStop contract gate and record a rejection as a defect.

    The verdict is computed by :func:`_contract_gate_verdict` and returned
    unchanged; a rejecting verdict additionally lands one ``harness_events`` row
    of type :data:`CONTRACT_REJECTED_EVENT` at severity ``error``. A salvaged
    truncation does not reject, so it is not recorded here -- the T11 fast-path /
    T9 backstop already capture that turn.

    See :func:`_contract_gate_verdict` for the argument semantics, including
    ``envelope_source``.
    """
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


# ---------------------------------------------------------------------------
# Row-only SubagentStop gate (step 2 of the source-of-truth migration: the
# fence is retired as an input to the close).
#
# Originally the gate validated ONLY the fence -- the
# ```agent_contract_handoff``` block the model re-types from memory into its
# final message -- while the finalized ``agent_contract_handoffs`` row (the
# actual sequence of ``gaia contract`` calls the turn ran) was a parallel,
# unconsulted record. Step 1 inverted that and kept the fence as a fallback.
# This step removes the fallback: the PERSISTED row is now the only thing this
# gate consults.
#
# WHY THE FALLBACK WENT. It never rescued WORK -- work is in the row if the
# turn checkpointed and absent if it did not, and no fence can supply an
# investigation that was never written. All it ever supplied was the CLOSING
# ENVELOPE for a turn that failed to write one, which is precisely how a
# non-compliant turn presented itself as compliant at the last second. It also
# sits at the END of the final message, so a truncated message loses it first
# -- it was weakest in the very case cited to justify it. The gap between a
# turn's last checkpoint and a cut is recovered by RESUMING the agent (which
# keeps its transcript), not by re-reading its last paragraph.
#
# Three exhaustive cases, all row-sourced:
#   - the row is reachable and was cleanly closed by the agent's own finalize
#     (see _row_cleanly_finalized): its persisted envelope goes through the
#     exact same core (gaia.contract.crosscheck.validate +
#     _blind_verification_required) the fence used to go through, so
#     accept/reject is unchanged -- only the SOURCE of the envelope changed.
#   - the row is reachable but was never cleanly finalized: reject
#     (_row_unfinalized_verdict).
#   - no row is reachable at all: reject (_row_missing_verdict). A turn that
#     did not close itself SHOULD read as not closed.
# A harness truncation still excuses all three: it is not the agent's
# violation and the T11 salvage / T9 backstop already capture that turn.
#
# NOTE on what the fence is still read for, deliberately: it remains one of
# four identity lanes in ``resolve_minted_agent_id`` -- i.e. a hint about
# WHICH row is this turn's own. That cannot make an unclosed turn read as
# closed (the row it points at must still be cleanly finalized on its own
# evidence), and removing it would strand a born-but-unclaimed turn with no
# reachable row at all.
# ---------------------------------------------------------------------------

GATE_SOURCE_ROW = "row"
GATE_SOURCE_ROW_UNFINALIZED = "row_unfinalized"
GATE_SOURCE_ROW_MISSING = "row_missing"


def _row_gate_candidate(
    *, bound_dispatch_row: Optional[dict], db_path: Optional[Path] = None,
) -> Optional[dict]:
    """The FULL ``agent_contract_handoffs`` row for this turn's dispatch
    binding, or None when no binding was resolvable at all.

    ``bound_dispatch_row`` (from :func:`ClaudeCodeAdapter._resolve_dispatch_row`,
    whose harness-stamped lane returns a FULL row while its identity lanes
    return only the projection) carries at minimum
    ``{id, contract_id, agent_id, agent_state, plan_task_id}``, enough for the
    blind-verification binding it was built for but not the two columns the
    row-first gate needs: ``raw_handoff_json`` (the persisted envelope) and
    ``cut_reason`` (whether the agent's OWN finalize closed it, vs. a reap /
    backstop / salvage). Re-fetched here by the SAME ``contract_id`` (the
    UNIQUE idempotency key), one extra read, so ``_resolve_dispatch_row``
    itself never has to widen its SELECT for a concern only this caller has.
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
    """True iff this row was closed by the agent's OWN ``gaia contract
    finalize`` -- the schema's own definition (schema.sql, agent_contract_
    handoffs.cut_reason column comment): ``cut_reason IS NULL`` means the turn
    closed cleanly under its own finalize; ``insert_dispatched_handoff`` stamps
    ``'never_finalized'`` at BIRTH and only ``finalize_agent_contract_handoff``
    called WITHOUT a ``cut_reason`` argument clears it (verified empirically:
    a nascent row reads ``agent_state='DISPATCHED'``,
    ``cut_reason='never_finalized'``; the agent's own finalize is the only
    writer that lands ``cut_reason=NULL``). The ``agent_state != 'DISPATCHED'``
    check is a belt-and-braces sanity check that should never diverge from
    ``cut_reason`` -- the writer sets both together in the same UPSERT.
    """
    return (
        full_row.get("agent_state") != "DISPATCHED"
        and full_row.get("cut_reason") is None
    )


def _parse_row_envelope(full_row: Dict[str, Any]) -> Any:
    """Best-effort JSON parse of a row's ``raw_handoff_json``.

    Returns None on a missing/unparseable value -- validate_form already
    reports a non-dict envelope as a single MISSING_FIELD
    (``agent_contract_handoff``), so an unreadable persisted envelope is
    correctly REJECTED by the core, never silently treated as "no row".
    """
    try:
        return json.loads(full_row.get("raw_handoff_json") or "null")
    except (TypeError, ValueError):
        return None


def _row_unfinalized_verdict(
    agent_type: str, full_row: Dict[str, Any], gate_mode: str,
) -> ContractGateVerdict:
    """The turn's OWN dispatch row exists but was never cleanly closed by
    ``gaia contract finalize`` -- reject.

    This is the fix for the measured gap this migration exists to close: a
    fence that types a clean COMPLETE from memory is not evidence the agent
    actually ran finalize -- the row is that evidence, and its absence (or its
    ``cut_reason`` marking a reap/backstop/salvage instead of a clean close)
    is authoritative.
    """
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
    """No dispatch row is reachable for this turn at all -- reject.

    Formerly the fence's branch: with no row to read, the gate validated the
    ```agent_contract_handoff``` block in the response text instead. That is
    the exact substitution this migration removes, so the case now rejects on
    its own terms.

    It is a REJECTION, not a dead end. The turn repairs it the same way it
    repairs any other: run ``gaia contract finalize --draft-id <contract_id>``
    on the draft the dispatch kernel named. That converges the born row, and
    ``ClaudeCodeAdapter._resolve_dispatch_row`` reaches it on the retry --
    through the harness-stamped lane, or through the identity lane that reads
    the ``agent_id`` the turn itself declares. A turn that received no kernel
    at all runs ``gaia contract init`` first, whose mint report the transcript
    lane recovers.
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

    This is the ONE place the row lookup happens; both
    :func:`_resolve_subagent_stop_gate_full` (the gate's own verdict) and
    nonce preservation (``ClaudeCodeAdapter.adapt_subagent_stop``, which reads
    the envelope for a wholly different purpose -- which pending approval_id
    to keep alive -- but must agree on WHERE that envelope came from) call
    this instead of each re-implementing it, so the two can never diverge on
    which turn's row counts.

    Returns ``(source, full_row)`` where ``source`` is one of
    :data:`GATE_SOURCE_ROW`, :data:`GATE_SOURCE_ROW_UNFINALIZED`,
    :data:`GATE_SOURCE_ROW_MISSING`, and ``full_row`` is the fetched dispatch
    row for the first two, else None.

    It takes no ``stop_reason_classification``: truncation used to select the
    fence here and now only softens the VERDICT, which keeps this function a
    pure statement about the row.
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
    """The row-only SubagentStop gate: locate the turn's own dispatch row and
    validate ITS persisted envelope. Nothing in the agent's response text is
    read. Returns ``(verdict, source, envelope_used)`` where ``source`` is one
    of :data:`GATE_SOURCE_ROW`, :data:`GATE_SOURCE_ROW_UNFINALIZED`,
    :data:`GATE_SOURCE_ROW_MISSING`, and ``envelope_used`` is the envelope
    this call treated as authoritative (the row's parsed ``raw_handoff_json``,
    or None when no row was reachable) -- exposed so a caller with a DIFFERENT
    reason to read the envelope (nonce preservation) reads from the exact same
    place the verdict did, instead of re-deriving its own notion of
    "authoritative" and risking the two disagreeing. ``source`` is otherwise
    for logging/telemetry only, never branched on by a caller.
    :func:`resolve_subagent_stop_gate` is the stable 2-tuple public wrapper.

    Three exhaustive cases:

      1. A dispatch row is reachable (``bound_dispatch_row``, resolved by the
         caller -- see ``ClaudeCodeAdapter._resolve_dispatch_row``) AND it was
         cleanly finalized (:func:`_row_cleanly_finalized`): its persisted
         envelope is validated through the identical core the fence used to go
         through (:func:`evaluate_contract_gate`).
      2. A dispatch row is reachable but was NOT cleanly finalized: reject via
         :func:`_row_unfinalized_verdict`. This is the case a well-formed
         fence used to paper over -- and the case where an approval mirrored
         onto the row via ``gaia contract fill`` (but never closed by
         ``finalize``) still counts as this turn's own record, not a lost one.
      3. No dispatch row is reachable at all: reject via
         :func:`_row_missing_verdict`.

    A harness truncation (``STOP_REASON_TRUNCATION``) softens cases 2 and 3 to
    a non-rejecting, ``salvaged_truncation`` verdict: the cut is not the
    agent's violation and the T11 salvage / T9 backstop already capture the
    turn, so rejecting would double-signal it. That is the SAME excuse
    :func:`_contract_gate_verdict` already applies to an invalid envelope in
    case 1, applied at the one seam where the row itself is what is missing.
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
    """Stable 2-tuple public wrapper over :func:`_resolve_subagent_stop_gate_full`.

    Returns ``(verdict, source)`` -- see the full function for the three-case
    ordering and argument semantics. Callers that also need to know WHICH
    envelope decided the verdict (nonce preservation) call the full function
    directly instead of re-deriving the same lookup a second time.
    """
    verdict, source, _envelope_used = _resolve_subagent_stop_gate_full(
        agent_type=agent_type,
        plan_task_id=plan_task_id,
        stop_reason_classification=stop_reason_classification,
        ramp_enabled=ramp_enabled,
        bound_dispatch_row=bound_dispatch_row,
        db_path=db_path,
    )
    return verdict, source


def _is_anomalous_dispatch_binding_rejection(
    reason: str, binding: Dict[str, Any],
) -> bool:
    """Whether a rejected nascent-row birth is worth surfacing as an event.

    A rejected birth is the correct, silent outcome for a free-standing turn
    (investigation, memory) dispatched with no plan coordinate at all --
    ``extract_dispatch_binding`` labels every non-verifier dispatch
    ``task_execution`` by default, so the ABSENCE of a ``task_id=`` token on
    such a turn is not a mistake, it is the shape of a legitimate free
    dispatch. Emitting an event for that case would train the triage reader
    to ignore the channel, which defeats the reason it exists.

    Four reasons are anomalous unconditionally: each one means SOME plan or
    verifier coordinate was supplied and failed to resolve, which is never the
    free-turn shape. The fifth, ``task_execution_requires_plan_task_id``, is
    anomalous only when ``plan_id`` WAS extracted from the prompt -- that
    combination means the dispatcher intended a plan-bound turn (it named the
    plan) and simply dropped the ``task_id=`` token, the exact misdispatch the
    convention in ``agents/gaia-orchestrator.md`` exists to prevent.
    """
    if reason in _ALWAYS_ANOMALOUS_BINDING_REASONS:
        return True
    if reason == _CONDITIONAL_BINDING_REASON:
        return binding.get("plan_id") is not None
    return False


def _record_dispatch_binding_rejection(
    exc: Any, *, agent_name: str, binding: Dict[str, Any],
) -> None:
    """Record an ANOMALOUS nascent-row birth rejection as a harness_events row.

    Mirrors ``_record_contract_rejection_defect``: writes through
    ``EventWriter().write_event`` at severity ``warning`` (below the ``error``
    that channel uses, since a rejected birth never blocks the dispatch the
    way a contract-gate rejection does), and is strictly best-effort -- every
    failure is swallowed so a birth rejection, itself already non-blocking,
    can never become a reason the dispatch fails.

    Silent by design for the legitimate free-turn shape; see
    :func:`_is_anomalous_dispatch_binding_rejection` for the discriminator.
    """
    reason = getattr(exc, "reason", "")
    if not _is_anomalous_dispatch_binding_rejection(reason, binding):
        return
    try:
        from modules.events.event_writer import EventWriter

        meta: Dict[str, Any] = {
            "agent": agent_name,
            "reason": reason,
            "binding": {
                "kind": binding.get("kind"),
                "turn_role": binding.get("turn_role"),
                "plan_id": binding.get("plan_id"),
                "plan_task_id": binding.get("plan_task_id"),
                "parent_handoff_id": binding.get("parent_handoff_id"),
            },
        }
        EventWriter().write_event(
            DISPATCH_BINDING_REJECTED_EVENT,
            "hook",
            agent_name,
            f"nascent-row birth rejected for {agent_name} ({reason})",
            severity="warning",
            meta=meta,
        )
    except Exception as write_exc:  # pragma: no cover - telemetry must never block
        logger.debug(
            "dispatch binding rejection event write failed (non-fatal): %s",
            write_exc,
        )


class ClaudeCodeAdapter(HookAdapter):
    """Concrete adapter for Claude Code v2.1+ hook protocol.

    Claude Code sends JSON on stdin with these top-level fields:
        - hook_event_name: str  (e.g. "PreToolUse", "PostToolUse", "SubagentStop")
        - session_id: str
        - tool_name: str        (PreToolUse / PostToolUse)
        - tool_input: dict      (PreToolUse / PostToolUse)
        - tool_response: dict    (PostToolUse only)
        - agent_type: str       (PreToolUse for subagent dispatches; also SubagentStop)
        - agent_id: str         (PreToolUse for subagent dispatches; also SubagentStop)
        - agent_transcript_path: str  (SubagentStop only)
        - last_assistant_message: str (SubagentStop only)
        - cwd: str              (SubagentStop only)

    Responses use hookSpecificOutput with permissionDecision for PreToolUse.
    """

    # ------------------------------------------------------------------ #
    # parse_event: stdin JSON -> HookEvent
    # ------------------------------------------------------------------ #

    def parse_event(self, stdin_data: str) -> HookEvent:
        """Parse raw stdin JSON into a normalized HookEvent.

        Raises:
            ValueError: If JSON is invalid, empty, or event type is unknown.
        """
        if not stdin_data or not stdin_data.strip():
            raise ValueError("Empty stdin data")

        try:
            raw = json.loads(stdin_data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON from stdin: {exc}") from exc

        if not isinstance(raw, dict):
            raise ValueError(f"Expected JSON object, got {type(raw).__name__}")

        # Map hook_event_name to HookEventType enum
        event_name = raw.get("hook_event_name", "")
        if not event_name:
            raise ValueError("Missing required field: hook_event_name")

        try:
            event_type = HookEventType(event_name)
        except ValueError:
            raise ValueError(f"Unknown hook event type: {event_name}")

        session_id = raw.get("session_id", "")

        return HookEvent(
            event_type=event_type,
            session_id=session_id,
            payload=raw,
            distribution=self.detect_distribution(),
        )

    # ------------------------------------------------------------------ #
    # format_validation_response: ValidationResult -> HookResponse
    # ------------------------------------------------------------------ #

    def format_validation_response(self, result: ValidationResult) -> HookResponse:
        """Format a ValidationResult into Claude Code's hookSpecificOutput JSON.

        Maps:
            allowed=True                -> permissionDecision: "allow", exit 0
            allowed=False, nonce=None   -> permissionDecision: "deny", exit 0
            allowed=False, permanent    -> permissionDecision: "deny", exit 2
            nonce present               -> include nonce in reason

        When result.modified_input is set, includes updatedInput for Claude Code
        to apply the modified parameters transparently.
        """
        if result.allowed:
            decision = PermissionDecision.ALLOW.value
        else:
            decision = PermissionDecision.DENY.value

        output: Dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": result.reason,
            }
        }

        # Include updatedInput when the command was modified (e.g. footer stripping)
        if result.modified_input is not None:
            output["hookSpecificOutput"]["updatedInput"] = result.modified_input

        # Exit code 2 = permanent block (blocked_commands.py), 0 = corrective deny
        # Permanent blocks have no nonce and are not allowed
        exit_code = 0
        if not result.allowed and result.nonce is None and result.tier == "BLOCKED":
            exit_code = 2

        return HookResponse(output=output, exit_code=exit_code)

    def read_permission_decision(self, output: Dict[str, object]) -> Optional[str]:
        """Read Claude Code's nested permission decision."""
        return read_permission_decision(output)

    def read_permission_reason(self, output: Dict[str, object]) -> str:
        """Read Claude Code's nested permission reason."""
        return read_permission_reason(output)

    def inject_updated_input(
        self, output: Dict[str, object], updated_input: Dict[str, object]
    ) -> Dict[str, object]:
        """Attach rewritten input using Claude Code's hook response shape."""
        return inject_updated_input(output, updated_input)

    # ------------------------------------------------------------------ #
    # format_completion_response: CompletionResult -> HookResponse
    # ------------------------------------------------------------------ #

    def format_completion_response(self, result: CompletionResult) -> HookResponse:
        """Format a CompletionResult for SubagentStop.

        Success case: minimal response with contract status.
        Repair needed: includes anomaly details for orchestrator.
        Exit code is always 0 (SubagentStop never blocks).
        """
        output: Dict[str, Any] = {
            "contract_valid": result.contract_valid,
            "anomalies_detected": len(result.anomalies),
        }

        if result.episode_id:
            output["episode_id"] = result.episode_id

        if result.context_updated:
            output["context_updated"] = True

        if result.repair_needed:
            output["repair_needed"] = True
            output["anomalies"] = result.anomalies

        return HookResponse(output=output, exit_code=0)

    # ------------------------------------------------------------------ #
    # format_context_response: ContextResult -> HookResponse
    # ------------------------------------------------------------------ #

    def format_context_response(self, result: ContextResult) -> HookResponse:
        """Format a ContextResult for SubagentStart context injection.

        Claude Code expects SubagentStart hooks to return::

            {"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                    "additionalContext": "..."}}

        The additionalContext string is appended to the subagent's system prompt.
        """
        hook_specific: Dict[str, Any] = {
            "hookEventName": "SubagentStart",
        }

        if result.context_injected and result.additional_context:
            hook_specific["additionalContext"] = result.additional_context

        output: Dict[str, Any] = {"hookSpecificOutput": hook_specific}

        if result.sections_provided:
            output["sections_provided"] = result.sections_provided

        return HookResponse(output=output, exit_code=0)

    # ------------------------------------------------------------------ #
    # P1: adapt_session_start
    # ------------------------------------------------------------------ #

    def adapt_session_start(self, raw: dict) -> BootstrapResult:
        """Parse SessionStart event and return bootstrap actions.

        SessionStart payload contains session_type which determines
        what bootstrap actions to take:
        - startup: full scan + refresh
        - resume: refresh only (no scan)
        - clear/compact: no scan, no refresh
        """
        session_type = raw.get("session_type", "startup")
        return BootstrapResult(
            should_scan=session_type == "startup",
            should_refresh=session_type in ("startup", "resume"),
            session_type=session_type,
        )

    # ------------------------------------------------------------------ #
    # P1: format_bootstrap_response
    # ------------------------------------------------------------------ #

    def format_bootstrap_response(self, result: BootstrapResult) -> HookResponse:
        """Format a BootstrapResult for SessionStart.

        SessionStart hooks are informational -- exit code is always 0.
        """
        output: Dict[str, Any] = {
            "session_type": result.session_type,
            "should_scan": result.should_scan,
            "should_refresh": result.should_refresh,
        }

        if result.project_scanned:
            output["project_scanned"] = True
        if result.context_path:
            output["context_path"] = str(result.context_path)
        if result.tools_detected:
            output["tools_detected"] = result.tools_detected

        return HookResponse(output=output, exit_code=0)

    # ------------------------------------------------------------------ #
    # detect_distribution: declare the host's channel + root (NPM vs PLUGIN)
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # capabilities: Claude Code DECLARES what this host can do
    # ------------------------------------------------------------------ #

    # Frozen, instance-stable declaration. Claude Code v2.1+ offers every
    # capability the core currently asks about: it gathers consent inline via
    # AskUserQuestion (INTERACTIVE_CONSENT), runs the orchestrator approval-id
    # cycle (OUT_OF_BAND_APPROVAL), accepts a structured permissionDecision
    # (STRUCTURED_PERMISSION_DECISION), applies updatedInput transparently
    # (UPDATED_INPUT), injects SessionStart/SubagentStart context
    # (CONTEXT_INJECTION), and exposes the agent transcript (TRANSCRIPT_ACCESS).
    # A future host that lacks one simply omits it here; the absence drives the
    # core's declared degradation, with no change to business logic.
    _CAPABILITIES: FrozenSet[HostCapability] = frozenset(
        {
            HostCapability.INTERACTIVE_CONSENT,
            HostCapability.OUT_OF_BAND_APPROVAL,
            HostCapability.STRUCTURED_PERMISSION_DECISION,
            HostCapability.UPDATED_INPUT,
            HostCapability.CONTEXT_INJECTION,
            HostCapability.TRANSCRIPT_ACCESS,
        }
    )

    def capabilities(self) -> FrozenSet[HostCapability]:
        """Declare the capabilities Claude Code offers (see ``_CAPABILITIES``)."""
        return self._CAPABILITIES

    def detect_distribution(self) -> HostDistribution:
        """Declare Claude Code's distribution model for this invocation.

        Resolves Claude Code's two channels and their root, then hands the core
        an opaque :class:`HostDistribution`:

        1. CLAUDE_PLUGIN_ROOT env var set -> "plugin" channel, root = that path
        2. Default                        -> "npm" channel, no root

        The channel names and the env var are confined to this adapter; the core
        never sees them.
        """
        plugin_root = self._get_plugin_root()
        if plugin_root is not None:
            return HostDistribution(channel=_CHANNEL_PLUGIN, root=plugin_root)
        return HostDistribution(channel=_CHANNEL_NPM, root=None)

    # ------------------------------------------------------------------ #
    # Helper: get_plugin_root
    # ------------------------------------------------------------------ #

    def _get_plugin_root(self) -> Optional[Path]:
        """Resolve plugin root from CLAUDE_PLUGIN_ROOT env var."""
        plugin_root = os.environ.get(_PLUGIN_ROOT_ENV_VAR)
        if plugin_root:
            return Path(plugin_root)
        return None

    # ------------------------------------------------------------------ #
    # T005: parse_pre_tool_use helper
    # ------------------------------------------------------------------ #

    def parse_pre_tool_use(self, raw: Dict[str, Any]) -> ValidationRequest:
        """Extract a ValidationRequest from a PreToolUse payload.

        Extracts:
        - tool_name: the tool being invoked (Bash, Task, Agent, etc.)
        - command: for Bash, the command string; for Task/Agent, the prompt
        - tool_input: the full tool_input dict
        - session_id: session identifier

        Args:
            raw: The full stdin JSON dict (HookEvent.payload).

        Returns:
            ValidationRequest with normalized fields.
        """
        tool_name = raw.get("tool_name", "")
        tool_input = raw.get("tool_input", {})
        session_id = raw.get("session_id", "")

        # Extract the primary command/prompt string based on tool type
        if tool_name.lower() == "bash":
            command = tool_input.get("command", "")
        elif tool_name.lower() in ("task", "agent"):
            command = tool_input.get("prompt", "")
        else:
            # For other tools, use the first string value or empty
            command = tool_input.get("command", "") or tool_input.get("prompt", "")

        return ValidationRequest(
            tool_name=tool_name,
            command=command,
            tool_input=tool_input,
            session_id=session_id,
        )

    # ------------------------------------------------------------------ #
    # T006: parse_post_tool_use helper
    # ------------------------------------------------------------------ #

    def parse_post_tool_use(self, raw: Dict[str, Any]) -> ToolResult:
        """Extract a ToolResult from a PostToolUse payload.

        Extracts:
        - tool_name: the tool that was invoked
        - command: the command that was run (from tool_input)
        - output: tool execution output
        - exit_code: execution exit code
        - session_id: session identifier

        Args:
            raw: The full stdin JSON dict (HookEvent.payload).

        Returns:
            ToolResult with execution data.
        """
        tool_name = raw.get("tool_name", "")
        tool_input = raw.get("tool_input", {})
        tool_response = raw.get("tool_response", {})
        session_id = raw.get("session_id", "")

        command = tool_input.get("command", "")

        # --- Harness field reality (verified against ~17.9k real Bash results) ---
        # Claude Code's Bash PostToolUse tool_response does NOT carry 'exit_code'
        # and does NOT carry 'output'. Its two observed shapes are:
        #   SUCCESS: a dict {stdout, stderr, interrupted(bool), isImage(bool),
        #            noOutputExpected(bool), [returnCodeInterpretation, ...]}.
        #            NOTE the field is 'stdout' (not 'output'); 'stderr' is
        #            present but empty (stderr is folded into stdout); a benign
        #            non-zero exit like grep-no-match stays a dict and carries
        #            'returnCodeInterpretation' -- the harness does NOT treat it
        #            as an error, so neither do we.
        #   FAILURE: a bare STRING (the error text). On a non-zero exit the
        #            harness replaces the dict with a string and sets the
        #            message-level tool_result is_error=true (a signal the hook
        #            never receives).
        # The previous code read tool_response.get('output')/('exit_code'),
        # neither of which exists: on success exit_code defaulted to 0 (EXECUTED)
        # and on failure tool_response is a str, so .get() raised and the whole
        # post-hook aborted -- so a FAILED event was NEVER recorded (261/0 split).
        #
        # Derive `success` defensively from whatever signals are actually
        # present, then synthesize an exit_code (0 clean / 1 failed) so the
        # downstream `success = exit_code == 0` check and the EXECUTED/FAILED
        # discriminator in _record_t3_outcome_event stay intact.
        failed = False
        output = ""
        exit_code = 0

        if isinstance(tool_response, str):
            # Failure form: the harness passed the error text as a bare string.
            output = tool_response
            failed = True
        elif isinstance(tool_response, dict):
            # Read stdout from the real key, falling back to the legacy 'output'.
            output = tool_response.get("stdout", tool_response.get("output", "")) or ""
            # Explicit failure flags. The current Bash harness omits these, but
            # other tools / future harness versions may set them, so honor them.
            is_error = tool_response.get("is_error", tool_response.get("isError"))
            interrupted = tool_response.get("interrupted")
            raw_exit = tool_response.get("exit_code", tool_response.get("exitCode"))
            if is_error or interrupted:
                failed = True
            if raw_exit is not None:
                try:
                    exit_code = int(raw_exit)
                except (TypeError, ValueError):
                    exit_code = 0
                if exit_code != 0:
                    failed = True
        # else: unknown shape -> leave success (preserve prior default behavior).

        if failed and exit_code == 0:
            # No explicit non-zero code available; synthesize one so the
            # downstream success check resolves to False.
            exit_code = 1

        return ToolResult(
            tool_name=tool_name,
            command=command,
            output=output,
            exit_code=exit_code,
            session_id=session_id,
        )

    # ------------------------------------------------------------------ #
    # T007: parse_agent_completion helper
    # ------------------------------------------------------------------ #

    def parse_agent_completion(self, raw: Dict[str, Any]) -> AgentCompletion:
        """Extract an AgentCompletion from a SubagentStop payload.

        Extracts:
        - agent_type: the type/name of the agent (e.g. "cloud-troubleshooter")
        - agent_id: unique agent instance identifier
        - transcript_path: path to the agent's transcript JSONL
        - last_message: the agent's final assistant message
        - session_id: session identifier

        Args:
            raw: The full stdin JSON dict (HookEvent.payload).

        Returns:
            AgentCompletion with agent data.
        """
        return AgentCompletion(
            agent_type=raw.get("agent_type", ""),
            agent_id=raw.get("agent_id", ""),
            transcript_path=raw.get("agent_transcript_path", ""),
            last_message=raw.get("last_assistant_message", ""),
            session_id=raw.get("session_id", ""),
        )

    # ------------------------------------------------------------------ #
    # _get_gaia_agent_names: discover Gaia-managed agents from agents/ dir
    # ------------------------------------------------------------------ #

    def _get_gaia_agent_names(self) -> set:
        """Get names of Gaia-managed agents from the agents/ directory.

        Returns a set of agent names (filenames without .md extension).
        Native Claude Code agents (Explore, Plan, claude-code-guide) will
        not appear in this set, enabling bypass of contract validation.
        """
        agents_dir = Path(__file__).resolve().parent.parent.parent / "agents"
        if not agents_dir.is_dir():
            return set()
        return {
            f.stem
            for f in agents_dir.iterdir()
            if f.suffix == ".md" and f.is_file()
        }

    # ------------------------------------------------------------------ #
    # format_ask_response: for interactive permission requests
    # ------------------------------------------------------------------ #

    def format_ask_response(
        self, reason: str, updated_input: dict | None = None
    ) -> HookResponse:
        """Format an 'ask' permission response.

        Used when the hook wants Claude Code to ask the user for permission.
        This is distinct from deny (which silently blocks).

        Args:
            reason: Human-readable explanation forwarded to the agent.
            updated_input: Optional modified tool input (e.g. footer-stripped
                command) to include as ``updatedInput`` so the modification
                survives the native permission dialog.
        """
        output: Dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": PermissionDecision.ASK.value,
                "permissionDecisionReason": reason,
            }
        }
        if updated_input:
            output["hookSpecificOutput"]["updatedInput"] = updated_input
        return HookResponse(output=output, exit_code=0)

    # ------------------------------------------------------------------ #
    # request_consent: host-specific consent mechanism (AskUserQuestion /
    # orchestrator approval-id hand-off) -- the ONLY place either lives.
    # ------------------------------------------------------------------ #

    def request_consent(self, request: ConsentRequest) -> HookResponse:
        """Drive Claude Code to obtain the user's consent for ``request``.

        This is where Claude Code's consent mechanics live and nowhere else.
        Two host shapes, selected by whether an out-of-band approval flow owns
        the decision:

        - ``approval_id`` set -> the orchestrator drives the Gaia approval
          cycle. Emit a ``deny`` keyed to that ``approval_id``; the subagent
          reports APPROVAL_REQUEST, the user clicks Approve in the native
          AskUserQuestion prompt, and the ElicitationResult hook activates the
          grant. The ``reason`` already carries the approval_id banner, so this
          is a thin formatting step.
        - ``approval_id`` is None -> gather consent inline via Claude Code's
          native permission prompt (``permissionDecision: "ask"`` ->
          AskUserQuestion), preserving ``updated_input`` through the dialog.

        Business logic calls this without knowing either shape exists.
        """
        if request.approval_id is not None:
            # Out-of-band approval flow: deny now, decision keyed to approval_id.
            return HookResponse(
                output={
                    _HOOK_SPECIFIC_OUTPUT: {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": PermissionDecision.DENY.value,
                        "permissionDecisionReason": request.reason,
                    }
                },
                exit_code=0,
            )
        # Inline consent via the native AskUserQuestion permission prompt.
        return self.format_ask_response(
            request.reason, updated_input=request.updated_input
        )

    # ------------------------------------------------------------------ #
    # adapt_pre_tool_use: full pre-tool-use lifecycle
    # ------------------------------------------------------------------ #

    def adapt_pre_tool_use(self, event: HookEvent) -> HookResponse:
        """Run all pre-tool-use business logic and return a formatted response.

        Orchestrates: routing (bash vs task), validation, state management,
        context injection, approval handling, and response formatting.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.security.approval_grants import (
            cleanup_expired_grants,
        )
        from modules.tools.bash_validator import BashValidator
        from modules.tools.task_validator import TaskValidator
        hook_data = event.payload
        tool_name = hook_data.get("tool_name") or ""
        tool_input = hook_data.get("tool_input", {})

        logger.info("Hook invoked: tool=%s, params=%s", tool_name, json.dumps(tool_input)[:200])

        try:
            # ── Delegate mode gate ─────────────────────────────────
            # Must run before any other logic.  The orchestrator (main
            # session) is restricted to dispatch tools plus Read.  Subagents
            # are unaffected.
            from modules.orchestrator.delegate_mode import check_delegate_mode

            dm_result = check_delegate_mode(tool_name, hook_data)
            if dm_result.blocked:
                logger.warning(
                    "DELEGATE_MODE denied %s for orchestrator", tool_name,
                )
                return HookResponse(
                    output={
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": dm_result.reason,
                        }
                    },
                    exit_code=0,
                )

            # Periodic cleanup of expired approval grants
            cleanup_expired_grants()

            if not isinstance(tool_name, str):
                return HookResponse(output="Error: Invalid tool name", exit_code=2)
            if not isinstance(tool_input, dict):
                return HookResponse(output="Error: Invalid parameters", exit_code=2)

            if tool_name.lower() == "bash":
                return self._adapt_bash(tool_name, tool_input, hook_data=hook_data)
            elif tool_name.lower() in ("task", "agent"):
                return self._adapt_task(
                    tool_name, tool_input,
                    session_id=event.session_id,
                    hook_data=hook_data,
                )
            elif tool_name.lower() == "sendmessage":
                return self._adapt_send_message(
                    tool_name, tool_input, session_id=event.session_id,
                )
            elif tool_name.lower() in ("write", "edit"):
                agent_id = (hook_data or {}).get("agent_id", "")
                is_subagent = bool(agent_id)
                session_id = (hook_data or {}).get("session_id", "")
                return self._adapt_write_edit(
                    tool_name, tool_input,
                    session_id=session_id,
                    is_subagent=is_subagent,
                    agent_id=agent_id,
                )
            else:
                # Other tools pass through
                return HookResponse(output={}, exit_code=0)

        except Exception as e:
            logger.error("Unexpected error in adapt_pre_tool_use: %s", e, exc_info=True)
            from modules.core.operational_errors import storage_exhaustion_message
            operational_message = storage_exhaustion_message(e)
            if operational_message is not None:
                # Exit 2 keeps the failure FAIL-CLOSED: the runtime treats only
                # exit 2 as blocking (exit 1 is a non-blocking "HOOK ERROR", so
                # the tool call would proceed unvalidated). The distinct message
                # is what tells the operator this is storage exhaustion, not a
                # security-policy denial.
                return HookResponse(
                    output=operational_message,
                    exit_code=2,
                )
            return HookResponse(
                output=f"Error during security validation: {str(e)}",
                exit_code=2,
            )

    def _adapt_bash(
        self,
        tool_name: str,
        parameters: dict,
        hook_data: dict | None = None,
    ) -> HookResponse:
        """Handle Bash tool validation within the adapter.

        Args:
            tool_name: The tool name ("Bash").
            parameters: The tool_input dict (contains "command").
            hook_data: Full hook event payload -- used to detect subagent
                context via the ``agent_id`` field.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.tools.bash_validator import BashValidator

        command = parameters.get("command", "")
        if not command:
            return HookResponse(output="Error: Bash tool requires a command", exit_code=2)

        # Detect subagent context: if agent_id is present in the hook event,
        # the command is running inside a subagent (not the orchestrator).
        is_subagent = bool(hook_data and hook_data.get("agent_id"))
        session_id = (hook_data or {}).get("session_id", "")
        agent_type = (hook_data or {}).get("agent_type", "")
        # Host stdin carries a top-level snake_case tool_use_id in BOTH
        # PreToolUse and PostToolUse, and it MATCHES for the same call. Keying
        # hook state by (session_id, tool_use_id) is what ends the concurrent-
        # subagent race that clobbered the old single global state file and
        # lost EXECUTED terminal events.
        tool_use_id = (hook_data or {}).get("tool_use_id", "")

        validator = BashValidator()
        result = validator.validate(
            command, is_subagent=is_subagent, session_id=session_id,
            agent_type=agent_type, hook_payload=hook_data,
        )

        if not result.allowed:
            logger.warning("BLOCKED: %s - %s", command[:100], result.reason)
            # Block with nonce for the orchestrator approval flow. The T3
            # deny-vs-native-ask decision was already made in the validator
            # (decide_t3_outcome): a subagent under the orchestrator gets a
            # deny+approval_id block_response; the main session falls back to
            # the native ask dialog. Either way the block_response carries the
            # correct outcome.
            if result.block_response is not None:
                return HookResponse(output=result.block_response, exit_code=0)
            return HookResponse(
                output=self._format_blocked_message(result),
                exit_code=2,
            )

        # Save state for post-hook. When the command was allowed by consuming a
        # T3 approval grant, carry that approval_id forward so the terminal event
        # is appended to the approval_events chain for that approval -- EXECUTED
        # by PostToolUse on a clean exit, or FAILED by the Stop-hook
        # reconciliation on a non-zero exit (the host does not fire PostToolUse
        # then). The grant is consumed here at PreToolUse and flips to CONSUMED,
        # so PostToolUse cannot re-discover it via check_approval_grant.
        effective_command = result.modified_input.get("command", command) if result.modified_input else command
        state = create_pre_hook_state(
            tool_name=tool_name,
            command=effective_command,
            tier=str(result.tier),
            session_id=session_id,
            tool_use_id=tool_use_id,
            allowed=True,
            consumed_approval_id=result.consumed_approval_id,
            command_set_reservation=result.command_set_reservation,
        )
        # Keyed by (session_id, tool_use_id) when both are present; degrades to
        # the legacy global file otherwise (create_pre_hook_state/save_hook_state
        # share the same key resolution, so PostToolUse retrieves the exact
        # entry this call wrote).
        state_saved = save_hook_state(state)
        if result.command_set_reservation and not state_saved:
            # Deny fail-closed: without the correlation state, PostToolUse/Stop
            # could never settle the reserved COMMAND_SET item. The settle here
            # is best-effort -- the denial stands even if it fails.
            try:
                from gaia.store.writer import settle_plan_command
                settle_plan_command(
                    result.command_set_reservation["approval_id"],
                    session_id=session_id, tool_use_id=tool_use_id, success=False,
                    failure_reason="PreToolUse correlation state could not be persisted",
                )
            except Exception as exc:
                logger.debug("reservation settle on state failure errored: %s", exc)
            return HookResponse(
                output="COMMAND_SET denied: host correlation state unavailable",
                exit_code=2,
            )

        # Dispatch-identity injection (fail-closed guards, M1). A subagent's
        # DB writes (memory / evidence / brief+plan content / state transitions /
        # handoff finalize) are gated by DB-side guards that read
        # GAIA_DISPATCH_AGENT. That variable is NEVER set at the process level, so
        # historically every subagent wrote with human-level (fail-open)
        # authority. Here we export the HARNESS-provided agent identity at the
        # front of the command so the `gaia` CLI subprocess (and any Python that
        # imports gaia.store.writer) inherits the real invoking identity and the
        # guards enforce the per-agent model. The identity is agent_type from the
        # hook payload -- host-provided, NOT anything the agent can forge. The
        # orchestrator (main session, no agent_id -> is_subagent False) and a
        # genuine human CLI call are never injected, so they keep fail-open
        # human authority. See build_dispatch_identity_command for why an
        # `export ...;` prefix (not a bare `VAR=x` word) is used.
        final_command = effective_command
        if is_subagent:
            final_command = build_dispatch_identity_command(
                effective_command, agent_type
            )

        if final_command != command:
            reason = (
                result.reason
                if result.modified_input
                else "dispatch-identity injected (GAIA_DISPATCH_AGENT)"
            )
            logger.info(
                "MODIFIED: %s -> tier=%s (footer_stripped=%s, dispatch_id=%s)",
                command[:80], result.tier,
                bool(result.modified_input), final_command != effective_command,
            )
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": reason,
                    "updatedInput": {"command": final_command},
                }
            }
            return HookResponse(output=output, exit_code=0)

        logger.info("ALLOWED: %s - tier=%s", command[:100], result.tier)
        return HookResponse(output={}, exit_code=0)

    def _adapt_task(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str = "",
        hook_data: Optional[dict] = None,
    ) -> HookResponse:
        """Handle Task/Agent tool validation within the adapter.

        The subagent's payload is the dispatch KERNEL, not a preloaded
        project-context snapshot. This method therefore no longer builds
        project context, computes surface routing, appends the workspace
        memory block, or renders the legacy identity block -- the born row
        (claimed at SubagentStart) carries everything the kernel renders, and
        the turn pulls project context on demand through the CLI. What still
        happens here: task validation, session-events digest, the birth of the
        nascent row, and the cache bridge that carries the events digest to
        SubagentStart.

        The session-events digest is agent-agnostic (no project-agent
        allowlist gate): every dispatched agent, registered or not, receives
        the same last-events digest.

        ``hook_data`` is the raw PreToolUse payload: the birth path mines its
        top-level ``prompt_id`` / ``tool_use_id`` / ``cwd`` (dispatch
        correlation coordinates, v43/v44) which ``parameters`` (the
        tool_input) does not carry.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.tools.task_validator import TaskValidator
        from modules.session.session_event_injector import build_session_events

        events_text = build_session_events(parameters)

        # Standard task validation (runs against ORIGINAL prompt -- no workaround needed)
        validator = TaskValidator()
        result = validator.validate(parameters)

        if not result.allowed:
            logger.warning("BLOCKED Task: %s - %s", result.agent_name, result.reason)
            return HookResponse(output=result.reason, exit_code=2)

        state = create_pre_hook_state(
            tool_name=tool_name,
            command=f"Task:{result.agent_name}",
            tier=str(result.tier),
            allowed=True,
            is_t3=result.is_t3_operation,
        )
        save_hook_state(state)

        logger.info("ALLOWED Task: %s", result.agent_name)

        # Born-at-dispatch (v37, plan 34 task 6): stamp the nascent
        # agent_contract_handoffs row FROM the dispatch metadata, validated for
        # referential integrity. Best-effort and STRICTLY non-blocking -- a
        # dispatch is never blocked by a binding that fails to resolve or a birth
        # that errors; the row simply is not born (the SubagentStop backstop/reaper
        # still guarantees exactly-one row).
        self._maybe_birth_dispatched_row(
            parameters, result.agent_name, session_id,
            hook_data=hook_data,
        )

        # Cache bridge to SubagentStart: only the session-events digest rides
        # it now (see _cache_context_for_subagent for why the bridge survives
        # at all). The born row's identity does NOT ride it -- SubagentStart
        # resolves the row via claim_dispatch_row, so an empty digest needs no
        # cache entry.
        additional = events_text or ""
        if additional:
            effective_session_id = session_id or "unknown"
            agent_type = result.agent_name or "unknown"
            self._cache_context_for_subagent(
                effective_session_id,
                agent_type,
                additional,
                task_description=parameters.get("description", ""),
            )
            logger.info(
                "Cached context for SubagentStart: agent=%s, session=%s",
                agent_type, effective_session_id,
            )

        # Write AGENT_DISPATCH event (non-blocking)
        try:
            from modules.events.event_writer import EventWriter, AGENT_DISPATCH
            prompt = parameters.get("prompt", "")
            EventWriter().write_event(
                AGENT_DISPATCH, "hook", result.agent_name or "unknown",
                f"dispatched for: {prompt[:100]}",
            )
        except Exception:
            pass  # Events are non-critical

        return HookResponse(output={}, exit_code=0)

    @staticmethod
    def _resolve_dispatch_workspace(
        parameters: dict, hook_data: Optional[dict],
    ) -> str:
        """Resolve the REAL workspace for a dispatch birth.

        The historical chain (``parameters['workspace']`` -> GAIA_WORKSPACE ->
        literal ``"global"``) landed every birth on ``"global"`` in practice:
        the Task tool_input never carries a workspace key and GAIA_WORKSPACE
        is not set by the harness, so the literal fallback always won -- which
        broke the workspace scoping of everything keyed to the row (injected
        memory above all). The explicit overrides keep their priority, but the
        fallback is now the canonical path-based resolver
        (``gaia.project.current``) anchored at the payload's ``cwd``, the same
        resolver every other workspace consumer uses; its own last resort is
        still ``"global"``.
        """
        explicit = (
            parameters.get("workspace")
            or os.environ.get("GAIA_WORKSPACE")
        )
        if explicit:
            return explicit
        try:
            from modules.install_detector import resolve_workspace

            return resolve_workspace((hook_data or {}).get("cwd") or None)
        except Exception:
            return "global"

    @staticmethod
    def _kernel_dispatch_facts(
        agent_name: str,
        workspace: str,
        *,
        turn_role: Optional[str] = None,
        cwd: Optional[str] = None,
        project_token: Optional[str] = None,
    ) -> "tuple[Optional[Dict[str, Any]], Optional[str]]":
        """Derive (kernel_sections, dispatch_project) for a birth -- no routing.

        Surface routing left the subagent dispatch path, so the kernel
        payload is derived from the agent's OWN declarations --
        ``agent_contract_permissions`` for can_read/can_write, the agent's own
        ``surface_routing`` row (primary_agent match) for its surface, the
        dispatch binding for its role.

        The project is DISPATCH DATA first: a ``project=<name>``
        token in the dispatch prompt (``project_token``, extracted by
        ``extract_dispatch_binding``) resolves by NAME against the workspace's
        project_identity (``resolve_project_by_name``). Only when the dispatch
        named no project does the cwd-based resolution run as fallback
        (``resolve_dispatch_project``) -- the orchestrator dispatches from the
        workspace root, so the cwd lane alone left dispatch_project NULL in
        practice.

        Fail-safe: any error degrades to ``(None, None)`` -- a birth without a
        kernel payload, never a blocked dispatch.
        """
        try:
            from modules.core.paths import ensure_package_root_importable

            ensure_package_root_importable()
            from tools.context.context_provider import (
                build_kernel_sections,
                resolve_dispatch_project,
                resolve_project_by_name,
            )

            sections = build_kernel_sections(
                agent_name, workspace, turn_role=turn_role,
            )
            project = resolve_project_by_name(workspace, project_token)
            if not project:
                project = resolve_dispatch_project(workspace, cwd)
            return sections, project
        except Exception:
            logger.debug(
                "kernel dispatch facts derivation failed (non-fatal)",
                exc_info=True,
            )
            return None, None

    @staticmethod
    def _maybe_birth_dispatched_row(
        parameters, agent_name, session_id,
        hook_data: Optional[dict] = None,
    ) -> Optional[Dict[str, str]]:
        """Best-effort born-at-dispatch row birth (plan 34 task 6; plan 49
        task 1 -- degrade-not-drop, D1).

        Mints a REAL, adoptable identity for the turn (see
        ``modules.agents.dispatch_identity``) and births the nascent row under
        it, returning ``{"agent_id": ..., "contract_id": ...}`` on success.
        Returns None only when NO row was born at all -- a writer error, or an
        extraction gap. The identity no longer travels to the subagent from
        here: SubagentStart recovers it by claiming the row itself
        (``claim_dispatch_row``), so the return value is a birth signal for
        callers and tests, not a payload.

        BIRTH IS TOTAL: EVERY ``DispatchBindingError`` degrades rather than
        dropping the row. Whichever coordinate failed to resolve is stamped
        NULL (referential integrity is not weakened), and the rejection reason
        plus the failed token are recorded INSIDE the birth envelope via
        :func:`~modules.agents.dispatch_binding.birth_degraded_row`. The
        identity is still returned and the turn still runs -- degrade, not
        block. The reason this is total and not a curated subset is in
        ``dispatch_binding``'s module docstring: an unborn row is not a weaker
        binding but no contract at all, and it is UNRECOVERABLE, since
        ``harness_agent_id`` is stamped only at the SubagentStart claim and no
        CLI verb can write it afterwards.

        ``hook_data`` (the raw PreToolUse payload) enriches the birth with the
        v43/v44 dispatch coordinates -- prompt_id/tool_use_id/description/
        prompt, the kernel_sections payload (derived without routing; see
        ``_kernel_dispatch_facts``), and the dispatch project -- and anchors
        the workspace resolution at the payload's cwd
        (see _resolve_dispatch_workspace). Optional: a caller without it
        births exactly as before, with NULL coordinates.

        ``session_id`` is stamped as-is when present and NULL when absent --
        never a placeholder string. A NULL leaves the column open for a later,
        truer attribution (finalize merges it with COALESCE); the old
        ``"unknown"`` literal baked a lie into the column that nothing could
        correct.

        Never raises: every failure path is swallowed so a dispatch is never
        blocked. See _adapt_task for the rationale.
        """
        try:
            from modules.agents.dispatch_binding import (
                DispatchBindingError,
                birth_degraded_row,
                birth_dispatched_row,
            )
            from modules.agents.dispatch_identity import mint_dispatch_identity

            meta = {
                "prompt": parameters.get("prompt", ""),
                "subagent_type": agent_name,
            }
            from modules.agents.dispatch_binding import extract_dispatch_binding
            binding = extract_dispatch_binding(meta)

            workspace = ClaudeCodeAdapter._resolve_dispatch_workspace(
                parameters, hook_data,
            )
            # Dispatch coordinates (v43/v44): prompt/description live in the
            # Task tool_input; prompt_id/tool_use_id/cwd are top-level payload
            # fields. kernel_sections and the project are derived here, at
            # birth, with NO surface routing (see _kernel_dispatch_facts).
            payload = hook_data or {}
            kernel_sections, dispatch_project = (
                ClaudeCodeAdapter._kernel_dispatch_facts(
                    agent_name or "",
                    workspace,
                    turn_role=binding.get("turn_role"),
                    cwd=payload.get("cwd") or None,
                    project_token=binding.get("project"),
                )
            )
            dispatch_fields = {
                "dispatch_prompt_id": payload.get("prompt_id") or None,
                "dispatch_tool_use_id": payload.get("tool_use_id") or None,
                "dispatch_description": parameters.get("description") or None,
                "dispatch_prompt": parameters.get("prompt") or None,
                "kernel_sections": kernel_sections,
                "dispatch_project": dispatch_project,
            }
            sid = session_id or None
            agent = agent_name or "unknown"
            ptid = binding.get("plan_task_id")
            # The identity is MINTED, not derived from (session, agent, task).
            # A derived key collapses two concurrent dispatches of the same
            # agent type onto one row -- see dispatch_identity's module comment
            # for why uniqueness beats per-key idempotency here. Idempotency
            # survives at the scope that matters: the writer's ON CONFLICT makes
            # a single dispatch's birth a no-op on retry.
            identity = mint_dispatch_identity()
            contract_id = identity["contract_id"]

            try:
                birth_dispatched_row(
                    contract_id=contract_id,
                    agent_id=identity["agent_id"],
                    workspace=workspace,
                    kind=binding.get("kind"),
                    turn_role=binding.get("turn_role"),
                    plan_task_id=ptid,
                    plan_id=binding.get("plan_id"),
                    parent_handoff_id=binding.get("parent_handoff_id"),
                    session_id=sid,
                    # The NAME goes in the birth envelope, not in agent_id --
                    # that column now holds the minted handle the turn adopts.
                    # It is the only coordinate a turn that never adopts still
                    # shares with its own row (see the writer's
                    # find_dispatched_row_by_agent_name).
                    agent_name=agent,
                    **dispatch_fields,
                )
                logger.info(
                    "Born-at-dispatch: nascent row stamped (agent=%s, task=%s, "
                    "contract_id=%s)",
                    agent, ptid, contract_id,
                )
                return identity
            except DispatchBindingError as exc:
                # A binding that does not resolve is NOT an error to block on --
                # log why the row was not born and let the dispatch proceed.
                logger.info(
                    "Born-at-dispatch: binding not born (agent=%s): %s",
                    agent, exc,
                )
                _record_dispatch_binding_rejection(
                    exc, agent_name=agent, binding=binding,
                )
                try:
                    birth_degraded_row(
                        contract_id=contract_id,
                        agent_id=identity["agent_id"],
                        workspace=workspace,
                        kind=binding.get("kind"),
                        rejection_reason=exc.reason,
                        failed_plan_task_id=ptid,
                        failed_parent_handoff_id=binding.get("parent_handoff_id"),
                        plan_id=binding.get("plan_id"),
                        session_id=sid,
                        agent_name=agent,
                        **dispatch_fields,
                    )
                    logger.info(
                        "Born-at-dispatch: DEGRADED row stamped (agent=%s, "
                        "failed_task=%s, failed_parent=%s, reason=%s, "
                        "contract_id=%s)",
                        agent, ptid, binding.get("parent_handoff_id"),
                        exc.reason, contract_id,
                    )
                    return identity
                except Exception:
                    logger.debug(
                        "Born-at-dispatch degraded birth failed (non-fatal)",
                        exc_info=True,
                    )
        except Exception:
            logger.debug("Born-at-dispatch birth failed (non-fatal)", exc_info=True)
        return None

    @staticmethod
    def _resolve_dispatch_row(
        *,
        session_id: str,
        agent_type: str,
        task_info: dict,
        parsed_contract,
        db_path: Optional[Path] = None,
    ) -> Optional[dict]:
        """Locate the born-at-dispatch row for the turn that is ENDING.

        The binding stamped at birth (``plan_task_id`` above all) lives on that
        row, and the blind-verification gate -- and, since the row-first
        migration, the SubagentStop contract gate itself
        (``resolve_subagent_stop_gate``) -- reads it to decide the turn's own
        outcome. Four lanes, most exact first, because the row is reachable by
        a different coordinate depending on what the turn did with the
        identity minted for it:

          1. HARNESS-STAMPED -- ``harness_agent_id`` (v40,
             ``gaia.store.writer.stamp_harness_agent_id``, written at the
             SubagentStart claim) joined against ``task_info['agent_id']``,
             which at SubagentStop time is the identical value. This is FIRST
             because it is the only coordinate the runtime itself stamps for
             THIS exact dispatch: it does not depend on the turn emitting a
             fence, on it running ``gaia contract init``, or on the row still
             being 'DISPATCHED', and it cannot reach a sibling's row the way a
             shared agent NAME or a mentioned-but-not-owned draft id can. It is
             also the shape a turn that stops emitting the fence always has, so
             ordering it first is what makes fence-less resolution deterministic
             rather than a fallthrough. The lane DECLINES an ambiguous match
             (2+ rows under one harness id) instead of taking the most recent --
             same refusal, and for the same reason, as the writer's
             ``find_dispatched_row_by_agent_name``.
          2. ADOPTED -- the turn ran ``gaia contract init`` under the injected
             identity (or the fence names it), so its own ``agent_id`` IS the
             row's. Looked up state-agnostically: an adopted turn's
             ``finalize`` CONVERGES the born row, so by now it is no longer
             'DISPATCHED' and a DISPATCHED-only query would report the turn as
             unbound and silently drop the gate.
          3. LEGACY -- rows born before the identity was minted carry the agent
             NAME in ``agent_id``. Still queried so an in-flight turn dispatched
             under the old shape keeps its binding.
          4. UNADOPTED -- the turn minted its own unrelated handle, so it shares
             no identifier with its row; the dispatched NAME recorded in the birth
             envelope is the last coordinate left. That lane refuses to guess
             between concurrent same-name dispatches (see the writer's
             ``find_dispatched_row_by_agent_name``), so it resolves nothing rather
             than binding a turn to a sibling's row.

        WHY THE HARNESS LANE MOVED FROM LAST TO FIRST. It was added as a fourth
        lane on the belief that lanes 1-3 would simply miss when no fence was
        emitted. They do not always miss -- they can HIT THE WRONG ROW, which is
        worse. ``resolve_minted_agent_id`` used to fall back to the harness
        ``agent_id`` itself, and the backstop capture stamps that same value
        into the ``agent_id`` COLUMN of the contention row it writes
        (``handoff_persister.persist_handoff``); the adopted lane then queried
        ``agent_id = <harness id>`` and ``dispatch_row_for_identity`` returned
        the most recent match -- the contention row -- instead of the turn's own
        cleanly-closed row. MEASURED live: the gate rejected a turn whose real
        work was correctly recorded. That fallback is gone from the resolver, and
        ordering the exact per-dispatch coordinate first means a later
        reintroduction of any inexact one cannot outrank it again.

        Returns the row dict, or None when no lane resolves -- which every caller
        must read as "unbound", never as an error. A full miss is LOGGED: it was
        previously indistinguishable from an unbound turn, so the resolution
        defects above lived without a trace.
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

    def _adapt_send_message(
        self, tool_name: str, parameters: dict, session_id: str = "",
    ) -> HookResponse:
        """Handle SendMessage tool validation for agent resumption.

        Validates agent ID format and message content. Does NOT inject
        project context (it's a resume). Nonce relay is no longer processed
        here -- approval grants are activated by the UserPromptSubmit hook.

        Contract-as-managed-data (T6, decision #3): this is the ONE place
        the adapter learns, with certainty, which agent_id a CC session is
        resuming (SendMessage's own ``to`` parameter). ``agent_id`` is the
        SAME identifier space ``gaia.contract.drafts`` keys a draft by (see
        the ``gaia.contract.validator.AGENT_ID_PATTERN_TEXT`` format shared
        with AC-1's form validator),
        so recording session_id -> agent_id here is enough for
        ``adapt_subagent_start``'s resume path to recover the resumed
        agent's own in-progress draft -- SubagentStart's payload carries
        only session_id + agent_type, never the resumed agent_id. Core/CLI
        stay agnostic (decision #1): only this CC-specific bridge, not
        gaia.contract.*, reads SendMessage's ``to`` field or a session_id.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        # The agent_id shape is owned by gaia.contract.validator and re-exported
        # by response_contract; never re-spell the literal here.
        from modules.agents.response_contract import _AGENT_ID_PATTERN

        agent_id = parameters.get("to", "")
        message = parameters.get("message", "")

        # Validate agentId format
        if not agent_id or not _AGENT_ID_PATTERN.match(agent_id):
            logger.warning("BLOCKED SendMessage: Invalid agentId format '%s'", agent_id)
            msg = (
                f"[ERROR] Invalid agent ID format: '{agent_id}'\n\n"
                "Agent ID must be 'a' followed by 16 or more hex characters.\n"
                "The agent ID is returned at the end of agent responses.\n"
                "Look for: 'agentId: a...' in the previous agent output -- copy "
                "it verbatim; a shortened or invented value will not address "
                "the running agent."
            )
            return HookResponse(output=msg, exit_code=2)

        if not message or not message.strip():
            logger.warning("BLOCKED SendMessage: Missing message for agent %s", agent_id)
            msg = (
                "[ERROR] SendMessage requires a message\n\n"
                "When resuming an agent, you must provide a message:\n\n"
                "SendMessage(\n"
                "    to=\"<the agentId from that agent's output>\",\n"
                "    message=\"Continue with the latest user instruction.\"\n"
                ")\n\n"
                "The message tells the agent what to do next."
            )
            return HookResponse(output=msg, exit_code=2)

        logger.info("SENDMESSAGE: Resuming agent %s", agent_id)

        # Record the session -> agent_id resume mapping (T6). Best-effort:
        # a failure here must never block a legitimate resume.
        try:
            self._cache_resume_mapping(session_id or "unknown", agent_id)
        except Exception:
            logger.debug("Resume mapping cache write failed (non-fatal)", exc_info=True)

        state = create_pre_hook_state(
            tool_name=tool_name,
            command=f"SendMessage:{agent_id}",
            tier="T0",
            allowed=True,
            is_t3=False,
            has_approval=False,
        )
        save_hook_state(state)

        logger.info("ALLOWED SendMessage: agent %s - message length: %d", agent_id, len(message))
        return HookResponse(output={}, exit_code=0)

    def _adapt_write_edit(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str = "",
        is_subagent: bool = False,
        agent_id: str = "",
    ) -> HookResponse:
        """Handle Write and Edit tool path protection, plus an advisory
        artifact-skill reminder.

        Blocks modifications to Gaia hooks, settings, and security config
        by requiring user approval for any path that matches protected path
        patterns.

        Foreground (orchestrator) flow: returns permissionDecision "ask" so
        the native Claude Code dialog handles approval.

        Subagent flow: mirrors the bash_validator nonce-based pattern.
        - Checks for an existing pending approval (retry guard).
        - If found, returns deny with the existing approval_id.
        - If not found, writes a pending approval and returns deny with a
          new approval_id so the orchestrator can ask the user and activate
          the grant via the ElicitationResult hook.
        - On retry, if an active grant exists for this path, allows through.

        Protected paths:
        - Any path that resolves within the gaia hooks directory (Path.resolve().relative_to(hooks_dir)), EXCEPT .md files — documentation does not execute code and is exempt
        - .claude/settings.json and .claude/settings.local.json

        Non-protected subagent writes additionally get a one-shot advisory
        nudge (see ``modules.agents.artifact_skill_reminder``): when the
        file's extension maps to a governing skill via ``artifact_skill_map``
        and that skill has not already been reminded this turn (keyed by
        session_id + agent_id), the response carries an "allow" decision
        whose ``additionalContext`` names the governing skill. It travels in
        ``additionalContext``, not ``permissionDecisionReason`` -- with
        ``permissionDecision: "allow"``, Claude Code's own hook contract
        surfaces the reason only in logs and the debug transcript, never to
        the model, so a reminder placed there would never reach the agent
        (``code.claude.com/docs/en/hooks.md``, "PreToolUse decision
        control"). A short ``permissionDecisionReason`` is still set for the
        audit log, but it is not the channel the agent reads. This never
        blocks -- it is the prevention half of the gap that
        ``skill_injection_verifier`` can only detect after the fact at
        SubagentStop. Restricted to ``is_subagent=True`` (with a non-empty
        ``agent_id``): the orchestrator delegates instead of writing code
        itself, so the foreground path is unaffected and existing foreground
        callers keep the exact-passthrough contract.
        """
        from modules.security.approval_grants import (
            check_approval_grant_for_file,
            find_pending_for_file,
            generate_nonce,
            write_pending_approval_for_file,
        )
        from modules.agents.artifact_skill_map import expected_skill_for_path
        from modules.agents.artifact_skill_reminder import (
            build_reminder_context,
            should_remind,
        )

        file_path = parameters.get("file_path", "")
        if not file_path:
            return HookResponse(output={}, exit_code=0)

        hooks_dir = Path(__file__).parent.parent.resolve()

        def _is_protected(path_str):
            p = Path(path_str)
            try:
                rp = p.resolve()
            except Exception:
                rp = p
            try:
                rp.relative_to(hooks_dir)
                if rp.suffix == ".md":
                    return False  # docs don't execute code; exempt from protection
                return True
            except ValueError:
                pass
            if p.name in ("settings.json", "settings.local.json"):
                for part in rp.parts:
                    if part == ".claude":
                        return True
            return False

        if not _is_protected(file_path):
            if is_subagent and agent_id:
                expected_skill = expected_skill_for_path(file_path)
                if expected_skill and should_remind(session_id, agent_id, expected_skill):
                    return HookResponse(
                        output={
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "allow",
                                "permissionDecisionReason": (
                                    f"artifact-skill reminder logged for "
                                    f"'{expected_skill}'"
                                ),
                                "additionalContext": build_reminder_context(
                                    file_path, expected_skill,
                                ),
                            }
                        },
                        exit_code=0,
                    )
            return HookResponse(output={}, exit_code=0)

        logger.warning(
            "PROTECTED_PATH: %s attempted to modify %s (subagent=%s)",
            tool_name, file_path, is_subagent,
        )

        if not is_subagent:
            # Foreground / orchestrator context: ask the user for consent
            # inline (the adapter maps this to the native approval dialog).
            reason = (
                "[PROTECTED_PATH] Modifications to Gaia hooks and security config "
                "require approval."
            )
            return self.request_consent(
                ConsentRequest(
                    operation=file_path,
                    kind="file",
                    reason=reason,
                    tier="T3_BLOCKED",
                )
            )

        # Subagent context: nonce-based pending approval flow.

        # 1. Check if a grant has already been activated for this path (retry
        #    after user approved).
        existing_grant = check_approval_grant_for_file(file_path, session_id or None)
        if existing_grant:
            logger.info(
                "File-path grant active, allowing %s through: %s",
                tool_name, file_path,
            )
            return HookResponse(output={}, exit_code=0)

        # 2. Check if a pending approval already exists (guard against infinite
        #    approval_id generation while the user is still reviewing).
        existing_nonce = find_pending_for_file(session_id or "", file_path)
        if existing_nonce:
            approval_id = existing_nonce
            logger.info(
                "Reusing pending approval_id=%s for retry: %s",
                approval_id, file_path,
            )
        else:
            # 3. No existing pending -- generate a new nonce.
            approval_id = generate_nonce()
            pending_path = write_pending_approval_for_file(
                nonce=approval_id,
                file_path=file_path,
                session_id=session_id or None,
            )
            if pending_path is None:
                # Persistence failure -- fall back to native ask dialog.
                logger.warning(
                    "Failed to persist pending file-path approval for subagent; "
                    "falling back to ask: %s",
                    file_path,
                )
                reason = (
                    "[PROTECTED_PATH] Modifications to Gaia hooks and security config "
                    "require approval. (Pending approval persistence failed; "
                    "native dialog fallback.)"
                )
                return self.request_consent(
                    ConsentRequest(
                        operation=file_path,
                        kind="file",
                        reason=reason,
                        tier="T3_BLOCKED",
                    )
                )

        reason = (
            f"[T3_BLOCKED] This file modification requires user approval.\n"
            f"Do NOT retry this operation. Report APPROVAL_REQUEST with this approval_id "
            f"in your agent_contract_handoff.\n"
            f"File: {file_path}\n"
            f"Tool: {tool_name}\n"
            f"approval_id: {approval_id}"
        )
        # Out-of-band approval flow: consent is keyed to the persisted approval_id.
        return self.request_consent(
            ConsentRequest(
                operation=file_path,
                kind="file",
                reason=reason,
                tier="T3_BLOCKED",
                approval_id=approval_id,
            )
        )

    @staticmethod
    def _format_blocked_message(result) -> str:
        """Format blocked command message. Delegates to blocked_message_formatter."""
        from modules.security.blocked_message_formatter import format_blocked_message
        return format_blocked_message(result)

    # ------------------------------------------------------------------ #
    # adapt_post_tool_use: full post-tool-use lifecycle
    # ------------------------------------------------------------------ #

    def adapt_post_tool_use(self, event: HookEvent) -> HookResponse:
        """Run all post-tool-use business logic and return a formatted response.

        Orchestrates: state retrieval, duration computation, audit logging,
        T3 grant confirmation, critical event detection, session context
        writing, state cleanup, and AskUserQuestion grant activation.
        """
        from modules.core.state import get_hook_state, clear_hook_state
        from modules.audit.logger import log_execution
        from modules.audit.event_detector import detect_critical_event
        from modules.session.session_context_writer import SessionContextWriter
        from modules.security.approval_grants import check_approval_grant, confirm_grant

        hook_data = event.payload
        tool_result_data = self.parse_post_tool_use(hook_data)
        logger.info("Post-hook event: %s", hook_data.get("hook_event_name"))

        raw_tool_response = hook_data.get("tool_response", {})
        tool_name = tool_result_data.tool_name
        parameters = hook_data.get("tool_input", {})
        # Retrieve the exact state this tool call wrote at PreToolUse. The host
        # sends the SAME top-level tool_use_id at PostToolUse, so the keyed
        # lookup is unambiguous even with concurrent subagents in flight.
        post_session_id = hook_data.get("session_id", "")
        tool_use_id = hook_data.get("tool_use_id", "")
        output = tool_result_data.output
        # On a Bash failure tool_response is a bare STRING (see parse_post_tool_use),
        # so guard the dict access -- otherwise .get() would raise and abort the
        # whole post-hook before the FAILED event is recorded.
        duration = (
            raw_tool_response.get("duration_ms", 0) / 1000.0
            if isinstance(raw_tool_response, dict)
            else 0.0
        )
        success = tool_result_data.exit_code == 0

        # ------------------------------------------------------------- #
        # AskUserQuestion: check if user approved a pending T3 grant
        # ------------------------------------------------------------- #
        if tool_name == "AskUserQuestion":
            self._handle_ask_user_question_result(hook_data)
            return HookResponse(output={}, exit_code=0)

        # ------------------------------------------------------------- #
        # Subagent dispatch: the ONLY place a harness-truncated subagent is
        # observable. A cut never reaches SubagentStop, so the subagent side
        # writes nothing at all; the parent, however, still receives a result
        # reporting success but carrying no contract fence. Record it here or
        # it is lost. Observation only -- never blocks the orchestrator.
        #
        # The tool reports itself as "Agent"; "Task" is its former name. The
        # hooks.json matcher still says "Task" and the harness still honors it,
        # so the hook DOES fire -- but the payload carries the new name, which
        # is why gating this branch on "Task" alone silently observed nothing.
        # ------------------------------------------------------------- #
        from modules.agents.task_result_observer import TASK_TOOL_NAMES

        if tool_name in TASK_TOOL_NAMES:
            self._observe_task_result(hook_data)
            return HookResponse(output={}, exit_code=0)

        try:
            pre_state = get_hook_state(
                session_id=post_session_id, tool_use_id=tool_use_id
            )
            tier = pre_state.tier if pre_state else "unknown"

            # Prefer wall-clock duration from pre-hook timestamp
            computed_duration = duration
            if pre_state and pre_state.start_time_epoch > 0:
                computed_duration = time.time() - pre_state.start_time_epoch

            log_execution(
                tool_name=tool_name,
                parameters=parameters,
                result=output,
                duration=computed_duration,
                exit_code=0 if success else 1,
                tier=tier,
            )

            # Confirm the T3 grant after a successful Bash execution. The grant
            # was already CONSUMED at the match in PreToolUse (bash_validator
            # flips PENDING->CONSUMED when the command is authorized); it is NOT
            # swept at SubagentStop (that sweep was removed in the M1 approvals
            # redesign). Confirming marks the consumed grant so subsequent retries
            # within the same subagent session are recognized.
            if tool_name == "Bash" and success:
                command = parameters.get("command", "")
                session_id = hook_data.get("session_id", "")
                if command:
                    grant = check_approval_grant(command, session_id=session_id)
                    if grant is not None and not grant.confirmed:
                        confirm_grant(command, session_id=session_id)
                        logger.info(
                            "T3 grant confirmed (consumed at match in PreToolUse): %s", command[:80],
                        )

            # Close the audit-log cycle for an APPROVED T3 command that just ran.
            # PreToolUse stashed the consumed grant's approval_id in HookState
            # (keyed by session_id+tool_use_id) when it matched (and consumed) the
            # grant. In practice this branch records EXECUTED: the host does NOT
            # fire PostToolUse for a non-zero Bash exit, so a FAILED command never
            # reaches here -- its FAILED event is recorded by the Stop-hook
            # reconciliation (_reconcile_dangling_t3_on_stop) instead. The
            # success/failure discriminator is kept for the rare host/tool that
            # does deliver a failure result to PostToolUse. This continues the
            # approval_events hash chain via the canonical store.record_event()
            # helper -- the only authorized writer for the chain (it routes
            # through chain.insert_event(), which links prev_hash -> this_hash
            # before INSERT).
            if tool_name == "Bash":
                consumed_approval_id = (
                    pre_state.metadata.get("consumed_approval_id") if pre_state else None
                )
                if consumed_approval_id:
                    reservation = pre_state.metadata.get("command_set_reservation")
                    if reservation:
                        from gaia.store.writer import settle_plan_command
                        if not settle_plan_command(
                            consumed_approval_id,
                            session_id=post_session_id,
                            tool_use_id=tool_use_id,
                            success=success,
                            failure_reason=None if success else str(output),
                        ):
                            raise RuntimeError("COMMAND_SET reservation correlation failed")
                    self._record_t3_outcome_event(
                        consumed_approval_id,
                        command=parameters.get("command", ""),
                        success=success,
                        exit_code=tool_result_data.exit_code,
                        session_id=hook_data.get("session_id", ""),
                    )

            events = detect_critical_event(tool_name, parameters, output, success)
            if events:
                writer = SessionContextWriter()
                for evt in events:
                    writer.update_context(evt.to_dict())

            # Write COMMAND_EXECUTED event for T2+ Bash commands only (non-blocking)
            if tool_name == "Bash" and tier in ("T2", "T3"):
                try:
                    from modules.events.event_writer import EventWriter, COMMAND_EXECUTED
                    cmd = parameters.get("command", "")
                    EventWriter().write_event(
                        COMMAND_EXECUTED, "hook", "",
                        f"{'ok' if success else 'error'}: {cmd[:120]}",
                        severity="info" if success else "warning",
                        meta={"tier": tier},
                    )
                except Exception:
                    pass  # Events are non-critical

            clear_hook_state(
                session_id=post_session_id, tool_use_id=tool_use_id
            )
            logger.debug("Post-hook completed for %s", tool_name)

        except Exception as e:
            logger.error("Error in adapt_post_tool_use: %s", e, exc_info=True)

        return HookResponse(output={}, exit_code=0)

    @staticmethod
    def _observe_task_result(hook_data) -> None:
        """Record a harness-cut subagent turn seen from the Task result.

        Best-effort and strictly non-blocking: detection or persistence
        failing must never disturb the orchestrator's own turn.
        """
        try:
            from modules.agents.task_result_observer import observe_task_result

            cut = observe_task_result(hook_data)
            if cut is not None:
                logger.warning(
                    "Subagent cut detected: agent=%s reason=%s metrics=%s",
                    cut.agent, cut.reason, cut.metrics,
                )
        except Exception as exc:
            logger.debug("Task result observation failed (non-fatal): %s", exc)

    def _record_t3_outcome_event(
        self,
        approval_id: str,
        *,
        command: str,
        success: bool,
        exit_code: int,
        session_id: str = "",
        error_text: str = "",
    ) -> None:
        """Append an EXECUTED or FAILED event for an approved T3 command.

        Closes the audit-log cycle: once a command runs under a consumed grant,
        the approval_events chain records whether it succeeded (EXECUTED) or
        failed (FAILED). Writes through gaia.approvals.store.record_event(), the
        canonical chain writer -- never a raw INSERT -- so prev_hash -> this_hash
        linkage is preserved and validate_chain() stays intact end to end.

        ``error_text`` carries the real failure detail on a FAILED event (the
        Stop-hook reconciliation supplies the transcript's toolUseResult string,
        since PostToolUse never fired to observe it directly).

        Best-effort and non-fatal: the approval store lives in gaia.db and may be
        unavailable in some hook contexts; any failure is logged and swallowed so
        a chain-write hiccup never breaks tool execution.
        """
        event_type = "EXECUTED" if success else "FAILED"
        try:
            from gaia.approvals import store as _approval_store

            payload = {
                "command": command,
                "exit_code": exit_code,
                "outcome": "success" if success else "failure",
            }
            if error_text:
                payload["error"] = error_text
            _approval_store.record_event(
                approval_id,
                event_type,
                session_id=session_id or None,
                payload_json=json.dumps(payload, sort_keys=True, separators=(",", ":")),
                metadata_json=json.dumps({"source": "post_tool_use"}),
            )
            logger.info(
                "Recorded %s event for approval_id=%s (exit=%d)",
                event_type, approval_id[:16], exit_code,
            )
        except Exception as exc:
            logger.warning(
                "Failed to record %s event for approval_id=%s (non-fatal): %s",
                event_type, approval_id[:16], exc,
            )

    @staticmethod
    def _extract_exit_code_from_result(result: object) -> int:
        """Derive an exit code from a host toolUseResult failure detail.

        A failed Bash command surfaces as a bare string such as
        ``"Error: Exit code 1"`` or ``"Error: Exit code 127\\n/bin/bash: ...
        command not found"``. Parse the first ``Exit code N`` and fall back to
        1 (generic failure) when no code is present.
        """
        text = result if isinstance(result, str) else json.dumps(result) if result else ""
        m = re.search(r"[Ee]xit code (\d+)", text)
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                return 1
        return 1

    def _reconcile_dangling_t3_on_stop(
        self, *, session_id: str, transcript_path: str
    ) -> None:
        """Close the audit cycle for T3 commands that FAILED (Stop-hook path).

        Why this exists: the current host does NOT fire PostToolUse for a
        non-zero Bash exit -- verified live (exit 1 and exit 127 both produce
        zero PostToolUse). PostToolUse is where an approved T3 command's
        EXECUTED/FAILED terminal event is written, so a FAILED command would
        otherwise NEVER record its outcome. The failure detail exists only in
        the session transcript's top-level ``toolUseResult`` (a bare string).

        At Stop the turn is fully finished: every successful Bash command has
        already had its PostToolUse (which clears its keyed state entry), so any
        keyed entry still present is a command whose PostToolUse never fired --
        i.e. a failure. For each such entry that consumed a T3 approval and has
        no terminal event yet, we record FAILED (with the real error text pulled
        from the transcript) via the SAME canonical writer PostToolUse uses, then
        clear the entry. Entries with no consumed approval are just cleared so
        the keyed store does not accumulate. Handles multiple dangling entries
        and entries left over from a prior turn.

        Best-effort and non-fatal: a Stop hook must never fail the turn.
        """
        from modules.core.state import iter_dangling_states, clear_hook_state

        if not session_id:
            return

        try:
            dangling = list(iter_dangling_states(session_id))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Dangling-state enumeration failed (non-fatal): %s", exc)
            return

        for tool_use_id, state in dangling:
            try:
                consumed_approval_id = (
                    state.metadata.get("consumed_approval_id")
                    if isinstance(state.metadata, dict)
                    else None
                )
                if consumed_approval_id:
                    reservation = state.metadata.get("command_set_reservation")
                    if reservation:
                        from gaia.store.writer import settle_plan_command
                        settle_plan_command(
                            consumed_approval_id,
                            session_id=session_id,
                            tool_use_id=tool_use_id,
                            success=False,
                            failure_reason="command failed; reconciled at Stop",
                        )
                    if not self._t3_terminal_event_exists(consumed_approval_id):
                        # Recover the failure detail from the transcript. Fall
                        # back to a generic message when it cannot be found so
                        # the FAILED event is still recorded.
                        detail = self._read_failure_detail(
                            transcript_path, tool_use_id
                        )
                        exit_code = self._extract_exit_code_from_result(detail)
                        command = state.command or ""
                        error_text = (
                            detail if isinstance(detail, str) and detail
                            else "command failed; no PostToolUse fired (reconciled at Stop)"
                        )
                        self._record_t3_outcome_event(
                            consumed_approval_id,
                            command=command,
                            success=False,
                            exit_code=exit_code,
                            session_id=session_id,
                            error_text=error_text,
                        )
                        logger.info(
                            "Reconciled FAILED at Stop for approval_id=%s "
                            "(tool_use_id=%s, exit=%d)",
                            consumed_approval_id[:16], tool_use_id[:16], exit_code,
                        )
                    else:
                        # Double-record guard: a terminal event already exists
                        # (e.g. PostToolUse did fire, or a prior Stop reconciled
                        # it). Skip recording; just clear the stale entry.
                        logger.debug(
                            "Skip reconcile: terminal event exists for %s",
                            consumed_approval_id[:16],
                        )
                # Clear the entry either way so it is not reprocessed.
                clear_hook_state(session_id=session_id, tool_use_id=tool_use_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Reconcile of dangling entry failed (non-fatal): %s", exc
                )

    @staticmethod
    def _read_failure_detail(transcript_path: str, tool_use_id: str) -> object:
        """Read the toolUseResult failure detail for a tool_use_id (best-effort)."""
        try:
            from adapters.host_transcript import find_tool_use_result
            return find_tool_use_result(transcript_path, tool_use_id)
        except Exception:
            return None

    @staticmethod
    def _t3_terminal_event_exists(approval_id: str) -> bool:
        """True if an EXECUTED or FAILED event already exists for the approval.

        The double-record guard: reconciliation must not append a FAILED event
        when the audit cycle for this approval was already closed.
        """
        try:
            from gaia.approvals import store as _approval_store
            events = _approval_store.replay_for_approval(approval_id)
            return any(
                e.get("event_type") in ("EXECUTED", "FAILED") for e in events
            )
        except Exception:
            # If we cannot tell, err toward NOT recording again (avoid a
            # duplicate FAILED). A missing record is less harmful than a
            # corrupted chain from a double write.
            return True

    # ------------------------------------------------------------------ #
    # _handle_ask_user_question_result: grant activation from user answer
    # ------------------------------------------------------------------ #

    def _handle_ask_user_question_result(self, hook_data: Dict[str, Any]) -> None:
        """Conditionally activate pending grants based on user's answer.

        Uses nonce-targeted activation when the approved answer contains a
        ``[P-<hex>]`` tag (the nonce prefix).  This works identically for
        same-session and cross-session approvals:
          1. Extract the nonce prefix from the approved label.
          2. Load the specific pending file by prefix (any session).
          3. Activate the grant under the CURRENT session.

        DB-only since the grant-lifecycle FS retirement: REQUESTED writes go
        to the DB, so the approved pending is resolved by nonce prefix straight
        from the DB via ``activate_db_pending_by_prefix()``.

        Never blocks (no exceptions raised to caller).
        """
        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            extract_nonce_from_label,
        )

        session_id = hook_data.get("session_id", "") or os.environ.get("CLAUDE_SESSION_ID", "")

        # Extract answers from tool_response first, then tool_input as fallback
        tool_response = hook_data.get("tool_response", {})
        answers = {}
        if isinstance(tool_response, dict):
            answers = tool_response.get("answers", {})
        if not answers and isinstance(hook_data.get("tool_input", {}), dict):
            answers = hook_data.get("tool_input", {}).get("answers", {})

        if not answers:
            logger.info("AskUserQuestion: no answers found in payload, skipping grant activation")
            return

        user_approved = any("approve" in str(v).lower() for v in answers.values())

        if not user_approved:
            logger.info(
                "AskUserQuestion: user did not approve (answers: %s), skipping grant activation",
                {k: v for k, v in answers.items()},
            )
            return

        # User approved -- activate grants
        logger.info("AskUserQuestion: user approved, activating grants for session %s", session_id[:12])

        try:
            if not session_id:
                logger.info("AskUserQuestion: no session_id available, skipping grant activation")
                return

            # Nonce-targeted activation: extract the nonce from answer labels.
            nonce_prefix = None
            for v in answers.values():
                nonce_prefix = extract_nonce_from_label(str(v))
                if nonce_prefix:
                    break

            if not nonce_prefix:
                logger.info(
                    "AskUserQuestion: no nonce prefix in answer labels -- "
                    "nothing to activate for session %s", session_id[:12],
                )
                return

            # Resolve the approved pending straight from the DB.
            result = activate_db_pending_by_prefix(
                nonce_prefix, current_session_id=session_id,
            )
            logger.info(
                "AskUserQuestion DB activation: prefix=%s success=%s status=%s reason=%s",
                nonce_prefix,
                result.success,
                getattr(result.status, "value", str(result.status)),
                result.reason,
            )

        except Exception as e:
            logger.error("Error in _handle_ask_user_question_result: %s", e, exc_info=True)

    # ------------------------------------------------------------------ #
    # adapt_subagent_stop: full subagent-stop lifecycle
    # ------------------------------------------------------------------ #

    def adapt_subagent_stop(self, event: HookEvent) -> HookResponse:
        """Run all subagent-stop business logic and return a formatted response.

        Orchestrates: contract parsing/validation, approval cleanup,
        context updates, workflow recording, response contract validation,
        anomaly detection, episodic memory, and result assembly.
        """
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

        hook_data = event.payload
        logger.info(
            "Hook event: %s, agent: %s",
            hook_data.get("hook_event_name"),
            hook_data.get("agent_type", "unknown"),
        )

        # Parse agent completion data
        completion = self.parse_agent_completion(hook_data)

        # ----------------------------------------------------------
        # Transcript analysis (T011)
        # ----------------------------------------------------------
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

        # Resolve agent output: prefer last_assistant_message, fall back to transcript
        agent_output = completion.last_message
        if not agent_output:
            transcript_path = completion.transcript_path
            agent_output = read_transcript(transcript_path) if transcript_path else ""
            logger.info("Agent output: %d chars from transcript (fallback)", len(agent_output))
        else:
            logger.info("Agent output: %d chars from last_assistant_message", len(agent_output))

        task_info = build_task_info_from_hook_data(hook_data, agent_output)

        # ----------------------------------------------------------
        # Native agent bypass: agents not defined in agents/ dir
        # (e.g. claude-code-guide, Explore, Plan) do not emit
        # agent_contract_handoff. Skip contract validation to avoid
        # an infinite retry loop (exit_code=2 -> retry -> no contract).
        # ----------------------------------------------------------
        _native_agent_type = task_info.get("agent", "unknown")
        _gaia_agents = self._get_gaia_agent_names()
        if _native_agent_type not in _gaia_agents:
            logger.info(
                "Native agent '%s' — skipping contract validation (gaia agents: %s)",
                _native_agent_type, _gaia_agents,
            )
            return HookResponse(
                output={"success": True, "native_agent": True, "agent": _native_agent_type},
                exit_code=0,
            )

        # Run the main processing chain
        try:
            from datetime import datetime as _dt
            # Prefer the session_id parsed from the stdin event so cleanup
            # actions (approvals, grants, anchors) target the real session
            # that owned the subagent. get_or_create_session_id() returns a
            # synthetic env-derived id when CLAUDE_SESSION_ID isn't set,
            # which never matches pending records persisted with the real
            # event.session_id and breaks cleanup (Bug B / P-a11d14e0).
            session_id = event.session_id or get_or_create_session_id()
            agent_type = task_info.get("agent", "unknown")

            parsed_contract = parse_contract(agent_output)

            # A turn that finalized its draft but emitted no fenced block
            # leaves `parsed_contract` empty, so everything DOWNSTREAM of the
            # gate that describes the turn -- agent_state resolution, episode
            # metrics, key_outputs, the update_contracts channel -- would see
            # nothing and record a working turn as a blank one. Reconstruct the
            # envelope from the finalized draft so those readers get the real
            # contract.
            #
            # This began as a rescue for the gate itself, back when the gate
            # parsed the fence from TEXT. It no longer serves that purpose (the
            # gate reads the row directly) and is kept for the descriptive
            # readers alone. Non-fatal: returns None -> callers see whatever
            # the fence gave them.
            if not (
                isinstance(parsed_contract, dict)
                and isinstance(parsed_contract.get("agent_status"), dict)
            ):
                _reconstructed = self._reconstruct_contract_from_finalized_draft(
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
                # BUG D fix: surface validate() anomalies into the anomalies list
                # (anomalies list is built later; collect here and merge below)
                _validation_anomalies = []
                for _m in (contract_result.missing or []):
                    _validation_anomalies.append({
                        "type": "contract_validation_failure",
                        "severity": "warning",
                        "message": f"Contract validation failed for {agent_type}: missing={_m}",
                    })
            else:
                _validation_anomalies = []

            # Resolve canonical agent_state from the agent_contract_handoff envelope.
            from modules.agents.contract_validator import _resolve_status
            _resolved_agent_state = (
                _resolve_status(parsed_contract.get("agent_status") or {})
                if isinstance(parsed_contract, dict) else ""
            )

            # task_info["plan_status"] was computed in build_task_info_from_hook_data,
            # BEFORE parse_contract and BEFORE the finalized-draft reconstruction
            # above -- from agent_output alone. A turn that closes with no fenced
            # block but a correctly finalized contract has nothing there to parse,
            # so it carries the empty string forward unless corrected here. Every
            # reader downstream of this point (workflow_metrics, the episodes row,
            # episode_writer's outcome bucketing) must see the REAL state the
            # (possibly reconstructed) envelope declares, whichever of the six
            # valid states it is -- not just the COMPLETE case, and not silence.
            if _resolved_agent_state:
                task_info["plan_status"] = _resolved_agent_state

            # ----------------------------------------------------------
            # stop_reason isolation (decision #5 / M5 / AC-11) -- resolved
            # ONCE here, EARLY (before anomalies are signaled), so the T16
            # full-verdict gate can consult it for salvage-vs-violation and so
            # the T11 fast-path / T9 backstop below READ this value instead of
            # recomputing it. The interpretation of stop_reason (max_tokens ->
            # truncation salvage candidate, end_turn -> genuine violation) is
            # host-specific judgment that lives HERE, in the adapter, never in
            # gaia.contract.validator / gaia.contract.crosscheck. Prefer the raw
            # hook payload's stop_reason; fall back to the transcript-derived one.
            # ----------------------------------------------------------
            _raw_stop_reason = hook_data.get("stop_reason")
            if not _raw_stop_reason and transcript_analysis and transcript_analysis.stop_reasons:
                _raw_stop_reason = transcript_analysis.stop_reasons[-1]
            _stop_reason_classification = classify_stop_reason(_raw_stop_reason)

            # ----------------------------------------------------------
            # T16/M4: evaluate the SubagentStop contract gate ONCE, via the ramp
            # flag (DEFAULT OFF). Ramp OFF -> the legacy 3-case Option B verdict.
            # Ramp ON -> the full-verdict verdict from the SINGLE portable core
            # (gaia.contract.crosscheck.validate). The verdict's anomalies and
            # rejection flow into the anomaly-signal block and the exit-code
            # assembly below.
            #
            # Fence retirement (step 2): the gate validates the turn's OWN
            # persisted ``agent_contract_handoffs`` row and nothing else. A
            # row that is unreachable, or reachable but never cleanly
            # finalized, REJECTS on that basis alone -- the response text is
            # not read. See ``resolve_subagent_stop_gate`` for the full
            # three-case ordering. Every OTHER use of ``parsed_contract`` in
            # this function (episode writing, key_outputs, update_contracts,
            # response-contract metrics) is unchanged: those describe the
            # turn, they do not close it.
            #
            # Robustness: the gate is evaluated defensively. A raise inside the
            # gate (e.g. an import failure in a lazily-imported dependency) must
            # never take down the SubagentStop hygiene that follows it --
            # cleanup_approval / nonce preservation below is session-critical
            # and has no relation to contract-gate correctness. On failure we
            # fall back to a non-rejecting verdict (same shape/mode the ramp
            # flag selected) and log loudly so the underlying gate defect is
            # not silently lost.
            # ----------------------------------------------------------
            _full_verdict = full_verdict_gate_enabled()
            _gate_mode = GATE_MODE_FULL_VERDICT if _full_verdict else GATE_MODE_THREE_CASE
            # Resolve the plan_task_id this turn was BOUND to at dispatch (plan 34
            # task 7): the blind-verification gate forces a bound COMPLETE to
            # NEEDS_VERIFICATION and lets an unbound turn self-COMPLETE. Best-effort
            # and non-fatal -- an unresolvable binding (no born-at-dispatch row, or
            # a DB read error) leaves plan_task_id None, i.e. treated as unbound.
            # The SAME resolved row (see ClaudeCodeAdapter._resolve_dispatch_row
            # for the lane order -- harness_agent_id first, then the identity
            # lanes) is reused below as the row-first gate's candidate, so the
            # binding lookup and the gate's row lookup can never disagree on
            # WHICH row is this turn's own.
            _bound_plan_task_id: Optional[int] = None
            _bound_dispatch_row: Optional[dict] = None
            try:
                _db_for_binding = task_info.get("db_path")
                _bound_dispatch_row = self._resolve_dispatch_row(
                    session_id=session_id,
                    agent_type=agent_type,
                    task_info=task_info,
                    parsed_contract=parsed_contract,
                    db_path=Path(_db_for_binding) if _db_for_binding else None,
                )
                if _bound_dispatch_row is not None:
                    _bound_plan_task_id = _bound_dispatch_row.get("plan_task_id")
                    if _bound_plan_task_id is None:
                        # The resolved row may be a CONTINUATION LINK: the
                        # harness lane collapses a chain to its live tip
                        # (collapse_continuation_chains), so the row judged here
                        # is the link, not the turn it continues. The mint
                        # carries the binding forward, but a link minted without
                        # it would otherwise read as unbound and let a
                        # plan-task-bound producer self-sign COMPLETE through
                        # the resumption. The writer's reader walks the chain
                        # for exactly that case; it degrades to None on any
                        # error, same as the outer handler.
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
            # An exception below leaves this at its pre-gate value (None), the
            # same value GATE_SOURCE_ROW_MISSING carries: a gate failure
            # preserves no nonce, exactly as a turn with no reachable row
            # preserves none. The pre-retirement code degraded to the fence
            # here instead, which is the one substitution this migration
            # removes.
            _authoritative_envelope: Any = None
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

            # ----------------------------------------------------------
            # Rejection circuit breaker.
            #
            # A rejection is handed back to the SUBAGENT, which repairs and
            # stops again -- and nothing used to count how often that had
            # already happened this turn (measured: eleven passes, 361k
            # tokens, a byte-identical message every time). Counting HERE, at
            # the one place the verdict is known, is what lets the rejection
            # message carry an attempt number and lets the third rejection end
            # the turn instead of extending the loop.
            #
            # Isolated exactly like the relay below: the breaker may enrich or
            # terminate a rejection, but a failure inside it must never erase
            # one, so `_circuit = None` (no ceiling, gate unchanged) is the
            # degraded outcome and it is reported rather than swallowed.
            # ----------------------------------------------------------
            _circuit = None
            _circuit_key = None
            _circuit_unkeyed = False
            if _gate.rejected:
                try:
                    from modules.agents import rejection_circuit

                    _circuit_key = rejection_circuit.counter_key(session_id, task_info)
                    if _circuit_key:
                        _circuit = rejection_circuit.record_rejection(_circuit_key)
                    else:
                        # No per-dispatch identity -> no key that belongs to this
                        # turn alone. Cutting on a shared key ends turns that
                        # never failed, so the breaker stands down and says so.
                        _circuit_unkeyed = True
                        logger.warning(
                            "Rejection circuit: no harness agent_id for %s "
                            "(session=%s), so this turn cannot be counted "
                            "separately from any other; the ceiling is NOT in "
                            "force for it.",
                            agent_type, session_id,
                        )
                except Exception as _circuit_exc:
                    logger.warning(
                        "Rejection circuit failed for %s (non-fatal); the retry "
                        "ceiling is NOT in force this turn: %s",
                        agent_type, _circuit_exc,
                    )
            # Resolved here, not at the verdict below: the episode write and the
            # anomaly append both happen earlier in this method and both have to
            # know the turn was cut.
            _circuit_tripped = bool(_circuit is not None and _circuit.tripped)

            # Preserve a pending approval this turn's own record still
            # references via APPROVAL_REQUEST. Cleanup must not destroy an
            # approval the user is being asked to act on -- and "this turn's
            # own record" is the SAME source the gate above just treated as
            # authoritative (_authoritative_envelope: the persisted dispatch
            # row's envelope whenever a row was reachable at all -- cleanly
            # finalized or not, see GATE_SOURCE_ROW /
            # GATE_SOURCE_ROW_UNFINALIZED -- and None when none was, see
            # GATE_SOURCE_ROW_MISSING). Reading the fence here (the
            # pre-inversion behavior) silently dropped the nonce for any turn
            # whose approval was recorded on the row but never echoed in a
            # final fenced declaration. Losing the fence as a fallback costs
            # almost nothing in the other direction: cleanup_approval only
            # EXPIRES pendings already past the 24h TTL, so a nonce minted
            # during the turn that just ended is never young enough to be at
            # risk.
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

            cleanup_approval(
                agent_type,
                session_id=session_id,
                preserve_nonces=preserved_nonces if preserved_nonces else None,
            )

            # NOTE (approvals redesign, M1): grants are consumed AT THE MATCH by
            # bash_validator (PENDING->CONSUMED when the command is authorized in
            # PreToolUse), NOT swept at SubagentStop. A grant that was never
            # presented to a matching retry stays PENDING and expires on its own
            # short (5m) TTL, so it must survive the subagent ending. The former
            # consume_session_grants() sweep has been removed.

            # Union, not substitution: _authoritative_envelope (resolved
            # above by the gate, no extra query) carries commands checkpointed
            # incrementally onto the row, while agent_output's fence carries
            # whatever the final message declared -- each loses different
            # turns when read alone (a fence missing its final block loses the
            # row's evidence; a row never mirrored before finalize loses the
            # fence's). See merge_commands_executed() for the dedup/order
            # decision.
            commands_executed = extract_commands_executed(
                agent_output=agent_output, row_envelope=_authoritative_envelope,
            )

            # ----------------------------------------------------------
            # Process update_contracts array (agent_contract_handoff envelope path).
            # Handles evidence routing to the evidence table and any
            # project_context entries in the envelope format.
            # Non-blocking: errors caught inside process_update_contracts.
            # ----------------------------------------------------------
            context_update_result = None
            if isinstance(parsed_contract, dict):
                _update_contracts_task_info = {
                    "agent": agent_type,
                    "db_path": task_info.get("db_path"),
                    "cloud_scope": task_info.get("cloud_scope"),
                    "workspace": task_info.get("workspace"),
                }
                _update_contracts_result = process_update_contracts(
                    parsed_contract, _update_contracts_task_info
                )
                if _update_contracts_result.get("updated"):
                    context_update_result = {
                        "updated": True,
                        "contract": ", ".join(_update_contracts_result.get("contracts", [])),
                    }
                if _update_contracts_result.get("rejected"):
                    logger.warning(
                        "update_contracts rejected for %s: %s",
                        agent_type,
                        _update_contracts_result.get("errors", []),
                    )

            # ----------------------------------------------------------
            # Auto-capture install events (B4)
            # Detect npm/pip/gaia install and auth configure patterns in
            # agent_output; persist to integrations table via store API.
            # Non-blocking: errors are logged but do not affect the hook.
            # Lazy imports keep this entirely opt-in -- no module-load
            # side effects affect tests that do not exercise installs.
            # ----------------------------------------------------------
            try:
                from modules.install_detector import detect, resolve_workspace, build_topic_key
                _install_match = detect(agent_output)
                if _install_match.get("matched"):
                    from gaia.store import save_integration
                    _ws = resolve_workspace()
                    _tgt = _install_match["target"]
                    _kind = _install_match.get("kind", "pkg")
                    _tk = build_topic_key(_kind, _tgt)
                    _store_result = save_integration(
                        workspace=_ws,
                        name=_tgt,
                        kind=_kind,
                        topic_key=_tk,
                        agent="system",
                    )
                    logger.info(
                        "Install capture: target=%s kind=%s workspace=%s store=%s",
                        _tgt, _kind, _ws, _store_result.get("status"),
                    )
            except Exception as _exc:
                logger.debug("Install capture failed (non-fatal): %s", _exc)

            # Compute context anchor hit tracking
            anchor_hits = None
            try:
                from modules.context.anchor_tracker import (
                    cleanup_anchors,
                    compute_anchor_hits,
                    extract_tool_calls_from_transcript,
                    load_anchors,
                )
                transcript_path = task_info.get("agent_transcript_path", "")
                # task_info_builder.py defaults agent_id to the literal string
                # "unknown" when the host omits it -- treat that placeholder
                # as absent here too, or two dispatches that both lack a real
                # agent_id would collide on the same fake "unknown" key,
                # reintroducing the exact bug this rekey fixes.
                dispatch_agent_id = task_info.get("agent_id", "")
                if dispatch_agent_id == "unknown":
                    dispatch_agent_id = ""
                anchors = load_anchors(session_id, agent_type, dispatch_agent_id)
                if anchors and transcript_path:
                    tool_calls = extract_tool_calls_from_transcript(transcript_path)
                    # Only report a rate when there were trackable tool calls to
                    # check. Zero tool calls means zero observations, not zero
                    # hits -- compute_anchor_hits([], anchors) would otherwise
                    # return hit_rate=0.0, indistinguishable downstream from a
                    # genuine "agent ignored the context" zero.
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

            # consolidation_required is False unconditionally: its signal
            # (the preloaded injected-context payload) retired with the
            # dispatch-kernel migration, so no task can be identified as
            # multi-surface at this seam anymore.
            response_contract = validate_response_contract(
                agent_output,
                task_agent_id=resolve_agent_id(task_info),
                consolidation_required=False,
                parsed_contract=parsed_contract,
            )
            save_validation_result(task_info, response_contract)

            anomalies = audit_workflow(
                workflow_metrics,
                agent_output,
                task_info,
                rejected_sections=(context_update_result or {}).get("rejected", []),
                transcript_analysis=transcript_analysis,
            )
            # ----------------------------------------------------------
            # Shape-invalidity anomalies (T16 / AC-9 "exactly one anomaly per
            # invalidity, not two").
            #
            # Ramp ON (full-verdict): the SINGLE core is the shape-enforcement
            # SSOT. Signal EXACTLY ONE anomaly per invalidity from the core (via
            # the gate) and SUPPRESS the legacy double -- validate_contract's
            # contract_validation_failure AND response_contract's
            # response_contract_violation, which independently re-report the
            # same invalidity (the double-anomaly bug). A salvaged truncation
            # yields no shape anomaly (the degraded row already captured it).
            #
            # Ramp OFF (3-case): byte-identical legacy behavior -- both legacy
            # shape anomalies are appended as before.
            # ----------------------------------------------------------
            if _full_verdict:
                anomalies.extend(_gate.anomalies)
            else:
                # BUG D fix: merge validate_contract() anomalies collected earlier
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

            # A tripped breaker is recorded HERE, ahead of write_episode, so it
            # reaches the persisted episode_anomalies floor an operator queries
            # with `gaia defects` -- the advisory appends further down only ever
            # reach the returned dict. A breaker that could not count is
            # recorded too: a turn running without the ceiling must not look
            # like a turn that simply never reached it.
            if _circuit is not None or _circuit_unkeyed:
                try:
                    from modules.agents import rejection_circuit

                    if _circuit_unkeyed:
                        anomalies.append(rejection_circuit.no_key_anomaly(agent_type))
                    elif _circuit.tripped:
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

            # ----------------------------------------------------------
            # Compliance score (T011)
            # Computed after audit so anomalies are available for
            # has_scope_escalation detection.
            # ----------------------------------------------------------
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

            # ----------------------------------------------------------
            # Contract-reported defect -> raw defect floor.
            #
            # Unrequested: the agent opts into nothing; emitting a
            # failure_report in its contract is its whole participation.
            # Non-blocking: build_defect_anomaly never raises, and the
            # write below (episode_writer.write -> store_episode ->
            # insert_episode_anomaly) logs and continues past a rejected
            # insert. Placed BEFORE write_episode -- the later advisory
            # appends (verbatim/approval/skill checks) only reach the
            # returned dict, never the persisted episode.
            # ----------------------------------------------------------
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

            # A cut turn is a failed turn everywhere it is recorded. The episode's
            # own derivation reads plan_status -- what the turn CLAIMED about
            # itself -- so a turn whose envelope says COMPLETE was stored as a
            # 'success' on the very pass the breaker cut it.
            episode_id = write_episode(
                workflow_metrics,
                anomalies=anomalies if anomalies else None,
                commands_executed=commands_executed,
                outcome_override="failed" if _circuit_tripped else None,
            )

            # ----------------------------------------------------------
            # T11 truncation salvage (M5 / AC-12): fast-path rescue.
            #
            # A max_tokens truncation (STOP_REASON_TRUNCATION -- the turn was
            # cut off by the token budget, NOT the agent's choice) that left a
            # partial draft on disk is EARLY auto-finalized to a degraded=true
            # row. It runs BEFORE the T9 backstop (persist_handoff, below) and
            # keys on the SAME contract_id, so the salvage row (marked
            # salvaged="truncation") wins and the writer's
            # ON CONFLICT(contract_id) DO NOTHING leaves the backstop passive --
            # the two converge to ONE row. OPTIMIZATION, never a gate: it never
            # raises and never alters contract_rejected/exit_code; the T9
            # backstop remains the correctness floor if salvage found nothing.
            # ----------------------------------------------------------
            _salvage = None
            if _stop_reason_classification == STOP_REASON_TRUNCATION:
                _salvage = self._salvage_truncated_draft(
                    parsed_contract=parsed_contract,
                    task_info=task_info,
                    session_id=session_id,
                    plan_task_id=_bound_plan_task_id,
                )

            # ----------------------------------------------------------
            # BUG C fix: Persist handoff row to DB (M4 / T4.2).
            # Wrapped in try/except per T4.2 spec -- DB failures must NOT
            # crash the hook.
            # ----------------------------------------------------------
            _captured_contract_id = None
            try:
                from modules.agents.handoff_persister import persist_handoff
                _capture = persist_handoff(
                    parsed_contract=parsed_contract,
                    agent_output=agent_output,
                    task_info=task_info,
                    session_id=session_id,
                    # The dispatch binding this adapter already resolved above.
                    # Attribution belongs on the row, and THIS layer is where
                    # reading a harness coordinate is legitimate -- the CLI
                    # finalize path can only receive it as an explicit flag, so a
                    # turn that does not pass one would otherwise persist an
                    # unattributable contract.
                    plan_task_id=_bound_plan_task_id,
                )
                if isinstance(_capture, dict):
                    _captured_contract_id = _capture.get("contract_id")
            except Exception as _handoff_exc:
                logger.warning(
                    "M4: handoff persistence call failed (non-blocking): %s",
                    _handoff_exc,
                )

            # Write AGENT_COMPLETE event (non-blocking)
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

            # How many times THIS turn's contract has been rejected, from the
            # breaker that actually counts them. This used to read a
            # `repair_attempts` key off ResponseContractValidation -- an
            # eleven-field dataclass that has no such field -- so it was 0 on
            # every turn, including the eleven-rejection one.
            contract_attempts = _circuit.attempt if _circuit is not None else 0

            # ----------------------------------------------------------
            # Option D: Cross-field validation for verbatim_outputs
            # Advisory only -- adds to anomalies but never blocks.
            # ----------------------------------------------------------
            verbatim_check = validate_verbatim_outputs_consistency(parsed_contract)
            if verbatim_check:
                anomalies.append(verbatim_check)
                logger.info(
                    "Verbatim outputs consistency warning for %s: %s",
                    agent_type, verbatim_check.get("message", ""),
                )

            # ----------------------------------------------------------
            # Extract agent_state for downstream checks (canonical field
            # resolved earlier via _resolve_status).
            # ----------------------------------------------------------
            _agent_state = _resolved_agent_state

            # ----------------------------------------------------------
            # State transition tracking
            # Validates that agent state transitions follow the state
            # machine (e.g., no IN_PROGRESS -> COMPLETE without APPROVAL_REQUEST
            # when T3 is involved). Advisory warnings, hard reject only
            # for illegal transitions.
            # ----------------------------------------------------------
            try:
                from modules.agents.state_tracker import track_transition
                _agent_id = resolve_agent_id(task_info)
                # AC-19: distinguish a legitimate mid-conversation RESUME from a
                # within-turn retry. An EXACT per-session resume mapping (written
                # at PreToolUse:SendMessage) means the orchestrator is continuing
                # this session's agent across messages, so IN_PROGRESS must not
                # trip the retry cap. Use the exact file only (never the fuzzy
                # cross-session fallback in _read_resume_mapping) so a fresh,
                # non-resumed dispatch keeps the anti-parking cap intact.
                _is_resume = (
                    self.RESUME_MAP_CACHE_DIR / f"{session_id}.json"
                ).is_file()
                if _agent_state and _agent_id:
                    transition_result = track_transition(
                        _agent_id,
                        _agent_state,
                        has_review_phase=False,  # Conservative: no T3 detection yet
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

            # ----------------------------------------------------------
            # Approval request validation
            # Advisory only -- adds to anomalies but never blocks.
            # ----------------------------------------------------------
            if parsed_contract is not None:
                approval_check = validate_approval_request(parsed_contract, _agent_state)
                if approval_check:
                    anomalies.append(approval_check)
                    logger.info(
                        "Approval request validation for %s: %s",
                        agent_type, approval_check.get("detail", ""),
                    )

            # ----------------------------------------------------------
            # Skill injection verification
            # Advisory only -- adds to anomalies but never blocks.
            # ----------------------------------------------------------
            try:
                from modules.agents.skill_injection_verifier import verify_skill_injection
                from modules.audit.workflow_recorder import load_agent_runtime_profile
                agent_profile = load_agent_runtime_profile(agent_type)
                declared_skills = agent_profile.get("skills", [])
                # Files the agent wrote/edited this turn -- routes the
                # expectation through artifact_skill_map, independent of
                # what the agent's frontmatter declares.
                written_paths = [
                    tc.arguments.get("file_path", "")
                    for tc in (transcript_analysis.tool_sequence if transcript_analysis else [])
                    if tc.tool_name in ("Write", "Edit")
                ]
                written_paths = [p for p in written_paths if p]
                # Search the FULL transcript (every role), not just the
                # agent's last message -- a skill's fingerprint is injected
                # earlier in the turn, in a tool-result entry last_message
                # never carries.
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

            # ----------------------------------------------------------
            # T16/M4: contract gate verdict (computed ONCE, early, via the ramp
            # flag -- see _gate above). Ramp OFF -> the legacy 3-case Option B
            # verdict (byte-identical). Ramp ON -> the full-verdict verdict from
            # the SINGLE portable core: a previously-exit-0 invalid envelope now
            # rejects with the RICH repair message, which
            # subagent_stop._handle_subagent_stop delivers to stderr via
            # contract_rejection_reason. A salvaged truncation is never a hard
            # rejection (the degraded row already captured it).
            # ----------------------------------------------------------
            contract_rejected = _gate.rejected
            contract_rejection_reason = _gate.rejection_reason

            # The trip. exit_code=2 is what invites the subagent to repair, so
            # ending the loop means NOT raising the rejection flag -- the turn
            # stops here instead of coming back for pass four.
            #
            # DEGRADED, NOT CERTIFIED: nothing below finalizes a row, promotes a
            # state, or claims a verdict the turn did not earn. Ending a turn is
            # not the same as passing it.
            #
            # Saying so in the returned dict is NOT sufficient, which is the
            # correction this branch carries. The last-resort backstop records a
            # fence-only turn's SELF-DECLARED agent_state, so an agent that
            # emitted a COMPLETE envelope and never finalized its row leaves a
            # row reading COMPLETE -- written on the FIRST pass, long before any
            # cut. Ending the turn there froze that row as the final word while
            # the return said the opposite. The demotion below reconciles it, and
            # is safe because it can only reach a row carrying a cut mark: a row
            # the agent finalized itself is unreachable from that statement.
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
                    # A cut that leaves a COMPLETE row standing is the failure
                    # this branch exists to prevent, so it is reported loudly and
                    # carried into the result rather than swallowed.
                    _demoted = {"status": "error", "reason": str(_demote_exc)}
                    logger.error(
                        "Rejection circuit: could NOT reconcile the contract row "
                        "for %s (%s); a row claiming COMPLETE may survive this "
                        "cut.", agent_type, _demote_exc,
                    )

            # stop_reason resolution (T10) and truncation salvage (T11) were
            # computed earlier, BEFORE the T9 backstop, so the salvage row wins
            # and the backstop stays passive. The values flow into result below.

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
                # Resume hint rendered by view.py's single renderer (T14) --
                # never re-inlined here -- so a resume continues the SAME
                # salvaged draft via --draft-id instead of re-emitting the block.
                result["salvage_resume_hint"] = _salvage.get("resume_hint")

            # The verdict is recorded BEFORE the relay runs. exit_code=2 is
            # driven by result['contract_rejected'] alone, and the outer except
            # below rebuilds `result` without that key -- so anything that can
            # raise between here and the return would downgrade a rejection to
            # exit 0. The relay is an enrichment of the rejection, never a
            # precondition for it, and is isolated accordingly.
            if contract_rejected:
                result["contract_rejected"] = True
                result["contract_rejection_reason"] = contract_rejection_reason
                logger.warning(
                    "Contract rejected for %s: %s",
                    agent_type, contract_rejection_reason.split("\n")[0],
                )

            # The degraded close, stated on the result so it cannot be mistaken
            # for a clean one. `contract_complete: False` is asserted rather than
            # left to be inferred from the ABSENCE of contract_rejected -- an
            # absent key reads identically to a turn that was never rejected at
            # all, and this turn was rejected until the loop was cut.
            if _circuit_tripped:
                result["status"] = "contract_circuit_open"
                result["contract_circuit_open"] = True
                result["contract_closed_degraded"] = True
                result["contract_complete"] = False
                result["contract_rejection_count"] = _circuit.attempt
                result["contract_rejection_limit"] = _circuit.limit
                result["contract_degraded_close_reason"] = _degraded_reason
                result["contract_row_reconciled"] = _demoted.get("status")

                # A degraded close exits 0, and on exit 0 the harness reads
                # stdout JSON and sends stderr to the debug log where the model
                # never sees it. So the notice travels by the two channels that
                # ARE read: additionalContext reaches the model as a system
                # reminder, systemMessage reaches the user. `decision: block` is
                # deliberately NOT used -- blocking the stop is the retry loop
                # this breaker exists to end.
                _cut_notice = (
                    f"El turno de {agent_type} fue CORTADO por el circuito de "
                    f"rechazos tras {_circuit.attempt} rechazos de contrato "
                    f"(limite {_circuit.limit}). El turno TERMINO DEGRADADO: no "
                    "esta certificado, el contrato NO quedo completo y su fila "
                    "persistida no fue finalizada. No trates su ultimo mensaje "
                    "como un cierre valido, aunque declare COMPLETE."
                )
                result["systemMessage"] = _cut_notice
                result["hookSpecificOutput"] = {
                    "hookEventName": "SubagentStop",
                    "additionalContext": (
                        f"{_cut_notice}\n\nUltimo veredicto de la compuerta:\n"
                        f"{contract_rejection_reason}"
                    ),
                }

            # A rejection sends the turn back to the SUBAGENT, whose repair
            # message then REPLACES the rejected one in everything the
            # orchestrator receives -- so the substantive work of the rejected
            # turn is lost in the relay unless it is preserved and handed back.
            # The gate stays as strict as before; only the cost of a rejection
            # changes. See modules/agents/rejected_turn_relay.py.
            #
            # The branch keys on the GATE's verdict, not on `contract_rejected`:
            # a tripped turn was rejected and had its flag cleared to end the
            # loop, and running the accepted-path cleanup on it would delete the
            # preserved evidence of the very turn the breaker just cut.
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
                        # Only a turn that is going back for repair receives the
                        # reinjected text and the attempt counter; a tripped turn
                        # is not being invited to read anything.
                        contract_rejection_reason = _relay["reason"]
                        if _circuit is not None and not _circuit.error:
                            contract_rejection_reason += rejection_circuit.retry_notice(_circuit)
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

            # The queryable trace of the tripped turn, landed on the same
            # append-only channel as agent.contract_rejected so an operator
            # reaches it with the verb that already exists. Written LAST, once
            # the preserved-evidence path is known, and never able to raise.
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

        if result.get("contract_rejected"):
            logger.warning("Returning exit_code=2 due to contract rejection")
            return HookResponse(output=result, exit_code=2)

        return HookResponse(output=result, exit_code=0)

    # ------------------------------------------------------------------ #
    # T11: truncation salvage (M5 / AC-12)
    # ------------------------------------------------------------------ #

    def _reconstruct_contract_from_finalized_draft(
        self,
        *,
        task_info: dict,
        parsed_contract,
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """M4 missing-fence footgun (Option A): rebuild the envelope from a
        FINALIZED draft when the agent forgot to echo the fence.

        The SubagentStop gate parses the fenced ``agent_contract_handoff`` out
        of the agent's response TEXT -- not its finalized DB row. So a turn that
        did all its work via the ``gaia contract`` CLI and ran ``gaia contract
        finalize`` (writing a valid terminal row) but never echoed the fence in
        its last message is hard-rejected by the full-verdict gate, and has to
        be resumed by hand. This addresses that hole: when the fence is missing
        but the agent's OWN draft was already finalized (a row exists keyed on
        its draft_id), reconstruct the envelope FROM that draft so the gate
        parses a valid contract instead of rejecting completed, persisted work.

        It closes the hole only as far as the draft is FINDABLE, and finding it
        is the fragile half. With no fence there is no envelope to read the
        minted agent id from, so the draft is located through
        ``resolve_minted_agent_id`` -- which, for the CURRENT dispatch shape,
        can only get there by the ``harness_agent_id`` bridge on the row, since
        a turn born with its draft already open never runs ``gaia contract
        init`` and so leaves no mint report in its transcript. ``session_id`` is
        threaded in for exactly that bridge; without it the join is unscoped.

        Fires ONLY when ``parsed_contract`` lacks a usable ``agent_status`` (no
        fence). "Finalized" is discriminated by the EXISTENCE of the terminal
        row for the draft_id -- without it the draft is merely in-progress
        (truncation-salvage / T9-backstop territory, which correctly produce
        ``degraded=true`` rows), NOT a finished turn missing only its fence.

        OPTIMIZATION, never a gate: every failure is swallowed and returns None,
        leaving the gate to reject as before. Returns the reconstructed envelope
        dict (tagged like ``parse_contract`` output) or None.

        EVERY MISS LOGS. This used to return None silently at four separate
        points, and that silence is why the resolver defect it depends on lived
        undetected: a turn whose ``update_contracts`` proposal was dropped
        (measured, handoff row 11304) looked exactly like a turn that had no
        proposal to begin with. The log lines below are the only difference
        between a diagnosable miss and an invisible one.
        """
        # A usable fence is already present -> nothing to reconstruct.
        if isinstance(parsed_contract, dict) and isinstance(
            parsed_contract.get("agent_status"), dict
        ):
            return None
        try:
            from gaia.contract.drafts import load_draft, resolve_draft_id
            from gaia.store import writer as _writer
            from modules.agents.handoff_persister import resolve_minted_agent_id
        except Exception as exc:
            logger.debug("M4 reconstruction: core import failed (non-fatal): %s", exc)
            return None

        # Fence absent -> the minted id comes from the transcript's mint report
        # or, for a turn born with its draft already open, from the row itself
        # via the harness_agent_id bridge. The harness agent_id is a different
        # id space and is never used as a draft key.
        minted_agent_id = resolve_minted_agent_id(
            parsed_contract, task_info, session_id=session_id,
        )
        if not minted_agent_id:
            logger.warning(
                "M4 reconstruction: fence missing AND no minted agent id "
                "resolvable (agent=%s session=%s) -- no draft can be located, "
                "so a finalized turn's envelope (including any update_contracts "
                "it carried) is NOT recovered.",
                task_info.get("agent"), session_id,
            )
            return None

        try:
            draft_id = resolve_draft_id(explicit=None, agent_id=str(minted_agent_id))
            if not draft_id:
                logger.warning(
                    "M4 reconstruction: no draft resolves for minted agent id %s "
                    "(agent=%s session=%s); nothing to reconstruct from.",
                    minted_agent_id, task_info.get("agent"), session_id,
                )
                return None
            db_path_str = task_info.get("db_path")
            db_path = Path(db_path_str) if db_path_str else None
            # "Finalized" == the agent's own `gaia contract finalize` already
            # wrote the TERMINAL row for this draft_id. If no terminal row exists,
            # the draft is not finalized -- do NOT reconstruct (that is the
            # salvage / backstop path's job, which marks the row degraded).
            # v37 born-at-dispatch: a NASCENT 'DISPATCHED' row born at dispatch is
            # NOT finalized, so use the terminal-row check (not "any row exists")
            # -- a born-but-orphaned row must not be mistaken for a completed one.
            if not _writer.agent_contract_handoff_finalized(draft_id, db_path=db_path):
                logger.info(
                    "M4 reconstruction: draft %s exists but its row is not "
                    "finalized -- salvage/backstop territory, not a completed "
                    "turn missing only its fence.",
                    draft_id,
                )
                return None
            envelope = load_draft(draft_id)
            if not isinstance(envelope, dict) or not isinstance(
                envelope.get("agent_status"), dict
            ):
                logger.warning(
                    "M4 reconstruction: draft %s is finalized but its on-disk "
                    "envelope is unusable (%s) -- cannot rebuild the fence.",
                    draft_id, type(envelope).__name__,
                )
                return None
            recon = dict(envelope)
            # Tag it exactly like parse_contract output so every downstream
            # consumer (agent_state resolution, the gate, update_contracts)
            # treats it uniformly, plus a provenance marker for the audit trail.
            recon["_contract_tag"] = "agent_contract_handoff"
            recon["reconstructed_from_finalized_draft"] = draft_id
            logger.info(
                "M4 reconstruction: fence missing but finalized draft %s found; "
                "envelope reconstructed so the gate parses the completed contract.",
                draft_id,
            )
            return recon
        except Exception as exc:
            logger.warning("M4 reconstruction: rebuild failed (non-fatal): %s", exc)
            return None

    def _salvage_truncated_draft(
        self,
        *,
        parsed_contract,
        task_info: dict,
        session_id: str,
        plan_task_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fast-path rescue of a TRUNCATED turn's partial contract draft.

        Called ONLY when the adapter classified the stop_reason as
        ``STOP_REASON_TRUNCATION`` (max_tokens): the turn was cut off by the
        token budget mid-work, not by the agent's choice, so its on-disk draft
        is a salvage candidate. This early auto-finalizes that draft to a
        ``degraded=true`` row via the idempotent core writer, keyed on the SAME
        ``contract_id`` (the draft_id resolved from the agent's minted
        agent_id) that the T9 hook backstop keys on -- so salvage and backstop
        converge to ONE row through ``ON CONFLICT(contract_id) DO NOTHING``.

        Consistency with T9 semantics: the row is marked ``degraded=true`` (it
        is NOT an agent-verified COMPLETE) with a ``salvaged="truncation"``
        marker recording WHY it degraded; only flags are added, never
        fabricated evidence. agent_state mirrors T9: the draft's own
        agent_state when it is a valid terminal value, else the honest
        ``IN_PROGRESS``.

        OPTIMIZATION, never a gate: every failure is swallowed and returns
        None; this never raises and never alters contract_rejected/exit_code.
        The T9 backstop (``persist_handoff`` later in the same lifecycle)
        remains the correctness floor. Returns
        ``{"contract_id", "resume_hint", "created"}`` when a draft was
        salvaged, else None.
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

        # Resolve the minted agent_id drafts are addressed by via the SHARED
        # resolver, so salvage, the T9 backstop, and the M4 reconstruction path
        # all resolve the SAME draft (hence the SAME contract_id).
        from modules.agents.handoff_persister import resolve_minted_agent_id
        minted_agent_id = resolve_minted_agent_id(
            parsed_contract, task_info, session_id=session_id,
        )
        if not minted_agent_id:
            return None

        try:
            draft_id = resolve_draft_id(explicit=None, agent_id=str(minted_agent_id))
            if not draft_id:
                # No partial draft to salvage -- the T9 backstop still captures
                # a minimal degraded row for a no-draft truncated turn.
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

            # Cleaned on the way in, exactly as the T9 backstop cleans its own
            # rescued envelope and the CLI cleans an agent's write: a salvaged
            # draft is the least validated input in the system -- a partial
            # write the token budget interrupted -- and it used to be persisted
            # verbatim. Cleaning cannot cost the salvage; the helper falls back
            # to the envelope as it arrived rather than raise.
            from modules.agents.handoff_persister import clean_rescue_envelope

            cleaning_log: list = []
            salvaged = dict(clean_rescue_envelope(envelope, log=cleaning_log))
            if cleaning_log:
                logger.debug(
                    "T11 salvage: cleaned draft %s: %s",
                    draft_id, "; ".join(str(line) for line in cleaning_log),
                )
            # Read from the RAW draft, not the cleaned copy -- the same split
            # the T9 backstop makes, and for the same reason: canonicalizing the
            # state here would change what a rescued turn is recorded as, which
            # is a policy decision separate from cleaning the envelope. So an
            # uncanonical spelling still falls to IN_PROGRESS below rather than
            # being repaired into a terminal verdict.
            agent_status = envelope.get("agent_status")
            agent_state = (
                agent_status.get("agent_state")
                if isinstance(agent_status, dict)
                else None
            )
            agent_state = (
                agent_state if agent_state in VALID_PLAN_STATUSES else "IN_PROGRESS"
            )
            if agent_state == "COMPLETE":
                # A COMPLETE in a SALVAGED draft is a claim, not a verdict. This
                # lane runs only for a turn the token budget cut off mid-work: it
                # never reached its own `gaia contract finalize`, so nothing
                # verified that COMPLETE, and recording it would falsely satisfy
                # the briefs "plan closed => a COMPLETE handoff row exists"
                # invariant (gaia/briefs/store.py, invariant 5) for a turn that
                # did not complete. The T9 backstop already downgrades exactly
                # this claim when it converges an unfinalized row; this lane had
                # no downgrade of its own, so the SAME truncated turn was
                # recorded COMPLETE or IN_PROGRESS depending only on which rescue
                # reached it first. The claim itself is not erased -- it stays in
                # the salvaged envelope under agent_status.agent_state, beside
                # the `salvaged` marker that says why the row disagrees with it.
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
                # The structural twin of the ``salvaged`` envelope flag above: a
                # rescued draft is a turn the token budget ended, never a
                # closure the agent chose, so the row must not read as cleanly
                # finalized just because a writer reached it.
                cut_reason=CUT_REASON_SALVAGED_TRUNCATION,
                db_path=db_path,
            )

            # Reuse view.py's single renderer (T14) for the resume hint -- do
            # NOT re-inline hint text here.
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

    # ------------------------------------------------------------------ #
    # P2: adapt_stop
    # ------------------------------------------------------------------ #

    def adapt_stop(self, raw: dict) -> QualityResult:
        """Parse Stop event and assess response quality.

        Extracts the response content from the Stop payload and evaluates
        whether the output meets evidence quality thresholds.

        Returns:
            QualityResult with quality assessment.
            Default: quality_sufficient=True (passthrough until business logic wired).
        """
        # Write SESSION_END event (non-blocking)
        try:
            from modules.events.event_writer import EventWriter, SESSION_END
            stop_reason = raw.get("stop_reason", "unknown")
            EventWriter().write_event(
                SESSION_END, "hook", "",
                f"session ended: {stop_reason}",
            )
        except Exception:
            pass  # Events are non-critical

        # Reconcile T3 commands that FAILED. PostToolUse does NOT fire for a
        # non-zero Bash exit in the current host, so an approved T3 command that
        # failed never got its terminal event -- its keyed pre-hook state is left
        # dangling. Stop is the point where the turn is fully done, so any keyed
        # state still present belongs to a command that never completed a
        # PostToolUse, i.e. a failure. The Stop payload carries session_id +
        # transcript_path, which is exactly what reconciliation needs.
        self._reconcile_dangling_t3_on_stop(
            session_id=raw.get("session_id", ""),
            transcript_path=raw.get("transcript_path", ""),
        )

        return QualityResult(
            quality_sufficient=True,
            score=1.0,
            missing_elements=[],
            recommendation="continue",
        )

    # ------------------------------------------------------------------ #
    # P2: adapt_task_completed
    # ------------------------------------------------------------------ #

    def adapt_task_completed(self, raw: dict) -> VerificationResult:
        """Parse TaskCompleted event and verify completion criteria.

        Extracts task output and metadata from the TaskCompleted payload.
        Checks if the task's acceptance criteria are met.

        Returns:
            VerificationResult with criteria assessment.
            Default: criteria_met=True (passthrough until business logic wired).
        """
        return VerificationResult(
            criteria_met=True,
            verified_items=[],
            failed_items=[],
            block_completion=False,
        )

    # ------------------------------------------------------------------ #
    # Context cache: PreToolUse -> SubagentStart bridge
    # ------------------------------------------------------------------ #

    CONTEXT_CACHE_DIR = Path("/tmp/gaia-context-cache")
    CONTEXT_CACHE_TTL_SECONDS = 60  # Cache entries older than this are stale

    def _cache_context_for_subagent(
        self, session_id: str, agent_type: str, context: str,
        task_description: str = "",
    ) -> Path:
        """Write the session-events digest to a cache file for SubagentStart.

        This bridge survives for ONE cargo: the session-events digest
        (``build_session_events``), which is computed at PreToolUse:Task from
        the dispatch parameters and cannot be recomputed at SubagentStart --
        that event's payload carries neither the Task tool_input nor the
        project-agent roster the digest is derived from. Everything else that
        once rode here is gone: the born row's contract identity is recovered
        at SubagentStart by ``claim_dispatch_row`` (a DB fact needs no cache),
        and context anchors died with the preloaded-context path. What would
        kill the bridge entirely: carrying the digest on the born row's
        kernel payload (so the claim delivers it), or a host that lets
        SubagentStart see the dispatch parameters.

        ``task_description`` is the Task tool's own ``description`` parameter,
        stored purely as a CORRELATION key: SubagentStart's payload carries a
        field of the same name, so when the two agree the bridge can pair a
        start with the dispatch that produced it instead of guessing by recency
        (see ``_read_cached_context``). It is never injected into the subagent.

        Returns the path to the cache file.
        """
        self.CONTEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        cache_file = self.CONTEXT_CACHE_DIR / f"{session_id}-{timestamp}.json"
        payload = {
            "context": context,
            "agent_type": agent_type,
            "session_id": session_id,
            "task_description": task_description or "",
            "created_at": time.time(),
        }
        cache_file.write_text(json.dumps(payload))
        logger.debug("Context cache written: %s", cache_file)
        return cache_file

    def _read_cached_context(
        self,
        session_id: str,
        agent_type: str = "",
        task_description: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Read and consume the cached context this subagent start belongs to.

        Finds the cache entries for the session, picks the one that best
        CORRELATES with the starting subagent, deletes it (one-shot
        consumption), and cleans up stale entries.

        Correlation, strongest first -- this is what keeps two concurrent
        dispatches from swapping payloads:
          1. Same ``agent_type`` AND same ``task_description``. The Task tool's
             ``description`` and SubagentStart's ``task_description`` are the
             only pair of fields the two events share beyond the agent name, so
             when both are present and agree the pairing is exact rather than
             inferred.
          2. Same ``agent_type``. Eliminates cross-TYPE contamination, which is
             the common shape of a concurrent dispatch.
          3. Most recent for the session -- the historical behavior, kept as the
             fallback so a host that supplies neither discriminator, and every
             existing caller passing only ``session_id``, behave exactly as
             before.
        Within each tier the newest entry wins, matching the prior contract.

        This narrows the crossing window; it does not close it. Two concurrent
        dispatches of the SAME agent type with the SAME description remain
        indistinguishable at this seam, because SubagentStart's payload carries
        no token identifying WHICH dispatch it is starting. Closing that needs a
        correlation token from the host, not a better heuristic here.

        Returns None if no cache is found.
        """
        if not self.CONTEXT_CACHE_DIR.exists():
            return None

        # Find all cache files for this session, sorted newest-first
        candidates: List[Path] = sorted(
            self.CONTEXT_CACHE_DIR.glob(f"{session_id}-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not candidates:
            # Fallback: try to find the most recent cache file regardless of
            # session_id, since the orchestrator session_id and the subagent
            # session_id may differ.
            all_files = sorted(
                self.CONTEXT_CACHE_DIR.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates = all_files

        now = time.time()

        # Collect the live entries first (newest-first order preserved) so the
        # correlation tiers can be applied across ALL of them rather than
        # committing to whichever happened to be newest.
        live: List[tuple] = []
        for cache_file in candidates:
            try:
                data = json.loads(cache_file.read_text())
                age = now - data.get("created_at", 0)

                if age > self.CONTEXT_CACHE_TTL_SECONDS:
                    # Stale entry -- clean up
                    cache_file.unlink(missing_ok=True)
                    logger.debug("Cleaned stale context cache: %s (age=%.1fs)", cache_file.name, age)
                    continue

                live.append((cache_file, data, age))

            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read context cache %s: %s", cache_file, exc)
                cache_file.unlink(missing_ok=True)
                continue

        selected = self._select_cached_entry(live, agent_type, task_description)

        result = None
        if selected is not None:
            cache_file, data, age = selected
            result = data
            cache_file.unlink(missing_ok=True)
            logger.debug("Consumed context cache: %s (age=%.1fs)", cache_file.name, age)

        # Clean up any remaining stale files (background hygiene)
        self._cleanup_stale_cache(now)

        return result

    @staticmethod
    def _select_cached_entry(
        live: List[tuple], agent_type: str, task_description: str,
    ) -> Optional[tuple]:
        """Pick the live cache entry correlating best with a starting subagent.

        ``live`` is ``[(path, payload, age), ...]`` newest-first. See
        ``_read_cached_context`` for the tier rationale; this is only the
        selection, split out so it is testable without touching the filesystem.
        """
        if not live:
            return None

        if agent_type:
            typed = [e for e in live if e[1].get("agent_type") == agent_type]
            if typed:
                if task_description:
                    exact = [
                        e for e in typed
                        if e[1].get("task_description") == task_description
                    ]
                    if exact:
                        return exact[0]
                return typed[0]

        return live[0]

    def _cleanup_stale_cache(self, now: float) -> None:
        """Remove cache files older than TTL."""
        if not self.CONTEXT_CACHE_DIR.exists():
            return
        for f in self.CONTEXT_CACHE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if now - data.get("created_at", 0) > self.CONTEXT_CACHE_TTL_SECONDS:
                    f.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                f.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    # Contract resume bridge: PreToolUse:SendMessage -> SubagentStart (T6)
    #
    # Brief: contract-as-managed-data-agent-contract-handoff-agnostico-por-cli
    # (M2, decision #3 / #8). ``gaia.contract.drafts`` (T5) keys a draft
    # purely by agent_id and never reads a harness session id -- that is
    # what keeps the CLI/core agnostic. But SubagentStart's own payload
    # (see the ClaudeCodeAdapter docstring's field table) carries only
    # session_id + agent_type on a resume, NEVER the resumed agent_id. This
    # section is the one CC-specific bridge that recovers "which agent_id is
    # this session resuming" so ``adapt_subagent_start`` can hand the
    # resumed agent its own draft back (AC-18) without it re-emitting
    # anything. It mirrors ``_cache_context_for_subagent`` /
    # ``_read_cached_context`` one directory over, with two differences
    # driven by the resume semantics: one file PER SESSION (overwritten on
    # every SendMessage, not timestamped) since a session may resume the
    # SAME agent across many messages (AC-19), and reads are
    # non-consuming (a mapping is a durable fact about a session's current
    # target agent, not a one-shot handoff payload).
    # ------------------------------------------------------------------ #

    RESUME_MAP_CACHE_DIR = Path("/tmp/gaia-contract-resume-map")
    RESUME_MAP_TTL_SECONDS = 24 * 60 * 60  # generous: spans a long resumed session

    def _cache_resume_mapping(self, session_id: str, agent_id: str) -> Path:
        """Record that ``session_id`` just resumed (SendMessage) ``agent_id``.

        Returns the path to the cache file (mainly for tests).
        """
        self.RESUME_MAP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = self.RESUME_MAP_CACHE_DIR / f"{session_id}.json"
        payload = {
            "agent_id": agent_id,
            "session_id": session_id,
            "created_at": time.time(),
        }
        cache_file.write_text(json.dumps(payload))
        logger.debug("Resume mapping cached: session=%s -> agent=%s", session_id, agent_id)
        return cache_file

    def _read_resume_mapping(self, session_id: str) -> Optional[str]:
        """Return the agent_id last resumed for ``session_id``, or None.

        Non-consuming (unlike the one-shot context cache): the same mapping
        must still be readable after N resumes of the same session
        (AC-19's "IN_PROGRESS across resumes"). Falls back to the most
        recently written mapping across ALL sessions when no exact match
        exists, mirroring ``_read_cached_context``'s own fallback for the
        orchestrator-session vs subagent-session id mismatch.
        """
        if not self.RESUME_MAP_CACHE_DIR.exists():
            return None
        self._cleanup_stale_resume_mappings()

        candidate = self.RESUME_MAP_CACHE_DIR / f"{session_id}.json"
        if not candidate.is_file():
            all_files = sorted(
                self.RESUME_MAP_CACHE_DIR.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidate = all_files[0] if all_files else None
        if candidate is None:
            return None

        try:
            data = json.loads(candidate.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        return data.get("agent_id") or None

    def _cleanup_stale_resume_mappings(self) -> None:
        """Remove resume-mapping files older than TTL."""
        now = time.time()
        for f in self.RESUME_MAP_CACHE_DIR.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if now - data.get("created_at", 0) > self.RESUME_MAP_TTL_SECONDS:
                    f.unlink(missing_ok=True)
            except (json.JSONDecodeError, OSError):
                f.unlink(missing_ok=True)

    def _build_resume_draft_context(self, session_id: str) -> Optional[str]:
        """Build a minimal, cache-safe additionalContext block surfacing a
        resumed agent's own in-progress contract draft (AC-18/AC-20; AC-16).

        Harness-agnostic by construction: this only touches
        ``gaia.contract.drafts`` (T5's harness-free storage/addressing) and
        ``gaia.contract.view`` (T14's single renderer) plus the CC-specific
        resume-mapping cache above -- it never reads ``CLAUDE_SESSION_ID`` and
        never mutates the draft; this is a read-only hint so the resumed agent
        continues writing the SAME draft via ``--draft-id`` instead of
        re-emitting the full ``agent_contract_handoff`` block from memory.

        Cache-safety (AC-16): the hint text is produced by
        ``gaia.contract.view.render_resume_hint`` -- ONE renderer shared with
        the token-savings measurement so the injected view and its measurement
        never diverge. That renderer orders the byte-stable invariant prefix
        (instructions + draft_id + CLI template) FIRST and the one volatile
        status line LAST, so the full variable contract is never re-injected
        atop the prompt and the cache-reusable prefix stays byte-stable across
        a fixed draft's resumes.
        """
        agent_id = self._read_resume_mapping(session_id)
        if not agent_id:
            return None
        try:
            from gaia.contract.drafts import resolve_draft_id, load_draft
            from gaia.contract.view import render_resume_hint
        except Exception:
            return None
        try:
            draft_id = resolve_draft_id(explicit=None, agent_id=agent_id)
        except Exception:
            # Several live drafts share this handle -- resolution refuses to
            # guess. Injecting a hint pointing at someone else's draft would
            # aim the resumed turn at the wrong contract, so the hint is simply
            # omitted; the agent still addresses its own draft by --draft-id.
            return None
        if not draft_id:
            return None
        envelope = load_draft(draft_id)
        if not envelope:
            return None
        return render_resume_hint(draft_id, envelope)

    # ------------------------------------------------------------------ #
    # v43 dispatch kernel: claim the born row, render the kernel blocks
    # ------------------------------------------------------------------ #

    @staticmethod
    def _maybe_claim_dispatch_kernel(raw: dict) -> Optional[str]:
        """Claim the born row this start correlates to, stamp it, render its kernel.

        The correlation keys are the host's own SubagentStart coordinates:
        ``prompt_id`` (matched against the ``dispatch_prompt_id`` stamped at
        birth) and ``task_description`` (against ``dispatch_description``),
        scoped by ``agent_type``. ``claim_dispatch_row`` owns the ladder and
        the divergent-signature guard.

        The harness agent id is stamped onto the claimed row HERE, right after
        the claim resolves, because the claim is where both identifier spaces
        first meet: the row carries the CLI-minted contract_id and ``raw``
        carries the host-assigned ``agent_id``. Stamping at the claim (instead
        of from a contract_id carried by the context cache) covers BOTH start
        lanes -- cache hit and cache miss -- where the cache-borne stamp
        silently lost the cache-miss lane, exactly the cut-turn traceability
        the stamp exists for. SubagentStop cannot be the seam: it never fires
        on a harness cut. The stamp runs before rendering so a render failure
        cannot lose it; both are best-effort and never block the start.

        Returns the joined kernel blocks, or None when nothing was claimed or
        rendering failed -- callers read None as "keep the legacy path",
        never as an error. Best-effort by contract: a start is never blocked
        by the kernel.
        """
        try:
            from gaia.store.writer import claim_dispatch_row, stamp_harness_agent_id
            from modules.context.kernel_builder import build_kernel_context

            agent_type = raw.get("agent_type", "")
            row = claim_dispatch_row(
                agent_name=agent_type or None,
                dispatch_prompt_id=raw.get("prompt_id") or None,
                dispatch_description=raw.get("task_description") or None,
            )
            if row is None:
                return None
            try:
                _stamp = stamp_harness_agent_id(
                    row.get("contract_id") or None,
                    raw.get("agent_id", "") or None,
                )
                if _stamp.get("status") == "applied":
                    logger.info(
                        "Harness agent id stamped: contract_id=%s harness_agent_id=%s",
                        row.get("contract_id"), raw.get("agent_id"),
                    )
            except Exception as exc:
                logger.debug("Harness agent id stamp failed (non-fatal): %s", exc)
            kernel = build_kernel_context(row, agent_name=agent_type)
            if kernel:
                logger.info(
                    "Dispatch kernel injected (contract_id=%s, agent=%s)",
                    row.get("contract_id"), agent_type or "unknown",
                )
            return kernel
        except Exception as exc:
            logger.debug("dispatch claim/kernel failed (non-fatal): %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # P2: adapt_subagent_start
    # ------------------------------------------------------------------ #

    def adapt_subagent_start(self, raw: dict) -> ContextResult:
        """Parse SubagentStart event and inject the dispatch kernel.

        What a freshly dispatched subagent receives is the KERNEL
        rendered from its claimed born row (# Your Contract / # Your CLI /
        # How the user works) plus, when present, the cached session-events
        digest. Project context is NOT preloaded (pulled on demand via the
        CLI) and surface routing is neither computed nor injected.

        Contributions, all optional, joined when present:
        1. Cache hit (normal start via Task/Agent tool): PreToolUse:Agent
           cached the events digest; this method forwards it, then claims the
           born row and injects the kernel.
        2. Cache miss + resume-mapping hit (T6, AC-18/AC-20): the CC session
           resuming this agent was recorded by
           ``_adapt_send_message``/``_cache_resume_mapping``; if that
           session_id (or, failing that, the most recent resume) maps to an
           agent_id with a live ``gaia.contract.drafts`` draft, surface a
           minimal summary of it so the resumed agent continues its own
           draft instead of re-emitting the contract block.
        3. Cache miss on a FRESH dispatch (the cache raced its 60s TTL): the
           born row is a DB fact, so the claim still resolves and the kernel
           is still injected.

        On every lane the claim is also the stamping seam: resolving the row
        is what joins the CLI-minted contract identity with the harness's own
        ``agent_id`` (see ``_maybe_claim_dispatch_kernel``).

        CLAIM-FAILURE FALLBACK (explicit, by design): when no kernel can be
        injected -- the claim found no row, refused an ambiguous correlation,
        or lost a race -- the turn starts WITHOUT a ``# Your Contract`` block.
        That is the agent-protocol skill's documented fallback shape: the
        agent runs a bare ``gaia contract init`` and works under the identity
        it mints. The born row (if one exists) stays unadopted and is closed
        by the SubagentStop persister -- superseded when the turn finalized
        its own row, reaped otherwise -- so no path leaves the turn without a
        contract or the row without a closure. The retired legacy identity
        block is NOT re-rendered as a fallback.
        """
        session_id = raw.get("session_id", "")

        # agent_type / task_description are passed as CORRELATION keys only:
        # they decide WHICH cached dispatch this start belongs to, so a
        # concurrent dispatch of another agent type never receives this
        # dispatch's events digest.
        cached = self._read_cached_context(
            session_id,
            agent_type=raw.get("agent_type", ""),
            task_description=raw.get("task_description", ""),
        )
        if cached:
            logger.info(
                "SubagentStart: forwarding cached context for agent=%s (session=%s)",
                cached.get("agent_type", "unknown"),
                session_id,
            )
            # A failed or refused claim leaves only the cached digest -- the
            # turn then follows the bare-init fallback (see docstring).
            context_text = cached["context"]
            kernel_context = self._maybe_claim_dispatch_kernel(raw)
            if kernel_context:
                context_text = "\n\n".join(filter(None, [
                    context_text, kernel_context,
                ]))
            return ContextResult(
                context_injected=True,
                additional_context=context_text,
                sections_provided=[],
            )

        # Cache-miss path (a SendMessage resume, or a fresh start that raced
        # the cache TTL). No project-context rebuild here -- the resumed turn
        # pulls context on demand exactly like a fresh one, and the
        # contributions below (draft hint, kernel claim) carry everything the
        # start needs.
        agent_type = raw.get("agent_type", "")
        resume_parts: List[str] = []

        try:
            draft_context = self._build_resume_draft_context(session_id)
        except Exception as exc:
            draft_context = None
            logger.warning(
                "SubagentStart: draft-resume lookup failed for session=%s: %s",
                session_id, exc,
            )
        if draft_context:
            logger.info(
                "SubagentStart: resumed draft surfaced for session=%s (agent=%s)",
                session_id, agent_type or "unknown",
            )
            resume_parts.append(draft_context)

        # v43 dispatch kernel, cache-miss lane: the born row is a DB fact that
        # outlives the 60s context cache, so a start that lost the cache race
        # can still claim its row and receive its kernel. A resume never
        # matches (its row is already claimed or was never born), so this is a
        # no-op there.
        kernel_context = self._maybe_claim_dispatch_kernel(raw)
        if kernel_context:
            resume_parts.append(kernel_context)

        if resume_parts:
            return ContextResult(
                context_injected=True,
                additional_context="\n\n".join(resume_parts),
                sections_provided=[],
            )

        logger.info(
            "SubagentStart: no cached context found for session=%s "
            "agent=%s (passthrough)",
            session_id, agent_type or "unknown",
        )
        return ContextResult(
            context_injected=False,
            additional_context=None,
            sections_provided=[],
        )

    # ------------------------------------------------------------------ #
    # P2: format_quality_response
    # ------------------------------------------------------------------ #

    def format_quality_response(self, result: QualityResult) -> HookResponse:
        """Format a QualityResult for CLI consumption.

        Stop events are informational -- exit code is always 0.
        """
        output: Dict[str, Any] = {
            "quality_sufficient": result.quality_sufficient,
            "score": result.score,
            "recommendation": result.recommendation,
        }

        if result.missing_elements:
            output["missing_elements"] = result.missing_elements

        return HookResponse(output=output, exit_code=0)

    # ------------------------------------------------------------------ #
    # P2: format_verification_response
    # ------------------------------------------------------------------ #

    def format_verification_response(self, result: VerificationResult) -> HookResponse:
        """Format a VerificationResult for CLI consumption.

        TaskCompleted events are informational -- exit code is always 0.
        """
        output: Dict[str, Any] = {
            "criteria_met": result.criteria_met,
            "block_completion": result.block_completion,
        }

        if result.verified_items:
            output["verified_items"] = result.verified_items
        if result.failed_items:
            output["failed_items"] = result.failed_items

        return HookResponse(output=output, exit_code=0)
