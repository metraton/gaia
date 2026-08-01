"""Orchestrator delegate mode enforcement.

When GAIA is installed, delegate mode is always active. Both non-dispatched
roles -- the orchestrator's own main thread AND a main thread started with
``--agent <specialist>`` -- are restricted to dispatch tools plus Read.
Mutative and bulk-investigation tools (Bash, Edit, Write, Glob, Grep, etc.)
are blocked; Read (read-only, T0) is allowed so either can triangulate
evidence with the user -- validate a document or an image against a
specialist's contract. Delegate-first remains an identity instruction, not a
hook lock.

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
developer`` has no ``agent_id`` and is not the orchestrator; the old binary
proxy (``not agent_id``) read that absence as orchestrator-ness.
``classify_session_role`` crosses both fields into three roles instead, and
the orchestrator is identified by NAME (``ORCHESTRATOR_AGENT_TYPES``)
because Gaia's own installer selects it through the settings ``agent``
field, which the harness treats exactly like ``--agent``: the
orchestrator's own main thread therefore also arrives carrying an
``agent_type``.

Distinguishing the three roles is a true statement about the harness's shape
and stays. What to DO with a NAMED_SPECIALIST main thread is a separate
decision: this module denies it, same as the orchestrator, because a
``--agent <specialist>`` session runs OUTSIDE the real dispatch path. The
per-agent, filtered project-context injection is built only when the
orchestrator actually dispatches (`modules.context.context_injector`) --
a `--agent` session never enters that path, so a named specialist granted
tools here would run WITH tools but WITHOUT the context a genuine dispatch
provides, which is worse than being blocked. Gaia's own design treats this
mode as out of scope: agents are tested and used through the orchestrator,
never invoked standalone. So NAMED_SPECIALIST is denied like ORCHESTRATOR,
but under its OWN reason -- it must never receive the orchestrator's
"delegate to a specialist" message, which would tell a specialist to
delegate to itself.
"""

from __future__ import annotations

import logging
import re
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

# Adapter-owned marker used to select a host-specific tool vocabulary without
# teaching the Claude policy about Codex names.  CodexAdapter adds this only to
# the in-process normalized payload; it is never trusted from tool_input.
HOST_MARKER_KEY = "_gaia_host"
CODEX_HOST = "codex"

# Codex reports local function tools by their canonical function name.  Tool
# namespaces may be flattened by the runtime (``collaboration.spawn_agent`` ->
# ``collaborationspawn_agent``), so membership uses ``normalize_tool_name``.
# This list is intentionally capability-shaped and closed: coordination,
# thread-local planning/state, tool discovery, and direct evidence reads.
CODEX_ALLOWED_TOOLS = frozenset({
    # Dispatch and coordination.  Keep the bare spellings too: Codex documents
    # spawn_agent as matching the Agent alias, and installations vary in
    # whether the collaboration namespace is present in hook input.
    "agent",
    "task",
    "spawnagent",
    "collaborationspawnagent",
    "sendinput",
    "sendmessage",
    "collaborationsendmessage",
    "collaborationfollowuptask",
    "collaborationlistagents",
    "collaborationwaitagent",
    "collaborationinterruptagent",

    # Turn-local planning and goal state.
    "updateplan",
    "getgoal",
    "creategoal",
    "updategoal",

    # Skills, discovery, and user interaction.
    "skill",
    "toolsearch",
    "askuserquestion",
    "requestuserinput",

    # Direct evidence reads.
    "viewimage",
    "listmcpresources",
    "listmcpresourcetemplates",
    "readmcpresource",
    "webrun",
    "websearch",
    "webfetch",

    # Code mode has no direct side effects; every nested tool call traverses
    # PreToolUse again.  Allowing the executor therefore does not bypass this
    # gate.
    "functionsexec",
    "functionswait",
})

CODEX_DELEGATION_TOOLS = frozenset({
    "agent",
    "task",
    "spawnagent",
    "collaborationspawnagent",
})

_READ_ONLY_MCP_VERBS = frozenset({
    "check",
    "count",
    "describe",
    "fetch",
    "find",
    "get",
    "inspect",
    "list",
    "locate",
    "lookup",
    "open",
    "preview",
    "query",
    "read",
    "search",
    "show",
    "view",
})

