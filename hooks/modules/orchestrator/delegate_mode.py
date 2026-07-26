"""Orchestrator delegate mode enforcement.

When GAIA is installed, delegate mode is always active. The orchestrator
(main session) is restricted to dispatch tools plus Read. Mutative and
bulk-investigation tools (Bash, Edit, Write, Glob, Grep, etc.) are blocked
so the orchestrator must delegate to specialist agents; Read (read-only,
T0) is allowed so the orchestrator can triangulate evidence with the user
-- validate a document or an image against a specialist's contract.
Delegate-first remains an identity instruction, not a hook lock.

Detection is a taxonomy over the TWO identity fields the harness provides,
not the presence of one of them. Claude Code documents them verbatim:

    agent_id   -- "Subagent identifier. Present only when the hook fires from
                  within a subagent (e.g., a tool called by an AgentTool
                  worker). Absent for the main thread, even in --agent
                  sessions. Use this field (not agent_type) to distinguish
                  subagent calls from main-thread calls."
    agent_type -- "Agent type name (e.g., "general-purpose",
                  "code-reviewer"). Present when the hook fires from within a
                  subagent (alongside agent_id), or on the main thread of a
                  session started with --agent (without agent_id)."

So ``agent_id`` alone answers "am I a dispatched subagent?" -- it does NOT
answer "am I the orchestrator?". A main thread started with ``--agent
developer`` has no ``agent_id`` and is not the orchestrator; reading the
absence of the id as orchestrator-ness denied Bash to the very specialist the
user named. ``classify_session_role`` crosses both fields instead, and the
orchestrator is identified by NAME (``ORCHESTRATOR_AGENT_TYPES``) because
Gaia's own installer selects it through the settings ``agent`` field, which
the harness treats exactly like ``--agent``: the orchestrator's own main
thread therefore also arrives carrying an ``agent_type``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
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

    # Direct evidence reading (read-only, T0). Lets the orchestrator
    # triangulate with the user -- validate a document or an image
    # (e.g. a Playwright screenshot) against a specialist's contract.
    # Governed by identity instruction (delegate-first); Bash, Edit,
    # Write, Glob, and Grep remain blocked by absence from this set.
    "read",
})


# Main-thread agent identities that ARE the orchestrator. Gaia's installer
# writes ``agent: gaia-orchestrator`` into settings.local.json
# (bin/cli/_install_helpers.py, enforced by `gaia doctor`), so the orchestrator
# reaches this module as a NAMED main thread, indistinguishable by shape from a
# named specialist -- only the name separates them. Both spellings are listed,
# mirroring the identity sets in gaia/store/writer.py and
# gaia/state/permissions.py. Those sets are NOT reused here: they enumerate
# curators (orchestrator + operator), and gaia-operator is a specialist that
# must keep its tools.
ORCHESTRATOR_AGENT_TYPES = frozenset({
    "orchestrator",
    "gaia-orchestrator",
})


class SessionRole(str, Enum):
    """Who is calling, over the harness's (agent_id, agent_type) pair.

    ORCHESTRATOR is the only role delegate mode restricts.
    """

    SUBAGENT = "subagent"                    # agent_id present: a dispatch
    ORCHESTRATOR = "orchestrator"            # main thread, unnamed or named as the orchestrator
    NAMED_SPECIALIST = "named_specialist"    # main thread of a --agent <specialist> session

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class DelegateModeResult:
    """Result of delegate mode check."""

    blocked: bool
    reason: Optional[str] = None


def classify_session_role(hook_payload: Dict[str, Any]) -> SessionRole:
    """Classify the caller from the harness's two identity fields.

    Args:
        hook_payload: The full stdin JSON dict from Claude Code.

    Returns:
        The :class:`SessionRole` for this call. An unrecognized ``agent_type``
        on a main thread is a NAMED_SPECIALIST, not the orchestrator: the
        orchestrator's identity is known by name, so a name that is not it
        cannot be it.
    """
    if hook_payload.get("agent_id"):
        return SessionRole.SUBAGENT

    agent_type = (hook_payload.get("agent_type") or "").strip().lower()
    if not agent_type or agent_type in ORCHESTRATOR_AGENT_TYPES:
        return SessionRole.ORCHESTRATOR
    return SessionRole.NAMED_SPECIALIST


def is_orchestrator_context(hook_payload: Dict[str, Any]) -> bool:
    """Whether this call comes from the orchestrator itself.

    Args:
        hook_payload: The full stdin JSON dict from Claude Code.

    Returns:
        True only for :attr:`SessionRole.ORCHESTRATOR`. A dispatched subagent
        and a ``--agent <specialist>`` main thread both return False.
    """
    return classify_session_role(hook_payload) is SessionRole.ORCHESTRATOR


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
    role = classify_session_role(hook_payload)
    if role is not SessionRole.ORCHESTRATOR:
        # Dispatched subagents and named-specialist main threads both have full
        # tool access -- delegate mode does not apply to either.
        logger.debug(
            "delegate_mode check: SKIP (role=%s agent=%s) tool=%s",
            role,
            hook_payload.get("agent_type") or hook_payload.get("agent_id") or "<none>",
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
