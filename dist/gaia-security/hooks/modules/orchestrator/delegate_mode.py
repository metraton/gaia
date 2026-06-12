"""Orchestrator delegate mode enforcement.

When GAIA is installed, delegate mode is always active. The orchestrator
(main session) is restricted to dispatch-only tools. Direct investigation
tools (Bash, Read, Edit, etc.) are blocked so the orchestrator must
delegate to specialist agents.

Detection: Claude Code includes ``agent_id`` and ``agent_type`` in the
PreToolUse payload ONLY when the hook fires inside a subagent. Their absence
means the call originates from the main session (orchestrator).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Tools the orchestrator is allowed to use in delegate mode.
# Everything NOT in this set is blocked for the main session.
ORCHESTRATOR_ALLOWED_TOOLS = frozenset({
    # Dispatch and communication
    "agent",
    "task",
    "sendmessage",

    # On-demand skills / procedures
    "skill",

    # Agent teams task management
    "taskcreate",
    "taskupdate",
    "tasklist",
    "taskget",

    # Tool discovery
    "toolsearch",

    # Web research (read-only, T0)
    "websearch",
    "webfetch",

    # User interaction (built-in, may not always trigger hooks)
    "askuserquestion",
})


@dataclass(frozen=True)
class DelegateModeResult:
    """Result of delegate mode check."""

    blocked: bool
    reason: Optional[str] = None


def is_orchestrator_context(hook_payload: Dict[str, Any]) -> bool:
    """Determine if the hook is firing in the main session (orchestrator).

    Claude Code includes ``agent_id`` in the PreToolUse payload only when
    the tool call originates from a subagent. Its absence means the call
    is from the main session.

    Args:
        hook_payload: The full stdin JSON dict from Claude Code.

    Returns:
        True if this is the orchestrator (main session), False if subagent.
    """
    agent_id = hook_payload.get("agent_id")
    # agent_id is absent or empty string for the main session
    return not agent_id


def check_delegate_mode(
    tool_name: str, hook_payload: Dict[str, Any]
) -> DelegateModeResult:
    """Check whether a tool call should be blocked by delegate mode.

    This is the single entry point. Call it early in the PreToolUse flow.

    Args:
        tool_name: The tool being invoked (e.g., "Bash", "Read", "Edit").
        hook_payload: The full stdin JSON dict from Claude Code.

    Returns:
        DelegateModeResult with blocked=True and a reason if the call
        should be denied, or blocked=False if it should proceed.
    """
    is_orchestrator = is_orchestrator_context(hook_payload)
    if not is_orchestrator:
        # Subagents have full tool access -- delegate mode does not apply
        agent_id = hook_payload.get("agent_id", "<none>")
        logger.debug(
            "delegate_mode check: SKIP (subagent %s) tool=%s",
            agent_id,
            tool_name,
        )
        return DelegateModeResult(blocked=False)

    normalized = tool_name.lower().strip()
    if normalized in ORCHESTRATOR_ALLOWED_TOOLS:
        logger.debug(
            "delegate_mode check: ALLOW (orchestrator allowed tool) tool=%s",
            tool_name,
        )
        return DelegateModeResult(blocked=False)

    logger.warning(
        "DELEGATE_MODE blocked tool '%s' for orchestrator (main session)",
        tool_name,
    )

    return DelegateModeResult(
        blocked=True,
        reason=(
            f"DELEGATION REQUIRED: '{tool_name}' is not available.\n"
            f"Dispatch a specialist agent for this task.\n"
            f"The routing recommendation in your last message indicates which agent to use."
        ),
    )
