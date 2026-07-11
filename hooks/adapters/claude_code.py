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


def classify_stop_reason(stop_reason: Optional[str]) -> str:
    """Map a Claude Code ``stop_reason`` to its adapter-owned semantic class.

    ``"max_tokens"`` -> ``STOP_REASON_TRUNCATION``: the turn was cut off by
        the token budget, not chosen by the agent. A broken/incomplete
        contract under this reason is a salvage candidate (T11), not a hard
        violation.
    ``"end_turn"`` -> ``STOP_REASON_VIOLATION``: the agent had room to finish
        and stopped anyway. A broken/incomplete contract under this reason is
        a genuine violation.
    Anything else (``None``, empty, or an unrecognized reason such as
        ``"tool_use"``) -> ``STOP_REASON_UNKNOWN``: the conservative default.
        A caller that gates on this classification should treat "unknown"
        the same as a violation (fail closed) rather than assume a
        salvage-worthy truncation it cannot confirm.

    This function is the SOLE owner of the max_tokens/end_turn mapping
    (decision #5). ``gaia.contract.validator`` and ``gaia.contract.crosscheck``
    never import it and never branch on stop_reason themselves.
    """
    if stop_reason == _STOP_REASON_MAX_TOKENS:
        return STOP_REASON_TRUNCATION
    if stop_reason == _STOP_REASON_END_TURN:
        return STOP_REASON_VIOLATION
    return STOP_REASON_UNKNOWN


# ---------------------------------------------------------------------------
# M4 full-verdict contract gate (brief contract-as-managed-data-agent-contract
# -handoff-agnostico-por-cli, T16 / AC-9).
#
# The live SubagentStop gate historically enforced only 3 structural cases
# (Option B: missing block / missing agent_status / bad plan_status) and let
# EVERYTHING else exit 0 + a bare anomaly, never delivering the rich repair
# message. T16 replaces that with a FULL-VERDICT gate driven by the SINGLE
# portable core (gaia.contract.crosscheck.validate == form + cross-check), so
# the gate, the CLI validate-on-write, the finalize writer, and the fence
# fallback all agree on ONE verdict.
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
# agent_status, bad plan_status). A handoff that PARSES but is otherwise
# incomplete or drifted (missing evidence_report keys, missing next_action,
# malformed agent_id, missing consolidation_report) produced a CRITICAL
# response_contract_violation anomaly WITHOUT forcing repair -- the turn ended
# exit 0 and recovery fell to the orchestrator via SendMessage. Full-verdict
# closes that gap; a handoff built through the `gaia contract` CLI already
# passed this SAME core at finalize, so the correct-path agent is never
# rejected -- only a genuinely broken fence is.
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


