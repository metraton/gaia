"""How OpenCode answers for each Gaia capability a Claude Code hook registration carries.

A capability is transported when it names the OpenCode bridge event that
carries it, excluded when it states why OpenCode needs no counterpart, and a
gap otherwise. ``tests/hooks/adapters/test_host_parity_alarm.py`` reads Claude
Code's registrations from ``build/gaia.manifest.json`` and fails on any gap
that ``PENDING_GAPS`` does not assign to the task that closes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Optional, Tuple

Registration = Tuple[str, str]


@dataclass(frozen=True)
class Capability:
    """A Gaia capability, the Claude Code registrations that carry it, and OpenCode's answer.

    A registration is ``(event, matcher token)``; an event registered with no
    matcher has the empty token.
    """

    name: str
    claude_code: FrozenSet[Registration]
    opencode_event: Optional[str] = None
    exclusion: Optional[str] = None

    @property
    def is_gap(self) -> bool:
        return not self.opencode_event and not self.exclusion


def _registrations(event: str, *tokens: str) -> FrozenSet[Registration]:
    return frozenset((event, token) for token in (tokens or ("",)))


_SESSION_SOURCES = ("startup", "resume", "clear", "compact", "fork")

CAPABILITIES: Tuple[Capability, ...] = (
    Capability(
        name="tool policy",
        claude_code=_registrations(
            "PreToolUse", "Bash", "Read", "Edit", "Write", "Glob", "Grep", "NotebookEdit",
        ),
        opencode_event="tool.execute.before",
    ),
    Capability(
        name="web tool policy",
        claude_code=_registrations("PreToolUse", "WebSearch", "WebFetch"),
        opencode_event="tool.execute.before",
    ),
    Capability(
        name="dispatch kernel",
        claude_code=_registrations("PreToolUse", "Task", "Agent")
        | _registrations("SubagentStart", "*"),
        opencode_event="tool.execute.before",
    ),
    Capability(
        name="signature question",
        claude_code=_registrations("PreToolUse", "AskUserQuestion")
        | _registrations("PostToolUse", "AskUserQuestion"),
        opencode_event="tool.execute.before",
    ),
    Capability(
        name="post-tool audit",
        claude_code=_registrations("PostToolUse", "Bash")
        | _registrations("PostToolUseFailure", "Bash"),
        opencode_event="tool.execute.after",
    ),
    Capability(
        name="child compaction kernel",
        claude_code=_registrations("PreCompact"),
        opencode_event="session.compacting",
    ),
    Capability(
        name="dispatch row close",
        claude_code=_registrations("SubagentStop", "*"),
        opencode_event="session.idle",
    ),
    Capability(
        name="SendMessage resume",
        claude_code=_registrations("PreToolUse", "SendMessage"),
        exclusion="OpenCode resumes a child session natively and has no SendMessage tool to validate.",
    ),
    Capability(
        name="TaskCompleted",
        claude_code=_registrations("TaskCompleted"),
        exclusion="Agent-teams event with no OpenCode counterpart; the Claude Code handler only passes through.",
    ),
    Capability(
        name="session birth block",
        claude_code=_registrations("SessionStart", "startup", "resume", "clear", "fork"),
        opencode_event="chat.message",
    ),
    Capability(
        name="startup maintenance",
        claude_code=_registrations("SessionStart", *_SESSION_SOURCES)
        | _registrations("SessionEnd")
        | _registrations("Stop"),
        opencode_event="chat.message",
    ),
    Capability(
        name="per-message heartbeat and notifications",
        claude_code=_registrations("UserPromptSubmit"),
        opencode_event="chat.prompt",
    ),
    Capability(
        name="primary compaction refresh",
        claude_code=_registrations("SessionStart", "compact") | _registrations("PostCompact"),
        opencode_event="session.compacting",
    ),
    Capability(
        name="contract summary after dispatch",
        claude_code=_registrations("PostToolUse", "Task"),
        opencode_event="tool.execute.after",
    ),
    Capability(
        name="session events digest",
        claude_code=_registrations("PreToolUse", "Task", "Agent")
        | _registrations("SubagentStart", "*"),
        opencode_event="tool.execute.before",
    ),
    Capability(
        name="SubagentStop gate and relay",
        claude_code=_registrations("SubagentStop", "*"),
    ),
    Capability(
        name="skills at birth",
        claude_code=_registrations("SubagentStart", "*"),
    ),
)

PENDING_GAPS: Dict[str, str] = {
    "SubagentStop gate and relay": "task 815 (T9)",
    "skills at birth": "task 816 (T10)",
}
