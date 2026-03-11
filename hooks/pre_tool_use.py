#!/usr/bin/env python3
"""
Pre-tool use hook - Thin Gate Architecture.

Entry point for Bash and Task/Agent tool validation. The hook is the primary
security gate: with Bash(*) in the settings.json allow list, all commands
reach this hook regardless of settings.json permissions.

This file is a thin gate: parse -> delegate -> format -> exit.
All business logic lives in modules under hooks/modules/.

Responsibilities:
- Bash: delegates to BashValidator (blocked commands, safe detection,
  dangerous verb detector with nonce-based approval flow)
- Task/Agent: delegates to prompt_validator, contracts_loader,
  context_injector, and session_event_injector
- State sharing with post-hook via hook state files
- Periodic cleanup of expired approval grants
"""

import sys
import json
import logging
import re
from pathlib import Path
from datetime import datetime

# Add modules to path
sys.path.insert(0, str(Path(__file__).parent))
from modules.core.paths import get_logs_dir

# Adapter layer: normalize stdin parsing and response formatting
from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import ValidationResult
from adapters.utils import has_stdin_data, warn_if_dual_channel

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
from modules.tools.bash_validator import BashValidator, create_permission_allow_response
from modules.tools.task_validator import TaskValidator, AVAILABLE_AGENTS, META_AGENTS

# Extracted modules
from modules.security.prompt_validator import classify_resume_prompt
from modules.context.context_injector import inject_project_context
from modules.context.context_injector import should_inject_on_resume
from modules.session.session_event_injector import inject_session_events

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
# DERIVED CONSTANTS
# ============================================================================

# Derive from task_validator lists: project agents = available minus meta
PROJECT_AGENTS = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]

# Hooks directory for module fallback path resolution
_HOOKS_DIR = Path(__file__).parent


# ============================================================================
# THIN DELEGATION WRAPPERS
# ============================================================================
# These wrappers maintain the original private API so that existing tests
# (which monkeypatch pre_tool_use._handle_task, etc.) continue to work.
# They delegate all logic to the extracted modules.

def _classify_resume_prompt(prompt: str) -> str:
    """Classify a resume prompt. Delegates to modules.security.prompt_validator."""
    return classify_resume_prompt(prompt)


def _inject_project_context(parameters: dict) -> dict:
    """Inject project context. Delegates to modules.context.context_injector."""
    return inject_project_context(parameters, PROJECT_AGENTS, _HOOKS_DIR)


def _inject_session_events(parameters: dict) -> dict:
    """Inject session events. Delegates to modules.session.session_event_injector."""
    return inject_session_events(parameters, PROJECT_AGENTS)


# ============================================================================
# MAIN HOOK LOGIC
# ============================================================================

