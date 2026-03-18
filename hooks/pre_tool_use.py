#!/usr/bin/env python3
"""
Pre-tool use hook - Thin Gate Architecture.

Entry point for Bash and Task/Agent tool validation. The hook is the primary
security gate: with Bash(*) in the settings.json allow list, all commands
reach this hook regardless of settings.json permissions.

Architecture:
- Uses adapter layer to parse and process the full PreToolUse lifecycle
- All business logic lives in ClaudeCodeAdapter.adapt_pre_tool_use()
- This file is stdin/stdout glue only
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from modules.core.paths import get_logs_dir

# Adapter layer
from adapters.claude_code import ClaudeCodeAdapter
from modules.core.stdin import has_stdin_data
from adapters.utils import warn_if_dual_channel

# Configure logging -- all hooks share hooks-YYYY-MM-DD.log for easy tailing
log_file = get_logs_dir() / f"hooks-{datetime.now().strftime('%Y-%m-%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [pre_tool_use] %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# BACKWARD-COMPATIBLE API
# ============================================================================
# Tests and e2e scripts import these names directly. They delegate to the
# adapter internally but preserve the original call signatures.

from modules.tools.bash_validator import BashValidator
from modules.tools.task_validator import TaskValidator, AVAILABLE_AGENTS, META_AGENTS
from modules.security.prompt_validator import classify_resume_prompt
from modules.context.context_injector import inject_project_context, build_project_context
from modules.session.session_event_injector import inject_session_events, build_session_events
from modules.core.state import create_pre_hook_state, save_hook_state
from modules.security.approval_constants import (
    NONCE_APPROVAL_PREFIX,
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

# Derived constants used by backward-compat wrappers
PROJECT_AGENTS = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
_HOOKS_DIR = Path(__file__).parent


def _classify_resume_prompt(prompt: str) -> str:
    """Classify a resume prompt. Delegates to modules.security.prompt_validator."""
    return classify_resume_prompt(prompt)


def _inject_project_context(parameters: dict) -> dict:
    """Inject project context. Delegates to modules.context.context_injector."""
    return inject_project_context(parameters, PROJECT_AGENTS, _HOOKS_DIR)


def _inject_session_events(parameters: dict) -> dict:
    """Inject session events. Delegates to modules.session.session_event_injector."""
    return inject_session_events(parameters, PROJECT_AGENTS)


def pre_tool_use_hook(tool_name: str, parameters: dict) -> str | dict | None:
    """
    Pre-tool use hook implementation (backward-compatible API).

    Delegates to adapter but preserves the original return signature:
        None: allowed (no modification)
        str: blocked (error message)
        dict: allowed with modification (JSON with updatedInput)
    """
    adapter = ClaudeCodeAdapter()

    # Build a minimal HookEvent-like payload for the adapter's internal methods
    logger.info(f"Hook invoked: tool={tool_name}, params={json.dumps(parameters)[:200]}")

    try:
        cleanup_expired_grants()

        if not isinstance(tool_name, str):
            return "Error: Invalid tool name"
        if not isinstance(parameters, dict):
            return "Error: Invalid parameters"

        if tool_name.lower() == "bash":
            return _handle_bash(tool_name, parameters)
        elif tool_name.lower() in ("task", "agent"):
            return _handle_task(tool_name, parameters)
        elif tool_name.lower() == "sendmessage":
            return _handle_send_message(tool_name, parameters)
        else:
            return None

    except Exception as e:
        logger.error(f"Unexpected error in pre_tool_use_hook: {e}", exc_info=True)
        return f"Error during security validation: {str(e)}"


def _handle_bash(tool_name: str, parameters: dict) -> str | dict | None:
    """
    Handle Bash tool validation.

    Returns:
        None: allowed (no modification)
        str: blocked (error message)
        dict: allowed with modification (hookSpecificOutput JSON)
    """
    command = parameters.get("command", "")
    if not command:
        return "Error: Bash tool requires a command"

    validator = BashValidator()
    result = validator.validate(command)

    if not result.allowed:
        logger.warning(f"BLOCKED: {command[:100]} - {result.reason}")
        if result.block_response is not None:
            return result.block_response
        return _format_blocked_message(result)

    effective_command = result.modified_input.get("command", command) if result.modified_input else command
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=effective_command,
        tier=str(result.tier),
        allowed=True,
    )
    save_hook_state(state)

    if result.modified_input:
        logger.info(f"MODIFIED: {command[:80]} -> stripped footer - tier={result.tier}")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": result.reason,
                "updatedInput": result.modified_input
            }
        }

    logger.info(f"ALLOWED: {command[:100]} - tier={result.tier}")
    return None


def _handle_task(tool_name: str, parameters: dict) -> str | dict | None:
    """
    Handle Task/Agent tool validation for new task dispatches.

    Uses additionalContext (Phase 2) instead of prompt mutation.
    Validation runs against the original prompt, eliminating T3 false positives.
    """
    context_text, _telemetry = build_project_context(parameters, PROJECT_AGENTS, _HOOKS_DIR)
    events_text = build_session_events(parameters, PROJECT_AGENTS)

    # Standard task validation (runs against ORIGINAL prompt -- no workaround needed)
    validator = TaskValidator()
    result = validator.validate(parameters)

    if not result.allowed:
        logger.warning(f"BLOCKED Task: {result.agent_name} - {result.reason}")
        return result.reason

    state = create_pre_hook_state(
        tool_name=tool_name,
        command=f"Task:{result.agent_name}",
        tier=str(result.tier),
        allowed=True,
        is_t3=result.is_t3_operation,
    )
    save_hook_state(state)

    logger.info(f"ALLOWED Task: {result.agent_name}")

    additional = "\n".join(filter(None, [context_text, events_text]))
    if additional:
        logger.info(f"Returning additionalContext for {result.agent_name} (context injected)")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": f"Context injected for {result.agent_name}",
                "additionalContext": additional,
            }
        }

    return None


def _handle_send_message(tool_name: str, parameters: dict) -> str | None:
    """
    Handle SendMessage tool validation for agent resumption.

    Validates agent ID format and message content, then runs nonce
    approval checks. Does NOT inject project context (it's a resume).

    Returns:
        None: allowed (no modification)
        str: blocked (error message)
    """
    import re

    agent_id = parameters.get("to", "")
    message = parameters.get("message", "")

    if not agent_id or not re.match(r'^a[0-9a-f]{5,}$', agent_id):
        logger.warning(f"BLOCKED SendMessage: Invalid agentId format '{agent_id}'")
        return (
            f"[ERROR] Invalid agent ID format: '{agent_id}'\n\n"
            "Agent ID should be 'a' followed by hex characters.\n"
            "Example: a12345f or a51a0cbbf6afb831d\n\n"
            "The agent ID is returned at the end of agent responses.\n"
            "Look for: 'agentId: a...' in the previous agent output."
        )

    if not message or not message.strip():
        logger.warning(f"BLOCKED SendMessage: Missing message for agent {agent_id}")
        return (
            "[ERROR] SendMessage requires a message\n\n"
            "When resuming an agent, you must provide a message:\n\n"
            "SendMessage(\n"
            "    to=\"a12345\",\n"
            "    message=\"Continue with the latest user instruction.\"\n"
            ")\n\n"
            "The message tells the agent what to do next."
        )

    logger.info(f"SENDMESSAGE: Resuming agent {agent_id}")

    approval_error, has_approval = _handle_resume_approval(agent_id, message)
    if approval_error:
        return approval_error

    state = create_pre_hook_state(
        tool_name=tool_name,
        command=f"SendMessage:{agent_id}",
        tier="T0",
        allowed=True,
        is_t3=False,
        has_approval=has_approval,
    )
    save_hook_state(state)

    logger.info(f"ALLOWED SendMessage: agent {agent_id} - message length: {len(message)}")
    return None


def _handle_resume_approval(resume_id: str, prompt: str) -> tuple[str | None, bool]:
    """Process nonce approval indicators for Task resume."""
    classification = _classify_resume_prompt(prompt)

    if classification == "nonce":
        nonce = NONCE_APPROVAL_PATTERN.search(prompt).group(1)
        activation = activate_pending_approval(nonce)
        status_text = getattr(activation.status, "value", str(activation.status))
        if activation.success:
            grant_path = activation.grant_path
            grant_name = grant_path.name if grant_path else "<unknown>"
            logger.info(
                "Nonce approval activated for resume %s: nonce=%s, file=%s",
                resume_id,
                nonce,
                grant_name,
            )
            return None, True

        logger.warning(
            "Denied resume %s: nonce approval activation failed for nonce=%s "
            "(status=%s, reason=%s)",
            resume_id,
            nonce,
            status_text,
            activation.reason,
        )
        return build_activation_failed_message(nonce, status_text, activation.reason), False

    if classification == "malformed_nonce":
        logger.warning(
            "Denied resume %s: malformed nonce approval token in prompt='%s'",
            resume_id,
            prompt[:120],
        )
        return build_invalid_nonce_message(), False

    if classification == "deprecated":
        logger.warning(
            "Denied resume %s: deprecated legacy approval phrase detected",
            resume_id,
        )
        return build_deprecated_approval_message(), False

    return None, False


def _format_blocked_message(result) -> str:
    """Format blocked command message. Delegates to blocked_message_formatter."""
    from modules.security.blocked_message_formatter import format_blocked_message
    return format_blocked_message(result)


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """CLI interface for testing."""
    if len(sys.argv) < 2:
        print("Usage: python pre_tool_use.py <command>")
        print("       python pre_tool_use.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        _run_tests()
    else:
        command = " ".join(sys.argv[1:])
        result = pre_tool_use_hook("bash", {"command": command})
        if result:
            print(f"BLOCKED: {result}")
            sys.exit(1)
        else:
            print(f"ALLOWED: {command}")


def _run_tests():
    """Run validation tests."""
    print("Testing Pre-Tool Use Hook...\n")

    test_cases = [
        ("terraform validate", True, "T1"),
        ("terraform apply", False, "T3"),
        ("kubectl get pods", True, "T0"),
        ("kubectl apply -f manifest.yaml", False, "T3"),
        ("kubectl apply -f manifest.yaml --dry-run=client", True, "T2"),
        ("ls -la", True, "T0"),
        ("rm -rf /", False, "T3"),
    ]

    for command, expected_allowed, expected_tier in test_cases:
        result = pre_tool_use_hook("bash", {"command": command})
        actual_allowed = result is None
        status = "PASS" if actual_allowed == expected_allowed else "FAIL"
        print(f"{status}: {command}")
        if status == "FAIL":
            print(f"  Expected: allowed={expected_allowed}, Got: allowed={actual_allowed}")

    print("\nTest completed")


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    # Check if running from CLI with arguments
    if len(sys.argv) > 1:
        main()
    elif has_stdin_data():
        try:
            adapter = ClaudeCodeAdapter()
            warn_if_dual_channel()

            stdin_data = sys.stdin.read()

            try:
                event = adapter.parse_event(stdin_data)
            except ValueError as e:
                error_msg = str(e)
                logger.error(f"Adapter parse failed: {error_msg}")
                print(f"HOOK ERROR: {error_msg}", file=sys.stderr)
                if "Empty stdin" in error_msg:
                    print(f"Error: {error_msg}")
                sys.exit(1)

            response = adapter.adapt_pre_tool_use(event)

            if isinstance(response.output, dict) and response.output:
                hook_output = response.output.get("hookSpecificOutput", {})
                if hook_output.get("permissionDecision") in ("block", "deny"):
                    reason = hook_output.get("permissionDecisionReason", "Command blocked by hook policy")
                    summary = reason.split('\n')[0]
                    print(f"BLOCKED: {summary}", file=sys.stderr)
                print(json.dumps(response.output))
                sys.exit(response.exit_code)
            elif isinstance(response.output, str) and response.output:
                summary = response.output.split('\n')[0]
                print(f"BLOCKED: {summary}", file=sys.stderr)
                print(response.output)
                sys.exit(response.exit_code)
            else:
                sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            print(f"HOOK ERROR: Invalid JSON from stdin: {e}", file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            print(f"HOOK ERROR: {str(e)}", file=sys.stderr)
            print(f"Hook error: {str(e)}")
            sys.exit(1)
    else:
        print("Usage: python pre_tool_use.py <command>")
        print("       python pre_tool_use.py --test")
        print("       echo '{\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"ls\"}}' | python pre_tool_use.py")
        sys.exit(1)