def full_verdict_gate_enabled() -> bool:
    """Whether the M4 full-verdict gate is active (DEFAULT ON).

    Reads ``GAIA_CONTRACT_FULL_VERDICT_GATE``. Unset / empty / any value that is
    NOT an explicit falsy token -> True -> the full-verdict gate, driven by the
    SINGLE portable core (gaia.contract.crosscheck) that also backs the CLI
    validate-on-write, finalize, and the fence fallback. One of
    {"0","false","no","off"} (case-insensitive) -> False -> the legacy 3-case
    Option B gate, still fully supported as the one-env-var rollback path (T17).

    The default was flipped ON to close the SubagentStop enforcement gap: in
    3-case mode a handoff that PARSES (agent_status + valid plan_status present)
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


def _three_case_verdict(parsed_contract: Any, agent_type: str) -> ContractGateVerdict:
    """The legacy Option B gate: reject ONLY the 3 critical structural cases.

    Byte-identical to the pre-T16 inline gate so the ramp-OFF path preserves
    today's behavior exactly (AC-10). Produces NO anomalies -- in this mode the
    anomalies remain on the legacy validate_contract / validate_response_contract
    path, unchanged.
    """
    from modules.agents.contract_validator import _resolve_status
    from modules.agents.response_contract import VALID_PLAN_STATUSES

    if parsed_contract is None:
        reason = (
            "[CONTRACT REJECTED] No parseable agent_contract_handoff block found in agent response.\n"
            "The agent must end its response with a ```agent_contract_handoff``` fenced block "
            "whose body is VALID JSON (it is parsed with json.loads). A block written in YAML, "
            "with comments, trailing commas, or unquoted keys will fail to parse and is treated "
            "as missing.\n"
            "Reissue the response with a complete agent_contract_handoff block whose body is valid JSON."
        )
        return ContractGateVerdict(True, reason, (), GATE_MODE_THREE_CASE)

    agent_status = parsed_contract.get("agent_status")
    if not agent_status or not isinstance(agent_status, dict):
        reason = (
            "[CONTRACT REJECTED] agent_status block missing from agent_contract_handoff.\n"
            "The agent_contract_handoff block must include an agent_status object with "
            "plan_status, agent_id, pending_steps, and next_action."
        )
        return ContractGateVerdict(True, reason, (), GATE_MODE_THREE_CASE)

    normalized = _resolve_status(agent_status)
    raw_plan_status = agent_status.get("plan_status", "")
    if not normalized or normalized not in VALID_PLAN_STATUSES:
        valid_list = ", ".join(sorted(VALID_PLAN_STATUSES))
        reason = (
            f"[CONTRACT REJECTED] plan_status is missing or invalid: "
            f"'{raw_plan_status}'.\n"
            f"Valid statuses: {valid_list}.\n"
            f"Set plan_status to one of these values in agent_status."
        )
        return ContractGateVerdict(True, reason, (), GATE_MODE_THREE_CASE)

    return ContractGateVerdict(False, "", (), GATE_MODE_THREE_CASE)


def evaluate_contract_gate(
    parsed_contract: Any,
    *,
    agent_type: str = "unknown",
    stop_reason_classification: str = STOP_REASON_UNKNOWN,
    ramp_enabled: Optional[bool] = None,
    db_path: Optional[str] = None,
) -> ContractGateVerdict:
    """Evaluate the SubagentStop contract gate for one turn (T16 / AC-9).

    Args:
        parsed_contract: the parsed agent_contract_handoff envelope dict, or
            None when no parseable block was found.
        agent_type: the emitting agent (for anomaly messages).
        stop_reason_classification: the ALREADY-resolved T10 classification
            (STOP_REASON_TRUNCATION / _VIOLATION / _UNKNOWN). Read, not
            recomputed -- decides salvage-vs-violation.
        ramp_enabled: None -> read the ramp flag from the environment. When
            False, returns the legacy 3-case verdict. When True, returns the
            full-verdict verdict from the single portable core.
        db_path: optional gaia.db path for the layer-2 cross-check.

    Returns:
        ContractGateVerdict.
    """
    if ramp_enabled is None:
        ramp_enabled = full_verdict_gate_enabled()

    if not ramp_enabled:
        return _three_case_verdict(parsed_contract, agent_type)

    # Full-verdict: the SINGLE core (form + cross-check) is the SSOT verdict.
    from gaia.contract.crosscheck import validate as _core_validate

    _db = Path(db_path) if db_path else None
    result = _core_validate(parsed_contract, db_path=_db)
    if result.ok:
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


def _append_workspace_memory(context: str) -> str:
    """Append the curated workspace memory block to a subagent context string.

    Calls the same primitive the orchestrator uses at SessionStart --
    ``session_manifest.build_workspace_memory_block`` -- but scoped to the
    ``anchor`` section only. A dispatched subagent receives just
    ``## Memory — About you / What I know`` (durable, identity-level anchors),
    NOT ``## Memory — For this session`` (carry_forward) nor
    ``## Memory — Open threads`` (thread/open): those are session-scoped state
    that belongs to the orchestrator's turn, not to a one-shot subagent. The
    orchestrator's own SessionStart path calls the primitive with no ``sections``
    argument and still receives all three sections -- this cut is subagent-only.
    Joins with a blank-line separator when context is non-empty. Returns the
    original context unchanged on any error (fail-safe: dispatch must never
    fail because memory injection misbehaved).
    """
    try:
        from modules.session.session_manifest import build_workspace_memory_block
        block = build_workspace_memory_block(sections=["anchor"])
        if not block:
            return context
        separator = "\n\n" if context else ""
        return context + separator + block
    except Exception as exc:
        logger.debug("_append_workspace_memory failed (non-fatal): %s", exc)
        return context


class ClaudeCodeAdapter(HookAdapter):
    """Concrete adapter for Claude Code v2.1+ hook protocol.

    Claude Code sends JSON on stdin with these top-level fields:
        - hook_event_name: str  (e.g. "PreToolUse", "PostToolUse", "SubagentStop")
        - session_id: str
        - tool_name: str        (PreToolUse / PostToolUse)
        - tool_input: dict      (PreToolUse / PostToolUse)
        - tool_response: dict    (PostToolUse only)
        - agent_type: str       (SubagentStop only)
        - agent_id: str         (SubagentStop only)
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
        output = tool_response.get("output", "")
        exit_code = tool_response.get("exit_code", 0)

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
        from modules.tools.task_validator import TaskValidator, AVAILABLE_AGENTS, META_AGENTS
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
                hooks_dir = Path(__file__).parent.parent
                project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
                return self._adapt_task(
                    tool_name, tool_input, project_agents, hooks_dir,
                    session_id=event.session_id,
                )
            elif tool_name.lower() == "sendmessage":
                return self._adapt_send_message(
                    tool_name, tool_input, session_id=event.session_id,
                )
            elif tool_name.lower() in ("write", "edit"):
                is_subagent = bool(hook_data and hook_data.get("agent_id"))
                session_id = (hook_data or {}).get("session_id", "")
                return self._adapt_write_edit(
                    tool_name, tool_input,
                    session_id=session_id,
                    is_subagent=is_subagent,
                )
            else:
                # Other tools pass through
                return HookResponse(output={}, exit_code=0)

        except Exception as e:
            logger.error("Unexpected error in adapt_pre_tool_use: %s", e, exc_info=True)
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

        validator = BashValidator()
        result = validator.validate(
            command, is_subagent=is_subagent, session_id=session_id,
            agent_type=agent_type,
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
        # T3 approval grant, carry that approval_id forward so PostToolUse can
        # append an EXECUTED/FAILED event to the approval_events chain (the grant
        # is consumed here at PreToolUse and flips to CONSUMED, so PostToolUse
        # cannot re-discover it via check_approval_grant).
        effective_command = result.modified_input.get("command", command) if result.modified_input else command
        state = create_pre_hook_state(
            tool_name=tool_name,
            command=effective_command,
            tier=str(result.tier),
            allowed=True,
            consumed_approval_id=result.consumed_approval_id,
        )
        save_hook_state(state)

        if result.modified_input:
            logger.info("MODIFIED: %s -> stripped footer - tier=%s", command[:80], result.tier)
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": result.reason,
                    "updatedInput": result.modified_input,
                }
            }
            return HookResponse(output=output, exit_code=0)

        logger.info("ALLOWED: %s - tier=%s", command[:100], result.tier)
        return HookResponse(output={}, exit_code=0)

    def _adapt_task(
        self,
        tool_name: str,
        parameters: dict,
        project_agents: list,
        hooks_dir: Path,
        session_id: str = "",
    ) -> HookResponse:
        """Handle Task/Agent tool validation within the adapter.

        Builds project context and caches it for SubagentStart to forward.
        PreToolUse no longer returns additionalContext directly -- that would
        inject it into the orchestrator instead of the subagent.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.tools.task_validator import TaskValidator
        from modules.context.context_injector import build_project_context
        from modules.session.session_event_injector import build_session_events

        context_text, _telemetry = build_project_context(parameters, project_agents, hooks_dir)
        events_text = build_session_events(parameters, project_agents)

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

        # Cache context for SubagentStart to pick up and forward to the subagent.
        # PreToolUse:Agent additionalContext goes to the orchestrator (wrong target).
        additional = "\n".join(filter(None, [context_text, events_text]))

        # Fallback: if build_project_context returned None because the
        # orchestrator already embedded context in the prompt (dedup guard),
        # extract the embedded context so SubagentStart can still inject it
        # via additionalContext.
        if not additional:
            prompt = parameters.get("prompt", "")
            marker = "# Project Context"
            if marker in prompt:
                # Extract everything from the marker onwards as context.
                # The orchestrator copied its own injected context into the
                # Agent tool prompt; we forward it to SubagentStart instead.
                idx = prompt.index(marker)
                additional = prompt[idx:]
                logger.info(
                    "Extracted embedded context from prompt for caching "
                    "(len=%d, agent=%s)",
                    len(additional), result.agent_name,
                )

        # Append curated workspace memory (atoms, decisions, negatives) so
        # subagents receive the same curated memory sections the orchestrator
        # gets at SessionStart. Reuses session_manifest.build_workspace_memory_block
        # as the single source of truth for the primitive. Fail-safe.
        additional = _append_workspace_memory(additional)

        if additional:
            effective_session_id = session_id or "unknown"
            agent_type = result.agent_name or "unknown"
            self._cache_context_for_subagent(effective_session_id, agent_type, additional)
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
        the ``^a[0-9a-f]{5,}$`` format shared with AC-1's form validator),
        so recording session_id -> agent_id here is enough for
        ``adapt_subagent_start``'s resume path to recover the resumed
        agent's own in-progress draft -- SubagentStart's payload carries
        only session_id + agent_type, never the resumed agent_id. Core/CLI
        stay agnostic (decision #1): only this CC-specific bridge, not
        gaia.contract.*, reads SendMessage's ``to`` field or a session_id.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state

        agent_id = parameters.get("to", "")
        message = parameters.get("message", "")

        # Validate agentId format
        if not agent_id or not re.match(r'^a[0-9a-f]{5,}$', agent_id):
            logger.warning("BLOCKED SendMessage: Invalid agentId format '%s'", agent_id)
            msg = (
                f"[ERROR] Invalid agent ID format: '{agent_id}'\n\n"
                "Agent ID should be 'a' followed by hex characters.\n"
                "Example: a12345f or a51a0cbbf6afb831d\n\n"
                "The agent ID is returned at the end of agent responses.\n"
                "Look for: 'agentId: a...' in the previous agent output."
            )
            return HookResponse(output=msg, exit_code=2)

        if not message or not message.strip():
            logger.warning("BLOCKED SendMessage: Missing message for agent %s", agent_id)
            msg = (
                "[ERROR] SendMessage requires a message\n\n"
                "When resuming an agent, you must provide a message:\n\n"
                "SendMessage(\n"
                "    to=\"a12345\",\n"
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
    ) -> HookResponse:
        """Handle Write and Edit tool path protection.

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
        """
        from modules.security.approval_grants import (
            check_approval_grant_for_file,
            find_pending_for_file,
            generate_nonce,
            write_pending_approval_for_file,
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
        output = tool_result_data.output
        duration = raw_tool_response.get("duration_ms", 0) / 1000.0
        success = tool_result_data.exit_code == 0

        # ------------------------------------------------------------- #
        # AskUserQuestion: check if user approved a pending T3 grant
        # ------------------------------------------------------------- #
        if tool_name == "AskUserQuestion":
            self._handle_ask_user_question_result(hook_data)
            return HookResponse(output={}, exit_code=0)

        try:
            pre_state = get_hook_state()
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

            # Confirm unconfirmed T3 grants after successful Bash execution.
            # Grants are consumed later at SubagentStop, not here -- the grant
            # lives for the full subagent session so retries work naturally.
            if tool_name == "Bash" and success:
                command = parameters.get("command", "")
                session_id = hook_data.get("session_id", "")
                if command:
                    grant = check_approval_grant(command, session_id=session_id)
                    if grant is not None and not grant.confirmed:
                        confirm_grant(command, session_id=session_id)
                        logger.info(
                            "T3 grant confirmed (will be consumed at SubagentStop): %s", command[:80],
                        )

            # Close the audit-log cycle for an APPROVED T3 command that just ran.
            # PreToolUse stashed the consumed grant's approval_id in HookState
            # when it matched (and consumed) the grant; append EXECUTED on a clean
            # exit, FAILED otherwise. This continues the approval_events hash chain
            # via the canonical store.record_event() helper -- the only authorized
            # writer for the chain (it routes through chain.insert_event(), which
            # links prev_hash -> this_hash before INSERT).
            if tool_name == "Bash":
                consumed_approval_id = (
                    pre_state.metadata.get("consumed_approval_id") if pre_state else None
                )
                if consumed_approval_id:
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

            clear_hook_state()
            logger.debug("Post-hook completed for %s", tool_name)

        except Exception as e:
            logger.error("Error in adapt_post_tool_use: %s", e, exc_info=True)

        return HookResponse(output={}, exit_code=0)

    def _record_t3_outcome_event(
        self,
        approval_id: str,
        *,
        command: str,
        success: bool,
        exit_code: int,
        session_id: str = "",
    ) -> None:
        """Append an EXECUTED or FAILED event for an approved T3 command.

        Closes the audit-log cycle: once a command runs under a consumed grant,
        the approval_events chain records whether it succeeded (EXECUTED) or
        failed (FAILED). Writes through gaia.approvals.store.record_event(), the
        canonical chain writer -- never a raw INSERT -- so prev_hash -> this_hash
        linkage is preserved and validate_chain() stays intact end to end.

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
            extract_commands_from_evidence,
            parse_contract,
            requires_consolidation_report,
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
        from modules.agents.transcript_reader import read_transcript
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

            # Resolve canonical plan_status from the agent_contract_handoff envelope.
            from modules.agents.contract_validator import _resolve_status
            _resolved_plan_status = (
                _resolve_status(parsed_contract.get("agent_status") or {})
                if isinstance(parsed_contract, dict) else ""
            )

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
            try:
                _gate = evaluate_contract_gate(
                    parsed_contract,
                    agent_type=agent_type,
                    stop_reason_classification=_stop_reason_classification,
                    ramp_enabled=_full_verdict,
                    db_path=task_info.get("db_path"),
                )
            except Exception:
                logger.exception(
                    "Contract gate raised for %s (mode=%s) -- falling back to a "
                    "non-rejecting verdict so SubagentStop cleanup still runs.",
                    agent_type, _gate_mode,
                )
                _gate = ContractGateVerdict(False, "", (), _gate_mode)

            # Preserve pending files that the agent's final contract still
            # references via APPROVAL_REQUEST. Cleanup must not destroy
            # approvals the user is being asked to act on.
            preserved_nonces: set = set()
            if isinstance(parsed_contract, dict):
                _approval_req = parsed_contract.get("approval_request") or {}
                _nonce = _approval_req.get("approval_id") if isinstance(_approval_req, dict) else None
                if _resolved_plan_status == "APPROVAL_REQUEST" and _nonce:
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

            commands_executed = extract_commands_from_evidence(agent_output)

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
                anchors = load_anchors(session_id, agent_type)
                if anchors and transcript_path:
                    tool_calls = extract_tool_calls_from_transcript(transcript_path)
                    anchor_hits = compute_anchor_hits(tool_calls, anchors)
                    logger.info(
                        "Anchor hits for %s: %d/%d (%.0f%%)",
                        agent_type,
                        anchor_hits.get("hits", 0),
                        anchor_hits.get("total_checked", 0),
                        anchor_hits.get("hit_rate", 0) * 100,
                    )
                    cleanup_anchors(session_id, agent_type)
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
                consolidation_required=requires_consolidation_report(task_info),
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

            if anomalies:
                logger.warning("%d anomalies detected in workflow", len(anomalies))
                signal_gaia_analysis(anomalies, workflow_metrics)

            workflow_metrics["anomalies_detected"] = len(anomalies)
            workflow_metrics["anomaly_types"] = [a.get("type", "") for a in anomalies]

            episode_id = write_episode(
                workflow_metrics,
                anomalies=anomalies if anomalies else None,
                commands_executed=commands_executed,
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
                )

            # ----------------------------------------------------------
            # BUG C fix: Persist handoff row to DB (M4 / T4.2).
            # Wrapped in try/except per T4.2 spec -- DB failures must NOT
            # crash the hook.
            # ----------------------------------------------------------
            try:
                from modules.agents.handoff_persister import persist_handoff
                persist_handoff(
                    parsed_contract=parsed_contract,
                    agent_output=agent_output,
                    task_info=task_info,
                    session_id=session_id,
                )
            except Exception as _handoff_exc:
                logger.warning(
                    "M4: handoff persistence call failed (non-blocking): %s",
                    _handoff_exc,
                )

            # Write AGENT_COMPLETE event (non-blocking)
            try:
                from modules.events.event_writer import EventWriter, AGENT_COMPLETE
                _plan = _resolved_plan_status
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

            contract_attempts = 0
            if not response_contract.valid:
                try:
                    repair_data = response_contract.to_dict()
                    contract_attempts = int(repair_data.get("repair_attempts", 0))
                except Exception:
                    contract_attempts = 0

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
            # Extract plan_status for downstream checks (canonical field
            # resolved earlier via _resolve_status).
            # ----------------------------------------------------------
            _plan_status = _resolved_plan_status

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
                if _plan_status and _agent_id:
                    transition_result = track_transition(
                        _agent_id,
                        _plan_status,
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
                approval_check = validate_approval_request(parsed_contract, _plan_status)
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
                if declared_skills and agent_output:
                    skill_check = verify_skill_injection(
                        agent_type, agent_output, declared_skills,
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
            }

            if _salvage:
                result["truncation_salvaged"] = True
                result["salvage_contract_id"] = _salvage.get("contract_id")
                # Resume hint rendered by view.py's single renderer (T14) --
                # never re-inlined here -- so a resume continues the SAME
                # salvaged draft via --draft-id instead of re-emitting the block.
                result["salvage_resume_hint"] = _salvage.get("resume_hint")

            if contract_rejected:
                result["contract_rejected"] = True
                result["contract_rejection_reason"] = contract_rejection_reason
                logger.warning(
                    "Contract rejected for %s: %s",
                    agent_type, contract_rejection_reason.split("\n")[0],
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

    def _salvage_truncated_draft(
        self,
        *,
        parsed_contract,
        task_info: dict,
        session_id: str,
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
        fabricated evidence. task_status mirrors T9: the draft's own
        plan_status when it is a valid terminal value, else the honest
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
            from gaia.state import VALID_PLAN_STATUSES
            from gaia.store import writer as _writer
        except Exception as exc:
            logger.debug("T11 salvage: core import failed (non-fatal): %s", exc)
            return None

        # Resolve the minted agent_id drafts are addressed by -- prefer the
        # authoritative agent_status.agent_id from the envelope (the exact value
        # the CLI minted the draft with), else task_info. Mirrors
        # handoff_persister._resolve_minted_agent_id so salvage and the T9
        # backstop resolve the SAME draft (hence the SAME contract_id).
        minted_agent_id = None
        if isinstance(parsed_contract, dict):
            agent_status = parsed_contract.get("agent_status")
            if isinstance(agent_status, dict) and agent_status.get("agent_id"):
                minted_agent_id = str(agent_status["agent_id"])
        if not minted_agent_id:
            minted_agent_id = task_info.get("agent_id")
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

            salvaged = dict(envelope)
            agent_status = salvaged.get("agent_status")
            plan_status = (
                agent_status.get("plan_status")
                if isinstance(agent_status, dict)
                else None
            )
            task_status = (
                plan_status if plan_status in VALID_PLAN_STATUSES else "IN_PROGRESS"
            )
            salvaged["degraded"] = True
            salvaged["auto_captured"] = True
            salvaged["salvaged"] = "truncation"

            outcome = _writer.finalize_agent_contract_handoff(
                contract_id=draft_id,
                agent_id=str(agent_id_col),
                workspace=workspace,
                task_status=task_status,
                raw_handoff_json=json.dumps(salvaged),
                session_id=session_id,
                brief_id=None,
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
    ) -> Path:
        """Write built context to a cache file for SubagentStart consumption.

        Returns the path to the cache file.
        """
        self.CONTEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        cache_file = self.CONTEXT_CACHE_DIR / f"{session_id}-{timestamp}.json"
        payload = {
            "context": context,
            "agent_type": agent_type,
            "session_id": session_id,
            "created_at": time.time(),
        }
        cache_file.write_text(json.dumps(payload))
        logger.debug("Context cache written: %s", cache_file)
        return cache_file

    def _read_cached_context(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Read and consume the most recent cached context for a session.

        Finds the newest cache file matching the session_id, reads it,
        deletes it (one-shot consumption), and cleans up stale entries.

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
        result = None

        for cache_file in candidates:
            try:
                data = json.loads(cache_file.read_text())
                age = now - data.get("created_at", 0)

                if age > self.CONTEXT_CACHE_TTL_SECONDS:
                    # Stale entry -- clean up
                    cache_file.unlink(missing_ok=True)
                    logger.debug("Cleaned stale context cache: %s (age=%.1fs)", cache_file.name, age)
                    continue

                # Found a valid entry -- consume it
                result = data
                cache_file.unlink(missing_ok=True)
                logger.debug("Consumed context cache: %s (age=%.1fs)", cache_file.name, age)
                break

            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read context cache %s: %s", cache_file, exc)
                cache_file.unlink(missing_ok=True)
                continue

        # Clean up any remaining stale files (background hygiene)
        self._cleanup_stale_cache(now)

        return result

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
        draft_id = resolve_draft_id(explicit=None, agent_id=agent_id)
        if not draft_id:
            return None
        envelope = load_draft(draft_id)
        if not envelope:
            return None
        return render_resume_hint(draft_id, envelope)

    # ------------------------------------------------------------------ #
    # P2: adapt_subagent_start
    # ------------------------------------------------------------------ #

    def adapt_subagent_start(self, raw: dict) -> ContextResult:
        """Parse SubagentStart event and forward cached context to the subagent.

        Three contributions, all optional, joined when present:
        1. Cache hit (normal start via Task/Agent tool): PreToolUse:Agent
           caches context, this method reads and forwards it verbatim (no
           draft-resume contribution applies here -- a freshly dispatched
           agent has no prior draft under its brand-new agent_id).
        2. Cache miss (resume via SendMessage): No PreToolUse:Agent fires,
           so no cache exists. If agent_type is present in the payload and
           is a known project agent, rebuild project context on-demand.
        3. Cache miss + resume-mapping hit (T6, AC-18/AC-20): the CC session
           resuming this agent was recorded by
           ``_adapt_send_message``/``_cache_resume_mapping``; if that
           session_id (or, failing that, the most recent resume) maps to an
           agent_id with a live ``gaia.contract.drafts`` draft, surface a
           minimal summary of it so the resumed agent continues its own
           draft instead of re-emitting the contract block.
        """
        session_id = raw.get("session_id", "")

        cached = self._read_cached_context(session_id)
        if cached:
            logger.info(
                "SubagentStart: forwarding cached context for agent=%s (session=%s)",
                cached.get("agent_type", "unknown"),
                session_id,
            )
            return ContextResult(
                context_injected=True,
                additional_context=cached["context"],
                sections_provided=[],
            )

        # Resume path: SendMessage skips PreToolUse:Agent so no cache is
        # written. If agent_type is present in the payload, rebuild context
        # on-demand so the resumed agent has its project context and tools.
        agent_type = raw.get("agent_type", "")
        resume_parts: List[str] = []
        if agent_type:
            try:
                from modules.context.context_injector import build_project_context
                from modules.session.session_event_injector import build_session_events
                from modules.tools.task_validator import AVAILABLE_AGENTS, META_AGENTS

                project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]

                if agent_type in project_agents:
                    hooks_dir = Path(__file__).parent.parent
                    task_description = raw.get("task_description", "")
                    parameters = {
                        "subagent_type": agent_type,
                        "prompt": task_description or f"resume {agent_type}",
                    }

                    context_text, _telemetry = build_project_context(
                        parameters, project_agents, hooks_dir,
                    )
                    events_text = build_session_events(parameters, project_agents)
                    additional = "\n".join(filter(None, [context_text, events_text]))

                    if additional:
                        logger.info(
                            "SubagentStart: rebuilt context on resume for "
                            "agent=%s (session=%s)",
                            agent_type, session_id,
                        )
                        resume_parts.append(additional)
            except Exception as exc:
                logger.warning(
                    "SubagentStart: resume context rebuild failed for "
                    "agent=%s: %s", agent_type, exc,
                )

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