def pre_tool_use_hook(tool_name: str, parameters: dict) -> str | dict | None:
    """
    Pre-tool use hook implementation.

    Args:
        tool_name: Name of the tool being invoked
        parameters: Tool parameters

    Returns:
        None: allowed (no modification)
        str: blocked (error message, triggers exit 2)
        dict: allowed with modification (JSON with updatedInput, exit 0)
    """
    logger.info(f"Hook invoked: tool={tool_name}, params={json.dumps(parameters)[:200]}")

    try:
        # Periodic cleanup of expired approval grants (fast no-op if none exist)
        cleanup_expired_grants()

        # Validate inputs
        if not isinstance(tool_name, str):
            return "Error: Invalid tool name"
        if not isinstance(parameters, dict):
            return "Error: Invalid parameters"

        # Route to appropriate validator
        if tool_name.lower() == "bash":
            return _handle_bash(tool_name, parameters)
        elif tool_name.lower() in ("task", "agent"):
            return _handle_task(tool_name, parameters)
        else:
            # Other tools pass through
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

    # Validate command
    validator = BashValidator()
    result = validator.validate(command)

    if not result.allowed:
        logger.warning(f"BLOCKED: {command[:100]} - {result.reason}")
        # Structured block responses (e.g. cloud pipe violations) must be returned
        # as a JSON dict (exit 0) so the agent receives the correction message
        # and adjusts — not as a plain string (exit 2) which terminates the agent.
        if result.block_response is not None:
            return result.block_response
        return _format_blocked_message(result)

    # Save state for post-hook (ONLY for allowed commands)
    effective_command = result.modified_input.get("command", command) if result.modified_input else command
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=effective_command,
        tier=str(result.tier),
        allowed=True,
    )
    save_hook_state(state)

    # If validator modified the command (e.g., stripped footer), emit updatedInput
    if result.modified_input:
        logger.info(f"MODIFIED: {command[:80]} → stripped footer - tier={result.tier}")
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
    Handle Task tool validation with resume support.

    Validates both new Task invocations and resume operations.
    Resume operations skip heavy validations since agent was already validated.

    NEW: Automatically injects project context for project agents.
    Returns updatedInput when prompt is modified so Claude Code applies changes.
    """
    # ========================================================================
    # PROJECT CONTEXT INJECTION (for new tasks only, not resume)
    # ========================================================================
    original_prompt = parameters.get("prompt", "")
    if not parameters.get("resume"):
        parameters = _inject_project_context(parameters)
        parameters = _inject_session_events(parameters)

    # ========================================================================
    # RESUME DETECTION AND VALIDATION
    # ========================================================================
    resume_id = parameters.get("resume")

    if resume_id:
        # This is a resume operation - validate differently

        # Step 24: Validate agentId format
        # Pattern: a###### where # is 0-9 or a-f (6-7 chars after 'a')
        if not re.match(r'^a[0-9a-f]{5,}$', resume_id):
            logger.warning(f"BLOCKED Resume: Invalid agentId format '{resume_id}'")
            return (
                f"❌ Invalid resume ID format: '{resume_id}'\n\n"
                "Agent ID should be 'a' followed by hex characters.\n"
                "Example: a12345f or a51a0cbbf6afb831d\n\n"
                "The agent ID is returned at the end of agent responses.\n"
                "Look for: 'agentId: a...' in the previous agent output."
            )

        # Step 25: Validate that prompt exists
        prompt = parameters.get("prompt", "")
        if not prompt or not prompt.strip():
            logger.warning(f"BLOCKED Resume: Missing prompt for agent {resume_id}")
            return (
                "❌ Resume requires a prompt\n\n"
                "When resuming an agent, you must provide instructions:\n\n"
                "Task(\n"
                "    resume=\"a12345\",\n"
                "    prompt=\"Continue with the latest user instruction.\"\n"
                ")\n\n"
                "The prompt tells the agent what to do next."
            )

        # Step 26: Skip heavy validations (agent already validated in phase 1)
        # Step 27: Log resume operation
        logger.info(f"✅ RESUME: Continuing agent {resume_id}")

        approval_error, has_approval = _handle_resume_approval(resume_id, prompt)
        if approval_error:
            return approval_error

        # Step 28: Save state for post-hook
        state = create_pre_hook_state(
            tool_name=tool_name,
            command=f"Task:resume:{resume_id}",
            tier="T0",  # Resume doesn't change tier
            allowed=True,
            is_t3=False,  # Approval already handled in phase 1
            has_approval=has_approval,
        )
        save_hook_state(state)

        logger.info(f"ALLOWED Resume: agent {resume_id} - prompt length: {len(prompt)}")
        return None  # Allow resume

    # ========================================================================
    # STANDARD TASK VALIDATION (new task, not resume)
    # ========================================================================
    validator = TaskValidator()
    result = validator.validate(parameters)

    if not result.allowed:
        logger.warning(f"BLOCKED Task: {result.agent_name} - {result.reason}")
        return result.reason

    # Save state for post-hook (ONLY for allowed tasks)
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=f"Task:{result.agent_name}",
        tier=str(result.tier),
        allowed=True,  # Always true here
        is_t3=result.is_t3_operation,
    )
    save_hook_state(state)

    logger.info(f"ALLOWED Task: {result.agent_name}")

    # Return updatedInput if prompt was modified by context/skills injection
    if parameters.get("prompt", "") != original_prompt:
        updated_input = {k: v for k, v in parameters.items() if not k.startswith("_")}
        logger.info(f"Returning updatedInput for {result.agent_name} (context injected)")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "permissionDecisionReason": f"Context injected for {result.agent_name}",
                "updatedInput": updated_input
            }
        }

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
    """Format blocked command message."""
    msg = (
        f"Command blocked by security policy\n\n"
        f"Tier: {result.tier}\n"
        f"Reason: {result.reason}\n"
    )

    if result.suggestions:
        msg += "\nSuggestions:\n"
        for suggestion in result.suggestions:
            msg += f"  - {suggestion}\n"

    return msg


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    """CLI interface for testing."""
    if len(sys.argv) < 2:
        print("Usage: python pre_tool_use_v2.py <command>")
        print("       python pre_tool_use_v2.py --test")
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
    print("Testing Pre-Tool Use Hook v2...\n")

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
            # --- Adapter: parse stdin ---
            adapter = ClaudeCodeAdapter()

            # Coexistence check: warn if both plugin and npm channels are active
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

            # Compatibility: expose raw payload for business logic
            hook_data = event.payload

            logger.info(f"Hook event: {hook_data.get('hook_event_name')}")

            tool_name = hook_data.get("tool_name") or ""
            tool_input = hook_data.get("tool_input", {})

            # Standard validation (auto-approval handled in BashValidator)
            result = pre_tool_use_hook(tool_name, tool_input)
            if isinstance(result, dict):
                # Dict = allowed with modification (updatedInput)
                # Output JSON so Claude Code applies the modified parameters
                #
                # Special case: structured block responses (e.g. cloud pipe violations)
                # use permissionDecision: "deny" inside a dict.  Claude Code still
                # shows the agent the reason, but stderr output ensures the USER also
                # sees WHY the command was blocked in the hook output panel.
                hook_output = result.get("hookSpecificOutput", {})
                if hook_output.get("permissionDecision") in ("block", "deny"):
                    reason = hook_output.get("permissionDecisionReason", "Command blocked by hook policy")
                    # One-line summary for stderr (first line of the reason)
                    summary = reason.split('\n')[0]
                    print(f"BLOCKED: {summary}", file=sys.stderr)
                print(json.dumps(result))
                sys.exit(0)
            elif isinstance(result, str):
                # String = BLOCKING error - Claude MUST stop execution
                # Exit code 2 = blocking, exit code 1 = non-blocking
                # See: https://docs.anthropic.com/en/docs/claude-code/hooks
                #
                # Write the rejection reason to stderr so Claude Code displays
                # it to the user.  stdout carries the full message for the agent;
                # stderr carries a concise summary for the human operator.
                summary = result.split('\n')[0]
                print(f"BLOCKED: {summary}", file=sys.stderr)
                print(result)
                sys.exit(2)
            else:
                # None = allowed, no modification
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
        # No args and no stdin - show usage
        print("Usage: python pre_tool_use_v2.py <command>")
        print("       python pre_tool_use_v2.py --test")
        print("       echo '{\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"ls\"}}' | python pre_tool_use_v2.py")
        sys.exit(1)
