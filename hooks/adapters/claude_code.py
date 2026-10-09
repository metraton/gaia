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
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional

from gaia.paths import tmp_dir

from .base import HookAdapter
# The shared tool policy's helpers keep their historical import path here.
from .tool_policy import (  # noqa: F401
    DISPATCH_BINDING_REJECTED_EVENT,
    GAIA_DISPATCH_AGENT_ENV,
    PolicyVerdict,
    ToolPolicy,
    _is_anomalous_dispatch_binding_rejection,
    _record_dispatch_binding_rejection,
    build_dispatch_identity_command,
    dispatch_tmpdir,
    failure_verdict,
)
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
from . import subagent_stop_core
# The SubagentStop gate's names keep their historical import path here.
from .subagent_stop_core import (  # noqa: F401
    CONTRACT_REJECTED_EVENT,
    GATE_MODE_FULL_VERDICT,
    GATE_MODE_THREE_CASE,
    GATE_RAMP_ENV_VAR,
    GATE_SOURCE_ROW,
    GATE_SOURCE_ROW_MISSING,
    GATE_SOURCE_ROW_UNFINALIZED,
    STOP_REASON_TRUNCATION,
    STOP_REASON_UNKNOWN,
    STOP_REASON_VIOLATION,
    ContractGateVerdict,
    _blind_verification_required,
    _three_case_verdict,
    evaluate_contract_gate,
    full_verdict_gate_enabled,
    resolve_subagent_stop_gate,
)

logger = logging.getLogger(__name__)


# Requester identity of a main-session call, whose event carries no agent. The
# approval core never defaults an identity, so the adapter names it explicitly.
PRIMARY_AGENT = "claude-code-primary"

# Host name recorded on an approval's chain when this host refuses a signed call.
HOST_NAME = "claude_code"

# The PostToolUseFailure ``error`` Claude Code sends when its permission layer,
# or the user at its prompt, refused a call: the command never ran, so the
# signature it matched must not be spent. Anchored at the start, where a real
# exit reads "Exit code N", so a command's own output never matches.
_HOST_REFUSAL = re.compile(
    r"(?:Error:\s*)?(?:Permission to use \S+ with command .* has been denied"
    r"|The user doesn't want to proceed with this tool use)",
    re.DOTALL,
)

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