_MUTATIVE_MCP_TOKENS = frozenset({
    "add",
    "apply",
    "approve",
    "archive",
    "cancel",
    "close",
    "copy",
    "create",
    "delete",
    "deploy",
    "draft",
    "edit",
    "execute",
    "forward",
    "install",
    "label",
    "merge",
    "move",
    "patch",
    "post",
    "publish",
    "put",
    "reject",
    "remove",
    "reply",
    "resolve",
    "run",
    "send",
    "set",
    "start",
    "stop",
    "trash",
    "trigger",
    "uninstall",
    "update",
    "upload",
    "write",
})


def normalize_tool_name(tool_name: str) -> str:
    """Return a case-insensitive name stable across host separators."""
    return re.sub(r"[^a-z0-9]+", "", str(tool_name).casefold())


def _is_read_only_codex_mcp_tool(tool_name: str) -> bool:
    """Conservatively recognize a read-only MCP/app operation by its leaf name.

    Hook input does not expose MCP annotations.  Gaia therefore allows only
    explicit read verbs and rejects any leaf containing a known mutative token.
    Unknown or malformed MCP names stay fail-closed.
    """
    lowered = str(tool_name).strip().casefold()
    if not lowered.startswith("mcp__"):
        return False
    leaf = lowered.rsplit("__", 1)[-1]
    tokens = tuple(token for token in re.split(r"[^a-z0-9]+", leaf) if token)
    if not tokens or tokens[0] not in _READ_ONLY_MCP_VERBS:
        return False
    return not any(token in _MUTATIVE_MCP_TOKENS for token in tokens)


def _tool_is_allowed(tool_name: str, hook_payload: Dict[str, Any]) -> bool:
    normalized = normalize_tool_name(tool_name)
    if hook_payload.get(HOST_MARKER_KEY) == CODEX_HOST:
        return (
            normalized in CODEX_ALLOWED_TOOLS
            or _is_read_only_codex_mcp_tool(tool_name)
        )
    return normalized in ORCHESTRATOR_ALLOWED_TOOLS


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

    SUBAGENT is the only role delegate mode leaves unrestricted. ORCHESTRATOR
    and NAMED_SPECIALIST are both gated -- a --agent main thread runs outside
    the real dispatch path and never receives the per-agent context a
    genuine dispatch builds -- but each is denied under its own, distinct
    reason (see ``check_delegate_mode``).
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
        should be denied, or blocked=False if it should proceed. A
        SUBAGENT call always proceeds; ORCHESTRATOR and NAMED_SPECIALIST
        are gated by the same ``ORCHESTRATOR_ALLOWED_TOOLS`` set but each
        carries its own, truthful denial reason.
    """
    role = classify_session_role(hook_payload)
    if role is SessionRole.SUBAGENT:
        # A dispatched subagent has full tool access -- delegate mode does
        # not apply. This is the only role it skips.
        logger.debug(
            "delegate_mode check: SKIP (role=%s agent=%s) tool=%s",
            role,
            hook_payload.get("agent_type") or hook_payload.get("agent_id") or "<none>",
            tool_name,
        )
        return DelegateModeResult(blocked=False)

    if _tool_is_allowed(tool_name, hook_payload):
        logger.debug(
            "delegate_mode check: ALLOW (role=%s allowed tool) tool=%s",
            role,
            tool_name,
        )
        return DelegateModeResult(blocked=False)

    if role is SessionRole.NAMED_SPECIALIST:
        logger.warning(
            "DELEGATE_MODE blocked tool '%s' for named specialist (main thread, "
            "agent_type=%s)",
            tool_name,
            hook_payload.get("agent_type"),
        )
        return DelegateModeResult(
            blocked=True,
            reason=(
                f"NOT RUNNABLE STANDALONE: '{tool_name}' is not available.\n"
                f"This agent runs only under a genuine orchestrator dispatch. "
                f"The per-agent, filtered project-context injection is built "
                f"solely on that dispatch path -- a --agent main thread never "
                f"enters it, so running here would give this specialist tools "
                f"without the context a real dispatch provides.\n"
                f"Ask the orchestrator to dispatch this agent instead."
            ),
        )

    logger.warning(
        "DELEGATE_MODE blocked tool '%s' for orchestrator (main session)",
        tool_name,
    )

    return DelegateModeResult(
        blocked=True,
        reason=(
            f"DELEGATION REQUIRED: '{tool_name}' is not available.\n"
            f"Dispatch a specialist agent for this task with "
            f"{'collaboration.spawn_agent' if hook_payload.get(HOST_MARKER_KEY) == CODEX_HOST else 'Agent/Task'}."
        ),
    )
