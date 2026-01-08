#!/usr/bin/env python3
"""
Pre-tool use hook v2 - Modular Architecture

Thin entry point that uses the new modular hook system.
Maintains backward compatibility with Claude Code hook interface.

Features:
- Modular validation (security, tools, workflow modules)
- Orchestrator gate (restricts tools for orchestrator)
- State sharing with post-hook
- Auto-approval for read-only commands
"""

import sys
import json
import logging
import os
import select
from pathlib import Path
from datetime import datetime

# Add modules to path
sys.path.insert(0, str(Path(__file__).parent))

from modules.core.paths import get_logs_dir
from modules.core.state import create_pre_hook_state, save_hook_state
from modules.security.tiers import SecurityTier, classify_command_tier
from modules.security.safe_commands import is_read_only_command
from modules.tools.bash_validator import BashValidator, create_permission_allow_response
from modules.tools.task_validator import TaskValidator

# Configure logging
log_file = get_logs_dir() / f"pre_tool_use_v2-{os.getenv('USER', 'unknown')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# ORCHESTRATOR GATE
# ============================================================================
# The orchestrator should delegate, not execute directly.
# Only specific tools are allowed for the orchestrator.
# ============================================================================

ORCHESTRATOR_ALLOWED_TOOLS = {
    "Read",           # Reading context files
    "Task",           # Delegating to agents
    "TodoWrite",      # Managing task lists
    "AskUserQuestion", # Getting user input
}

ORCHESTRATOR_CONTEXT_INDICATORS = [
    "orchestrator",
    "CLAUDE.md",
    "Phase 1",
    "Phase 2",
    "Phase 3",
    "Phase 4",
    "Phase 5",
    "routing",
]


def is_orchestrator_context() -> bool:
    """
    Detect if we're running in orchestrator context.

    Uses environment variables and context hints.
    """
    # Check environment
    if os.environ.get("GAIA_ORCHESTRATOR_MODE") == "true":
        return True

    # Check session context
    session_context = os.environ.get("CLAUDE_SESSION_CONTEXT", "")
    return any(indicator in session_context for indicator in ORCHESTRATOR_CONTEXT_INDICATORS)


def check_orchestrator_gate(tool_name: str) -> tuple[bool, str]:
    """
    Check if tool is allowed for orchestrator.

    Args:
        tool_name: Name of the tool being invoked

    Returns:
        (allowed, reason)
    """
    if not is_orchestrator_context():
        return True, "Not in orchestrator context"

    if tool_name in ORCHESTRATOR_ALLOWED_TOOLS:
        return True, f"Tool {tool_name} allowed for orchestrator"

    return False, (
        f"Orchestrator should not use {tool_name} directly.\n\n"
        f"Orchestrator allowed tools: {', '.join(ORCHESTRATOR_ALLOWED_TOOLS)}\n\n"
        "Delegate to appropriate agent using Task tool instead."
    )


# ============================================================================
# MAIN HOOK LOGIC
# ============================================================================

def pre_tool_use_hook(tool_name: str, parameters: dict) -> str | None:
    """
    Pre-tool use hook implementation.

    Args:
        tool_name: Name of the tool being invoked
        parameters: Tool parameters

    Returns:
        None if allowed, error message if blocked
    """
    logger.info(f"Hook invoked: tool={tool_name}, params={json.dumps(parameters)[:200]}")

    try:
        # Validate inputs
        if not isinstance(tool_name, str):
            return "Error: Invalid tool name"
        if not isinstance(parameters, dict):
            return "Error: Invalid parameters"

        # Check orchestrator gate
        gate_allowed, gate_reason = check_orchestrator_gate(tool_name)
        if not gate_allowed:
            logger.warning(f"Orchestrator gate blocked: {gate_reason}")
            return gate_reason

        # Route to appropriate validator
        if tool_name.lower() == "bash":
            return _handle_bash(tool_name, parameters)
        elif tool_name.lower() == "task":
            return _handle_task(tool_name, parameters)
        else:
            # Other tools pass through
            return None

    except Exception as e:
        logger.error(f"Unexpected error in pre_tool_use_hook: {e}", exc_info=True)
        return f"Error during security validation: {str(e)}"


def _handle_bash(tool_name: str, parameters: dict) -> str | None:
    """Handle Bash tool validation."""
    command = parameters.get("command", "")
    if not command:
        return "Error: Bash tool requires a command"

    # Validate command
    validator = BashValidator()
    result = validator.validate(command)

    # Save state for post-hook
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=command,
        tier=str(result.tier),
        allowed=result.allowed,
    )
    save_hook_state(state)

    if not result.allowed:
        logger.warning(f"BLOCKED: {command[:100]} - {result.reason}")
        return _format_blocked_message(result)

    logger.info(f"ALLOWED: {command[:100]} - tier={result.tier}")
    return None


def _handle_task(tool_name: str, parameters: dict) -> str | None:
    """Handle Task tool validation."""
    validator = TaskValidator()
    result = validator.validate(parameters)

    # Save state for post-hook
    state = create_pre_hook_state(
        tool_name=tool_name,
        command=f"Task:{result.agent_name}",
        tier=str(result.tier),
        allowed=result.allowed,
        is_t3=result.is_t3_operation,
        has_approval=result.has_approval,
    )
    save_hook_state(state)

    if not result.allowed:
        logger.warning(f"BLOCKED Task: {result.agent_name} - {result.reason}")
        return result.reason

    logger.info(f"ALLOWED Task: {result.agent_name}")
    return None


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


def has_stdin_data() -> bool:
    """Check if there's data available on stdin."""
    if sys.stdin.isatty():
        return False
    # Check if stdin has data available (non-blocking)
    try:
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(readable)
    except Exception:
        return not sys.stdin.isatty()


# ============================================================================
# STDIN HANDLER (Claude Code integration)
# ============================================================================

if __name__ == "__main__":
    # Check if running from CLI with arguments
    if len(sys.argv) > 1:
        main()
    elif has_stdin_data():
        try:
            stdin_data = sys.stdin.read()
            if not stdin_data.strip():
                print("Error: Empty stdin data")
                sys.exit(1)

            hook_data = json.loads(stdin_data)

            logger.info(f"Hook event: {hook_data.get('hook_event_name')}")

            tool_name = hook_data.get("tool_name", "")
            tool_input = hook_data.get("tool_input", {})
            command = tool_input.get("command", "")

            # Auto-approve read-only commands
            if tool_name.lower() == "bash" and command:
                is_safe, reason = is_read_only_command(command)
                if is_safe:
                    logger.info(f"AUTO-APPROVED: {command[:80]}... | {reason}")
                    print(create_permission_allow_response(f"Read-only: {reason}"))
                    sys.exit(0)

            # Standard validation
            result = pre_tool_use_hook(tool_name, tool_input)
            if result:
                print(result)
                sys.exit(1)
            else:
                sys.exit(0)

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error processing hook: {e}", exc_info=True)
            print(f"Hook error: {str(e)}")
            sys.exit(1)
    else:
        # No args and no stdin - show usage
        print("Usage: python pre_tool_use_v2.py <command>")
        print("       python pre_tool_use_v2.py --test")
        print("       echo '{\"tool_name\":\"bash\",\"tool_input\":{\"command\":\"ls\"}}' | python pre_tool_use_v2.py")
        sys.exit(1)
