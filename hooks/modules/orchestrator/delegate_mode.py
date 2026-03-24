"""Orchestrator delegate mode enforcement.

When enabled, the orchestrator (main session) is restricted to dispatch-only
tools. Direct investigation tools (Bash, Read, Edit, etc.) are blocked so the
orchestrator must delegate to specialist agents.

Detection: Claude Code includes ``agent_id`` and ``agent_type`` in the
PreToolUse payload ONLY when the hook fires inside a subagent. Their absence
means the call originates from the main session (orchestrator).

Configuration (checked in order):
1. ``ORCHESTRATOR_DELEGATE_MODE`` env var (set via settings.json ``env`` block
   or shell export before launching Claude Code).
2. settings.json ``env.ORCHESTRATOR_DELEGATE_MODE`` read directly from disk
   (fallback when Claude Code does not propagate the var to hook subprocesses).
3. settings.json ``environment.ORCHESTRATOR_DELEGATE_MODE`` (legacy key name;
   Claude Code only recognises ``env``, not ``environment``).
4. Default: disabled.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
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


def _read_settings_delegate_mode() -> Optional[str]:
    """Read ORCHESTRATOR_DELEGATE_MODE from settings.json on disk.

    Checks both ``env`` (correct Claude Code key) and ``environment``
    (legacy gaia-ops key) blocks.  Returns the raw string value or None.
    """
    try:
        # Walk upward from cwd to find .claude/settings.json
        current = Path.cwd()
        for base in [current] + list(current.parents):
            settings_path = base / ".claude" / "settings.json"
            if settings_path.is_file():
                with open(settings_path, "r") as f:
                    data = json.load(f)

                # Preferred key: "env" (Claude Code native)
                env_block = data.get("env")
                if isinstance(env_block, dict):
                    val = env_block.get("ORCHESTRATOR_DELEGATE_MODE")
                    if val is not None:
                        logger.debug(
                            "delegate_mode: found in settings.json env block: %r",
                            val,
                        )
                        return str(val)

                # Legacy key: "environment" (gaia-ops custom, not read by
                # Claude Code as env vars)
                env_block = data.get("environment")
                if isinstance(env_block, dict):
                    val = env_block.get("ORCHESTRATOR_DELEGATE_MODE")
                    if val is not None:
                        logger.debug(
                            "delegate_mode: found in settings.json environment block (legacy): %r",
                            val,
                        )
                        return str(val)

                # settings.json found but key absent
                return None
    except Exception as exc:
        logger.debug("delegate_mode: failed reading settings.json: %s", exc)
    return None


def is_delegate_mode_enabled() -> bool:
    """Check if orchestrator delegate mode is enabled.

    Sources (checked in order):
    1. ORCHESTRATOR_DELEGATE_MODE env var
    2. settings.json ``env.ORCHESTRATOR_DELEGATE_MODE`` (disk fallback)
    3. settings.json ``environment.ORCHESTRATOR_DELEGATE_MODE`` (legacy)
    4. Default: False (disabled)
    """
    # 1. Environment variable (fast path)
    value = os.environ.get("ORCHESTRATOR_DELEGATE_MODE", "").lower().strip()
    if value:
        enabled = value in ("1", "true", "yes", "on")
        logger.debug("delegate_mode: env var=%r -> enabled=%s", value, enabled)
        return enabled

    # 2-3. Fallback: read settings.json from disk
    disk_value = _read_settings_delegate_mode()
    if disk_value is not None:
        enabled = disk_value.lower().strip() in ("1", "true", "yes", "on")
        logger.debug(
            "delegate_mode: settings.json value=%r -> enabled=%s",
            disk_value,
            enabled,
        )
        return enabled

    logger.debug("delegate_mode: not configured, defaulting to disabled")
    return False


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
    enabled = is_delegate_mode_enabled()
    if not enabled:
        logger.debug(
            "delegate_mode check: SKIP (disabled) tool=%s", tool_name,
        )
        return DelegateModeResult(blocked=False)

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
            f"[DELEGATE MODE] Tool '{tool_name}' is not available to the orchestrator.\n\n"
            f"As the orchestrator, you must delegate work to specialist agents.\n"
            f"Use the Agent tool to dispatch to the appropriate agent, or use\n"
            f"SendMessage to resume an existing agent.\n\n"
            f"Allowed orchestrator tools: Agent, SendMessage, Skill, TaskCreate, "
            f"TaskUpdate, TaskList, TaskGet, ToolSearch, WebSearch, WebFetch, "
            f"AskUserQuestion.\n\n"
            f"Do NOT attempt to use {tool_name} directly. Identify the right\n"
            f"specialist agent and delegate the work."
        ),
    )
