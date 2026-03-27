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
from __future__ import annotations

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
from modules.context.context_injector import build_project_context
from modules.session.session_event_injector import build_session_events
from modules.core.state import create_pre_hook_state, save_hook_state
from modules.security.approval_grants import (
    cleanup_expired_grants,
)

# Derived constants used by backward-compat wrappers
PROJECT_AGENTS = [a for a in AVAILABLE_AGENTS if a not in META_AGENTS]
_HOOKS_DIR = Path(__file__).parent


def _classify_resume_prompt(prompt: str) -> str:
    """Classify a resume prompt. Delegates to modules.security.prompt_validator."""
    return classify_resume_prompt(prompt)


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

        # Structured response from bash_validator (ask or deny)
        if result.block_response is not None:
            return result.block_response

        # Permanently blocked (no structured response) — hard block (exit 2)
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

    Context is built here and cached for SubagentStart to forward to the
    subagent.  PreToolUse no longer returns additionalContext (that would
    inject it into the orchestrator, not the subagent).
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

    # Cache context for SubagentStart to pick up and forward to the subagent.
    additional = "\n".join(filter(None, [context_text, events_text]))

    # Fallback: if build_project_context returned None because the
    # orchestrator already embedded context in the prompt (dedup guard),
    # extract the embedded context so SubagentStart can still inject it.
    if not additional:
        prompt = parameters.get("prompt", "")
        marker = "# Project Context"
        if marker in prompt:
            idx = prompt.index(marker)
            additional = prompt[idx:]
            logger.info(
                "Extracted embedded context from prompt for caching "
                "(len=%d, agent=%s)",
                len(additional), result.agent_name,
            )

    if additional:
        from adapters.claude_code import ClaudeCodeAdapter
        adapter = ClaudeCodeAdapter()
        session_id = parameters.get("session_id", "") or "unknown"
        agent_type = result.agent_name or "unknown"
        adapter._cache_context_for_subagent(session_id, agent_type, additional)
        logger.info(f"Cached context for SubagentStart: agent={agent_type}")

    return None


def _handle_send_message(tool_name: str, parameters: dict) -> str | None:
    """
    Handle SendMessage tool validation for agent resumption.

    Validates agent ID format and message content. Does NOT inject
    project context (it's a resume). Nonce relay is no longer processed
    here -- approval grants are activated by the UserPromptSubmit hook.

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

    state = create_pre_hook_state(
        tool_name=tool_name,
        command=f"SendMessage:{agent_id}",
        tier="T0",
        allowed=True,
        is_t3=False,
        has_approval=False,
    )
    save_hook_state(state)

    logger.info(f"ALLOWED SendMessage: agent {agent_id} - message length: {len(message)}")
    return None


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
                decision = hook_output.get("permissionDecision")
                if decision in ("block", "deny"):
                    reason = hook_output.get("permissionDecisionReason", "Command blocked by hook policy")
                    summary = reason.split('\n')[0]
                    print(f"BLOCKED: {summary}", file=sys.stderr)
                elif decision == "ask":
                    reason = hook_output.get("permissionDecisionReason", "")
                    summary = reason.split('\n')[0]
                    print(f"T3: {summary}", file=sys.stderr)
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
