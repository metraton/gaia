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
from pathlib import Path
from typing import Any, Dict, Optional

from .base import HookAdapter
from .types import (
    AgentCompletion,
    BootstrapResult,
    CompletionResult,
    ContextResult,
    DistributionChannel,
    HookEvent,
    HookEventType,
    HookResponse,
    PermissionDecision,
    QualityResult,
    ToolResult,
    ValidationRequest,
    ValidationResult,
    VerificationResult,
)

logger = logging.getLogger(__name__)


class ClaudeCodeAdapter(HookAdapter):
    """Concrete adapter for Claude Code v2.1+ hook protocol.

    Claude Code sends JSON on stdin with these top-level fields:
        - hook_event_name: str  (e.g. "PreToolUse", "PostToolUse", "SubagentStop")
        - session_id: str
        - tool_name: str        (PreToolUse / PostToolUse)
        - tool_input: dict      (PreToolUse / PostToolUse)
        - tool_result: dict     (PostToolUse only)
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

        channel = self.detect_channel()
        plugin_root = self._get_plugin_root() if channel == DistributionChannel.PLUGIN else None

        return HookEvent(
            event_type=event_type,
            session_id=session_id,
            payload=raw,
            channel=channel,
            plugin_root=plugin_root,
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
        """Format a ContextResult for context injection hooks.

        Returns additionalContext field that Claude Code appends to the prompt.
        Note: UserPromptSubmit was simplified to a static echo in settings.json.
        This method is retained for SubagentStart and future context injection hooks.
        """
        output: Dict[str, Any] = {}

        if result.context_injected and result.additional_context:
            output["additionalContext"] = result.additional_context

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
    # detect_channel: determine NPM vs PLUGIN distribution
    # ------------------------------------------------------------------ #

    def detect_channel(self) -> DistributionChannel:
        """Detect distribution channel.

        Priority:
        1. CLAUDE_PLUGIN_ROOT env var set -> PLUGIN
        2. Default -> NPM
        """
        if os.environ.get("CLAUDE_PLUGIN_ROOT"):
            return DistributionChannel.PLUGIN
        return DistributionChannel.NPM

    # ------------------------------------------------------------------ #
    # Helper: get_plugin_root
    # ------------------------------------------------------------------ #

    def _get_plugin_root(self) -> Optional[Path]:
        """Resolve plugin root from CLAUDE_PLUGIN_ROOT env var."""
        plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
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
        tool_result = raw.get("tool_result", {})
        session_id = raw.get("session_id", "")

        command = tool_input.get("command", "")
        output = tool_result.get("output", "")
        exit_code = tool_result.get("exit_code", 0)

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
    # adapt_pre_tool_use: full pre-tool-use lifecycle
    # ------------------------------------------------------------------ #

    def adapt_pre_tool_use(self, event: HookEvent) -> HookResponse:
        """Run all pre-tool-use business logic and return a formatted response.

        Orchestrates: routing (bash vs task), validation, state management,
        context injection, approval handling, and response formatting.
        """
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.security.approval_constants import (
            NONCE_APPROVAL_PATTERN,
        )
        from modules.security.approval_messages import (
            build_activation_failed_message,
            build_deprecated_approval_message,
            build_invalid_nonce_message,
        )
        from modules.security.approval_grants import (
            activate_pending_approval,
            cleanup_expired_grants,
        )
        from modules.tools.bash_validator import BashValidator
        from modules.tools.task_validator import TaskValidator, AVAILABLE_AGENTS, META_AGENTS
        from modules.security.prompt_validator import classify_resume_prompt
        from modules.context.context_injector import inject_project_context
        from modules.session.session_event_injector import inject_session_events

        hook_data = event.payload
        tool_name = hook_data.get("tool_name") or ""
        tool_input = hook_data.get("tool_input", {})

        logger.info("Hook invoked: tool=%s, params=%s", tool_name, json.dumps(tool_input)[:200])

        try:
            # Periodic cleanup of expired approval grants
            cleanup_expired_grants()

            if not isinstance(tool_name, str):
                return HookResponse(output="Error: Invalid tool name", exit_code=2)
            if not isinstance(tool_input, dict):
                return HookResponse(output="Error: Invalid parameters", exit_code=2)

            if tool_name.lower() == "bash":
                return self._adapt_bash(tool_name, tool_input)
            elif tool_name.lower() in ("task", "agent"):
                hooks_dir = Path(__file__).parent.parent
                project_agents = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
                return self._adapt_task(
                    tool_name, tool_input, project_agents, hooks_dir,
                )
            elif tool_name.lower() == "sendmessage":
                return self._adapt_send_message(tool_name, tool_input)
            else:
                # Other tools pass through
                return HookResponse(output={}, exit_code=0)

        except Exception as e:
            logger.error("Unexpected error in adapt_pre_tool_use: %s", e, exc_info=True)
            return HookResponse(
                output=f"Error during security validation: {str(e)}",
                exit_code=2,
            )

    def _adapt_bash(self, tool_name: str, parameters: dict) -> HookResponse:
        """Handle Bash tool validation within the adapter."""
        from modules.core.state import create_pre_hook_state, save_hook_state
        from modules.tools.bash_validator import BashValidator

        command = parameters.get("command", "")
        if not command:
            return HookResponse(output="Error: Bash tool requires a command", exit_code=2)

        validator = BashValidator()
        result = validator.validate(command)

        if not result.allowed:
            logger.warning("BLOCKED: %s - %s", command[:100], result.reason)
            # Structured block responses must be returned as JSON dict (exit 0)
            if result.block_response is not None:
                return HookResponse(output=result.block_response, exit_code=0)
            return HookResponse(
                output=self._format_blocked_message(result),
                exit_code=2,
            )

        # Save state for post-hook
        effective_command = result.modified_input.get("command", command) if result.modified_input else command
        state = create_pre_hook_state(
            tool_name=tool_name,
            command=effective_command,
            tier=str(result.tier),
            allowed=True,
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
    ) -> HookResponse:
        """Handle Task/Agent tool validation within the adapter.

        Uses additionalContext (Phase 2) instead of prompt mutation.
        Validation runs against the original prompt, eliminating T3 false positives.
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

        additional = "\n".join(filter(None, [context_text, events_text]))
        if additional:
            logger.info("Returning additionalContext for %s (context injected)", result.agent_name)
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": f"Context injected for {result.agent_name}",
                    "additionalContext": additional,
                }
            }
            return HookResponse(output=output, exit_code=0)

        return HookResponse(output={}, exit_code=0)

    def _adapt_send_message(
        self, tool_name: str, parameters: dict,
    ) -> HookResponse:
        """Handle SendMessage tool validation for agent resumption.

        Validates agent ID format and message content, then runs nonce
        approval checks. Does NOT inject project context (it's a resume).
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

        approval_error, has_approval = self._adapt_resume_approval(agent_id, message)
        if approval_error:
            return HookResponse(output=approval_error, exit_code=2)

        state = create_pre_hook_state(
            tool_name=tool_name,
            command=f"SendMessage:{agent_id}",
            tier="T0",
            allowed=True,
            is_t3=False,
            has_approval=has_approval,
        )
        save_hook_state(state)

        logger.info("ALLOWED SendMessage: agent %s - message length: %d", agent_id, len(message))
        return HookResponse(output={}, exit_code=0)

    def _adapt_resume_approval(
        self, resume_id: str, prompt: str,
    ) -> tuple[str | None, bool]:
        """Process nonce approval indicators for Task resume."""
        from modules.security.approval_constants import NONCE_APPROVAL_PATTERN
        from modules.security.approval_messages import (
            build_activation_failed_message,
            build_deprecated_approval_message,
            build_invalid_nonce_message,
        )
        from modules.security.approval_grants import activate_pending_approval
        from modules.security.prompt_validator import classify_resume_prompt

        classification = classify_resume_prompt(prompt)

        if classification == "nonce":
            nonce = NONCE_APPROVAL_PATTERN.search(prompt).group(1)
            activation = activate_pending_approval(nonce)
            status_text = getattr(activation.status, "value", str(activation.status))
            if activation.success:
                grant_path = activation.grant_path
                grant_name = grant_path.name if grant_path else "<unknown>"
                logger.info(
                    "Nonce approval activated for resume %s: nonce=%s, file=%s",
                    resume_id, nonce, grant_name,
                )
                return None, True

            logger.warning(
                "Denied resume %s: nonce approval activation failed for nonce=%s "
                "(status=%s, reason=%s)",
                resume_id, nonce, status_text, activation.reason,
            )
            return build_activation_failed_message(nonce, status_text, activation.reason), False

        if classification == "malformed_nonce":
            logger.warning(
                "Denied resume %s: malformed nonce approval token in prompt='%s'",
                resume_id, prompt[:120],
            )
            return build_invalid_nonce_message(), False

        if classification == "deprecated":
            logger.warning(
                "Denied resume %s: deprecated legacy approval phrase detected",
                resume_id,
            )
            return build_deprecated_approval_message(), False

        return None, False

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
        writing, and state cleanup.
        """
        from modules.core.state import get_hook_state, clear_hook_state
        from modules.audit.logger import log_execution
        from modules.audit.event_detector import detect_critical_event
        from modules.session.session_context_writer import SessionContextWriter
        from modules.security.approval_grants import check_approval_grant, confirm_grant

        hook_data = event.payload
        tool_result_data = self.parse_post_tool_use(hook_data)
        logger.info("Post-hook event: %s", hook_data.get("hook_event_name"))

        raw_tool_result = hook_data.get("tool_result", {})
        tool_name = tool_result_data.tool_name
        parameters = hook_data.get("tool_input", {})
        output = tool_result_data.output
        duration = raw_tool_result.get("duration_ms", 0) / 1000.0
        success = tool_result_data.exit_code == 0

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

            # Confirm unconfirmed T3 grants after successful Bash execution
            if tool_name == "Bash" and success:
                command = parameters.get("command", "")
                if command:
                    grant = check_approval_grant(command)
                    if grant is not None and not grant.confirmed:
                        confirm_grant(command)
                        logger.info(
                            "T3 grant confirmed post-execution: %s", command[:80],
                        )

            events = detect_critical_event(tool_name, parameters, output, success)
            if events:
                writer = SessionContextWriter()
                for evt in events:
                    writer.update_context(evt.to_dict())

            clear_hook_state()
            logger.debug("Post-hook completed for %s", tool_name)

        except Exception as e:
            logger.error("Error in adapt_post_tool_use: %s", e, exc_info=True)

        return HookResponse(output={}, exit_code=0)

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
            validate_awaiting_approval_has_nonce,
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
        from modules.context.context_writer import process_context_updates
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

        # Run the main processing chain
        try:
            from datetime import datetime as _dt
            session_id = get_or_create_session_id()
            agent_type = task_info.get("agent", "unknown")

            parsed_contract = parse_contract(agent_output)

            contract_result = validate_contract(agent_output, task_info)
            if not contract_result.is_valid:
                logger.warning(
                    "Contract validation failed for %s: %s",
                    agent_type, contract_result.error_message,
                )

            cleanup_approval(agent_type)

            commands_executed = extract_commands_from_evidence(agent_output)
            context_update_result = process_context_updates(agent_output, task_info)

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
            # False pending-approval detection
            # Advisory only -- adds to anomalies but never blocks.
            # ----------------------------------------------------------
            _plan_status = ""
            if parsed_contract and isinstance(parsed_contract.get("agent_status"), dict):
                _plan_status = str(parsed_contract["agent_status"].get("plan_status", ""))
            false_pa_check = validate_awaiting_approval_has_nonce(agent_output, _plan_status)
            if false_pa_check:
                anomalies.append(false_pa_check)
                logger.info(
                    "AWAITING_APPROVAL without nonce for %s: %s",
                    agent_type, false_pa_check.get("detail", ""),
                )

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
            # Option B: Selective enforcement for critical structural failures.
            # Only 3 cases set contract_rejected=True:
            #   1. json:contract block completely missing
            #   2. plan_status missing or not one of the 8 valid statuses
            #   3. agent_status block missing entirely
            # ----------------------------------------------------------
            contract_rejected = False
            contract_rejection_reason = ""

            if parsed_contract is None:
                contract_rejected = True
                contract_rejection_reason = (
                    "[CONTRACT REJECTED] No json:contract block found in agent response.\n"
                    "The agent must end its response with a ```json:contract``` fenced block.\n"
                    "Reissue the response with a complete json:contract block."
                )
            elif not parsed_contract.get("agent_status") or not isinstance(
                parsed_contract.get("agent_status"), dict
            ):
                contract_rejected = True
                contract_rejection_reason = (
                    "[CONTRACT REJECTED] agent_status block missing from json:contract.\n"
                    "The json:contract block must include an agent_status object with "
                    "plan_status, agent_id, pending_steps, and next_action."
                )
            else:
                from modules.agents.response_contract import VALID_PLAN_STATUSES
                raw_plan_status = parsed_contract["agent_status"].get("plan_status", "")
                normalized = str(raw_plan_status).upper().rstrip(".,;") if raw_plan_status else ""
                if not normalized or normalized not in VALID_PLAN_STATUSES:
                    contract_rejected = True
                    valid_list = ", ".join(sorted(VALID_PLAN_STATUSES))
                    contract_rejection_reason = (
                        f"[CONTRACT REJECTED] plan_status is missing or invalid: "
                        f"'{raw_plan_status}'.\n"
                        f"Valid statuses: {valid_list}.\n"
                        f"Set plan_status to one of these values in agent_status."
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
            }

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
        # Extract stop reason and response content for future quality checks
        _stop_reason = raw.get("stop_reason", "")
        _last_message = raw.get("last_assistant_message", "")

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
        # Extract task details for future verification logic
        _task_id = raw.get("task_id", "")
        _task_output = raw.get("task_output", "")

        return VerificationResult(
            criteria_met=True,
            verified_items=[],
            failed_items=[],
            block_completion=False,
        )

    # ------------------------------------------------------------------ #
    # P2: adapt_subagent_start
    # ------------------------------------------------------------------ #

    def adapt_subagent_start(self, raw: dict) -> ContextResult:
        """Parse SubagentStart event. Context injection is handled by PreToolUse."""
        _agent_type = raw.get("agent_type", "")
        _task_description = raw.get("task_description", "")

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