class ClaudeCodeAdapter(ToolPolicy, HookAdapter):
    """Concrete adapter for Claude Code v2.1+ hook protocol.

    Claude Code sends JSON on stdin with these top-level fields:
        - hook_event_name: str  (e.g. "PreToolUse", "PostToolUse", "SubagentStop")
        - session_id: str
        - tool_name: str        (PreToolUse / PostToolUse)
        - tool_input: dict      (PreToolUse / PostToolUse)
        - tool_response: dict    (PostToolUse only)
        - agent_type: str       (PreToolUse for subagent dispatches; also SubagentStop;
                                 "gaia:<name>" under a plugin install, canonicalized
                                 to "<name>" by parse_event)
        - agent_id: str         (PreToolUse for subagent dispatches; also SubagentStop)
        - agent_transcript_path: str  (SubagentStop only)
        - last_assistant_message: str (SubagentStop only)
        - cwd: str              (SubagentStop only)

    Responses use hookSpecificOutput with permissionDecision for PreToolUse.

    The tool-call policy is the shared ``ToolPolicy``, whose verdicts this
    adapter formats. It is inherited rather than held so the policy's seams
    (``_maybe_birth_dispatched_row``, ``_adapt_bash``'s validator path) stay
    reachable on this class, where callers and tests address them.
    """

    PRIMARY_AGENT_TYPE = PRIMARY_AGENT
    HOST_NAME = HOST_NAME

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

        # A plugin install reports agent_type as "gaia:<name>"; every consumer
        # of the payload compares the bare name, so it is canonicalized here,
        # once, before any of them reads it.
        if "agent_type" in raw:
            from gaia.agent_identity import canonical_agent_name

            raw["agent_type"] = canonical_agent_name(raw["agent_type"])

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
        ran = True

        if raw.get("hook_event_name") == HookEventType.POST_TOOL_USE_FAILURE.value:
            # A failed call has no tool_response; its outcome is the top-level
            # ``error`` ("Exit code 3" for a Bash exit, captured on 2.1.273).
            output = str(raw.get("error") or "")
            failed = True
            exit_code = self._extract_exit_code_from_result(output)
            ran = _HOST_REFUSAL.match(output) is None
        elif isinstance(tool_response, str):
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
            ran=ran,
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
        """``subagent_stop_core.gaia_agent_roster``."""
        return subagent_stop_core.gaia_agent_roster()

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
          AskUserQuestion prompt, and _handle_ask_user_question_result activates the
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

    def adapt_pre_tool_use(
        self, event: HookEvent, *, _dispatch_identity_in_env: bool = False,
    ) -> HookResponse:
        """Run the shared tool policy, then Claude Code's own tools, as a hook response.

        SendMessage and AskUserQuestion exist only in this host, so they are
        decided here once the shared policy (delegate-mode gate, grant cleanup,
        input shape) has left the call to the host.
        """
        verdict = self.pre_tool_verdict(
            event, dispatch_identity_in_env=_dispatch_identity_in_env,
        )
        tool_name = event.payload.get("tool_name") or ""
        if verdict.is_silent and isinstance(tool_name, str):
            tool_input = event.payload.get("tool_input", {})
            try:
                if tool_name.lower() == "sendmessage":
                    return self._adapt_send_message(
                        tool_name, tool_input, session_id=event.session_id,
                    )
                if tool_name == "AskUserQuestion":
                    return self._adapt_ask_user_question(tool_input, hook_data=event.payload)
            except Exception as e:
                verdict = failure_verdict(e)
        return self._format_pre_tool_verdict(verdict)

    def _format_pre_tool_verdict(self, verdict: PolicyVerdict) -> HookResponse:
        """Translate a policy verdict into Claude Code's PreToolUse response.

        A refusal exits 2, the only exit code this host treats as blocking
        (exit 1 is a non-blocking "HOOK ERROR" and the call would run
        unvalidated).
        """
        if verdict.refusal is not None:
            return HookResponse(output=verdict.refusal, exit_code=2)
        if verdict.consent is not None:
            return replace(
                self.request_consent(verdict.consent), approval_id=verdict.approval_id,
            )
        if verdict.decision is None:
            return HookResponse(output={}, exit_code=0)
        specific: Dict[str, Any] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": verdict.decision,
            "permissionDecisionReason": verdict.reason,
        }
        if verdict.updated_input is not None:
            specific["updatedInput"] = verdict.updated_input
        if verdict.context:
            specific["additionalContext"] = verdict.context
        return HookResponse(
            output={_HOOK_SPECIFIC_OUTPUT: specific}, exit_code=0,
            approval_id=verdict.approval_id,
        )

    def _deliver_subagent_context(
        self, session_id: str, agent_type: str, context: str, task_description: str,
    ) -> None:
        """Cache the events digest for this dispatch's SubagentStart to inject."""
        self._cache_context_for_subagent(
            session_id, agent_type, context, task_description=task_description,
        )
        logger.info(
            "Cached context for SubagentStart: agent=%s, session=%s",
            agent_type, session_id,
        )

    def _adapt_bash(
        self,
        tool_name: str,
        parameters: dict,
        hook_data: dict | None = None,
        *,
        _dispatch_identity_in_env: bool = False,
    ) -> HookResponse:
        """Decide one Bash call through the shared policy, as a hook response."""
        return self._format_pre_tool_verdict(self._bash_verdict(
            tool_name, parameters, hook_data=hook_data,
            dispatch_identity_in_env=_dispatch_identity_in_env,
        ))

    def _adapt_task(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str = "",
        hook_data: Optional[dict] = None,
    ) -> HookResponse:
        """Decide one Task/Agent dispatch through the shared policy, as a hook response."""
        return self._format_pre_tool_verdict(self._task_verdict(
            tool_name, parameters, session_id=session_id, hook_data=hook_data,
        ))

    def _adapt_write_edit(
        self,
        tool_name: str,
        parameters: dict,
        session_id: str = "",
        is_subagent: bool = False,
        agent_id: str = "",
        agent_type: str = "",
    ) -> HookResponse:
        """Decide one Write/Edit call through the shared policy, as a hook response.

        A main-session write to a protected path is asked inline through this
        host's native dialog (``request_consent``). The reminder a subagent
        write may carry travels in ``additionalContext``, not
        ``permissionDecisionReason``: with ``permissionDecision: "allow"`` this
        host surfaces the reason only in logs, never to the model
        (``code.claude.com/docs/en/hooks.md``, "PreToolUse decision control").
        """
        return self._format_pre_tool_verdict(self._write_edit_verdict(
            tool_name, parameters, session_id=session_id,
            is_subagent=is_subagent, agent_id=agent_id, agent_type=agent_type,
        ))

    @staticmethod
    def _resolve_dispatch_row(
        *,
        session_id: str,
        agent_type: str,
        task_info: dict,
        parsed_contract,
        db_path: Optional[Path] = None,
    ) -> Optional[dict]:
        """``subagent_stop_core.resolve_dispatch_row``."""
        return subagent_stop_core.resolve_dispatch_row(
            session_id=session_id,
            agent_type=agent_type,
            task_info=task_info,
            parsed_contract=parsed_contract,
            db_path=db_path,
        )

    @staticmethod
    def _adapt_ask_user_question(tool_input: dict, *, hook_data: dict) -> HookResponse:
        """Record the signatures of a question Gaia built, or deny one it did not build.

        A question without a signature's shape is left alone. One with it must
        be, byte for byte, the objects ``gaia approvals question`` printed:
        each command's question carries its signature in its own text (D33),
        so each position is recorded as shown under this tool_use_id and the
        hook returns no decision, which could let the question be skipped.
        """
        from gaia.approvals import core, surface

        questions = tool_input.get("questions")
        if not isinstance(questions, list) or not any(
            isinstance(question, dict) and surface.is_signature_question(question)
            for question in questions
        ):
            return HookResponse(output={}, exit_code=0)

        def deny(reason: str) -> HookResponse:
            return HookResponse(
                output={
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Gaia did not show this approval question: {reason}. Ask with "
                            "the exact object `gaia approvals question <approval_id> ...` "
                            "prints, unchanged, and print nothing about the signature."
                        ),
                    }
                },
                exit_code=0,
            )

        tool_use_id = str(hook_data.get("tool_use_id") or "")
        if not tool_use_id:
            return deny("the host event carries no tool_use_id to bind the answers to")
        try:
            surfaces = core.match_question_batch(questions)
        except core.SealError as exc:
            return deny(str(exc))
        for position, (approval_id, _, _) in enumerate(surface.slots(surfaces)):
            core.record_presentation(
                approval_id,
                native_ref=tool_use_id,
                session_id=str(hook_data.get("session_id") or ""),
                agent_id=str(hook_data.get("agent_id") or PRIMARY_AGENT),
                position=position,
            )
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

    # ------------------------------------------------------------------ #
    # adapt_post_tool_use: full post-tool-use lifecycle
    # ------------------------------------------------------------------ #

    def adapt_post_tool_use(self, event: HookEvent) -> HookResponse:
        """Record the call through the shared policy, or decide an AskUserQuestion answer.

        The call's outcome is read by this host's ``parse_post_tool_use``. A
        Task/Agent contract summary reaches the orchestrator as
        ``additionalContext`` next to the tool result.
        """
        tool_result = self.parse_post_tool_use(event.payload)
        if tool_result.tool_name == "AskUserQuestion":
            return self._handle_ask_user_question_result(event.payload)
        verdict = self.post_tool_verdict(event, tool_result)
        if verdict.context:
            return HookResponse(
                output={
                    _HOOK_SPECIFIC_OUTPUT: {
                        "hookEventName": "PostToolUse",
                        "additionalContext": verdict.context,
                    }
                },
                exit_code=0,
            )
        return HookResponse(output={}, exit_code=0)

    @staticmethod
    def _extract_exit_code_from_result(error: str) -> int:
        """Return the first ``Exit code N`` in a host failure text, or 1 when it names none."""
        match = re.search(r"[Ee]xit code (\d+)", error)
        return int(match.group(1)) if match else 1

    @staticmethod
    def _handle_ask_user_question_result(hook_data: Dict[str, Any]) -> HookResponse:
        """Decide each signature shown in this call from the labels chosen at its positions.

        Only signatures PreToolUse recorded as shown under this tool_use_id are
        decided, each by the answers to its own questions, one per command
        (D33): ``Approve``, ``Reject`` and ``Details`` are the option keys, and
        any other text (the Other row, a dismissal) or no answer counts as no
        choice. ``core.signature_decision`` folds them into one decision. A
        label is never searched for an approval id. An Approve that activates
        nothing is recorded (``decision_audit``) so a lost signature stays
        findable. Details asks the model to present that signature again with
        ``--details``, whose questions carry each command's Details text.
        """
        from gaia.approvals import core, surface

        tool_use_id = str(hook_data.get("tool_use_id") or "")
        try:
            shown = core.presented(tool_use_id) if tool_use_id else []
        except Exception as exc:
            logger.error("AskUserQuestion: cannot read its presentations: %s", exc, exc_info=True)
            return HookResponse(output={}, exit_code=0)
        if not shown:
            return HookResponse(output={}, exit_code=0)

        tool_response = hook_data.get("tool_response")
        tool_input = hook_data.get("tool_input")
        response = tool_response if isinstance(tool_response, dict) else {}
        asked = tool_input if isinstance(tool_input, dict) else {}
        questions = response.get("questions") or asked.get("questions") or []
        answers = response.get("answers") or asked.get("answers") or {}
        session_id = str(hook_data.get("session_id") or "")

        option_keys: Dict[int, Optional[str]] = {}
        chosen: Dict[str, List[Any]] = {}
        for position, approval_id in shown:
            question = questions[position] if position < len(questions) else {}
            answer = answers.get(question.get("question")) if isinstance(question, dict) else None
            option_keys[position] = surface.OPTION_KEYS.get(answer)
            chosen.setdefault(approval_id, []).append(answer)
        try:
            decided = core.decide_signatures(
                native_ref=tool_use_id, session_id=session_id, option_keys=option_keys,
            )
        except Exception as exc:
            logger.error("AskUserQuestion: decision failed: %s", exc, exc_info=True)
            return HookResponse(output={}, exit_code=0)

        details: List[str] = []
        for approval_id, option_key, result in decided:
            logger.info(
                "AskUserQuestion decision: approval_id=%s decision=%s status=%s reason=%s",
                approval_id, option_key, result.status, result.reason,
            )
            answer = chosen.get(approval_id, [])
            if option_key == "approve" and result.status != "activated":
                from gaia.approvals.decision_audit import (
                    LANE_CLAUDE_CODE_QUESTION,
                    REASON_ACTIVATION_FAILED,
                    record_decision_not_activated,
                )

                record_decision_not_activated(
                    reason=REASON_ACTIVATION_FAILED,
                    lane=LANE_CLAUDE_CODE_QUESTION,
                    session_id=session_id,
                    approval_id=approval_id,
                    decision_values=answer,
                    detail=result.reason,
                )
            if option_key == "details":
                details.append(approval_id)

        if not details:
            return HookResponse(output={}, exit_code=0)
        return HookResponse(
            output={
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": (
                        "The user chose Details. Ask again with "
                        f"`gaia approvals question --details {' '.join(details)}` and pass "
                        "its output unchanged to AskUserQuestion: Gaia shows the Details "
                        "block when that question opens. Print nothing about the "
                        "signature yourself."
                    ),
                },
            },
            exit_code=0,
        )

    # ------------------------------------------------------------------ #
    # adapt_subagent_stop: full subagent-stop lifecycle
    # ------------------------------------------------------------------ #

    def adapt_subagent_stop(self, event: HookEvent) -> HookResponse:
        """Close a subagent turn through the host-neutral core and format its
        outcome as Claude Code's SubagentStop response.

        The user-facing text travels on ``systemMessage`` beside a
        ``hookSpecificOutput`` carrying only the event echo: Claude Code hands
        ``additionalContext`` back to the subagent and treats ``decision:
        block`` as a retry, and either would resume the turn being closed --
        measured against the live harness on the circuit's cut.
        """
        # The lookups go through this adapter's own methods so that replacing
        # one on the adapter (callers and tests address them there) reaches
        # the close.
        host = subagent_stop_core.SubagentStopHost(
            agent_roster=self._get_gaia_agent_names,
            classify_stop_reason=classify_stop_reason,
            resume_map_dir=self.RESUME_MAP_CACHE_DIR,
            resolve_dispatch_row=self._resolve_dispatch_row,
            reconstruct_contract=self._reconstruct_contract_from_finalized_draft,
            salvage_truncated_draft=self._salvage_truncated_draft,
        )
        completion = self.parse_agent_completion(event.payload)
        outcome = subagent_stop_core.run_subagent_stop(
            host, event.payload, completion, event_session_id=event.session_id,
        )
        output = outcome.result
        if outcome.user_message:
            output["systemMessage"] = outcome.user_message
            output.setdefault("hookSpecificOutput", {"hookEventName": "SubagentStop"})
        if outcome.rejected:
            logger.warning("Returning exit_code=2 due to contract rejection")
            return HookResponse(output=output, exit_code=2)
        return HookResponse(output=output, exit_code=0)

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
        """``subagent_stop_core.reconstruct_contract_from_finalized_draft``."""
        return subagent_stop_core.reconstruct_contract_from_finalized_draft(
            task_info=task_info,
            parsed_contract=parsed_contract,
            session_id=session_id,
        )

    def _salvage_truncated_draft(
        self,
        *,
        parsed_contract,
        task_info: dict,
        session_id: str,
        plan_task_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """``subagent_stop_core.salvage_truncated_draft``."""
        return subagent_stop_core.salvage_truncated_draft(
            parsed_contract=parsed_contract,
            task_info=task_info,
            session_id=session_id,
            plan_task_id=plan_task_id,
        )

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

    CONTEXT_CACHE_DIR = tmp_dir() / "gaia-context-cache"
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

        # Only this session's own entries are candidates: with none, nothing is
        # injected rather than another session's newest digest.
        candidates: List[Path] = sorted(
            self.CONTEXT_CACHE_DIR.glob(f"{session_id}-*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

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

    RESUME_MAP_CACHE_DIR = tmp_dir() / "gaia-contract-resume-map"
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
        (AC-19's "IN_PROGRESS across resumes"). Only the session's own
        mapping is read: without one this returns None, never the agent
        another session resumed.
        """
        if not self.RESUME_MAP_CACHE_DIR.exists():
            return None
        self._cleanup_stale_resume_mappings()

        candidate = self.RESUME_MAP_CACHE_DIR / f"{session_id}.json"
        if not candidate.is_file():
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
            return self._closed_contract_hint(agent_id)
        envelope = load_draft(draft_id)
        if not envelope:
            return self._closed_contract_hint(agent_id)
        return render_resume_hint(draft_id, envelope)

    @staticmethod
    def _closed_contract_hint(agent_id: str) -> Optional[str]:
        """The hint for a resumed agent whose newest contract already closed, or None.

        A closed contract leaves no live draft, so the resumed turn would
        otherwise start with no contract and learn of it from a denied ``init``.
        """
        try:
            from gaia.state import CLOSED_TURN_PLAN_STATUSES
            from gaia.contract.view import render_closed_contract_hint
            from gaia.store.writer import list_agent_contract_handoffs

            rows = list_agent_contract_handoffs(agent_id=agent_id, limit=50)
            newest = max(rows, key=lambda row: row.get("id") or 0) if rows else None
            if newest is None or newest.get("agent_state") not in CLOSED_TURN_PLAN_STATUSES:
                return None
            return render_closed_contract_hint(newest["contract_id"], newest["agent_state"])
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # v43 dispatch kernel: claim the born row, render the kernel blocks
    # ------------------------------------------------------------------ #

    @staticmethod
    def _maybe_claim_dispatch_kernel(raw: dict) -> Optional[str]:
        """Translate a start event into the host-neutral dispatch-lifecycle
        claim and return its rendered kernel (or None).

        All orchestration (claim -> stamp -> render) lives in the host-neutral
        facade ``modules.agents.dispatch_lifecycle.claim_dispatch_kernel``;
        this method only maps this host's own start-event fields onto that
        facade's neutral arguments (``prompt_id`` -> ``dispatch_prompt_id``,
        ``task_description`` -> ``dispatch_description``, ``agent_type`` ->
        ``agent_name``, ``agent_id`` -> ``host_agent_id``, ``tool_use_id`` ->
        ``dispatch_tool_use_id``). See the facade's own docstring for why the
        stamp seam sits at the claim. ``tool_use_id`` is mapped for
        forward-compatibility only: this host's SubagentStart payload carries
        no such key today, so the mapping always resolves to ``None`` here and
        the exact-callID layer 0 never engages on this host's live path.
        """
        try:
            from modules.agents.dispatch_lifecycle import claim_dispatch_kernel
        except Exception as exc:
            logger.debug("dispatch claim/kernel failed (non-fatal): %s", exc)
            return None

        agent_type = raw.get("agent_type", "")
        return claim_dispatch_kernel(
            agent_name=agent_type or None,
            dispatch_prompt_id=raw.get("prompt_id") or None,
            dispatch_description=raw.get("task_description") or None,
            host_agent_id=raw.get("agent_id", "") or None,
            dispatch_tool_use_id=raw.get("tool_use_id") or None,
        )

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
           same session_id maps to an
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
