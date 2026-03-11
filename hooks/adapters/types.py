"""
Adapter Normalized Types for Gaia-Ops Hooks.

CLI-agnostic frozen dataclasses and enums consumed by business logic modules.
The adapter layer translates between these types and CLI-specific JSON protocols.

No dependencies on any existing gaia-ops module -- this is standalone.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional


class HookEventType(enum.Enum):
    """All Claude Code hook events as an enumeration."""

    # P0 - Currently implemented
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SUBAGENT_STOP = "SubagentStop"

    # P1
    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"

    # P2
    PERMISSION_REQUEST = "PermissionRequest"
    STOP = "Stop"
    TASK_COMPLETED = "TaskCompleted"
    SUBAGENT_START = "SubagentStart"

    # P3
    PRE_COMPACT = "PreCompact"
    CONFIG_CHANGE = "ConfigChange"
    SESSION_END = "SessionEnd"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"

    # P4
    NOTIFICATION = "Notification"


class PermissionDecision(enum.Enum):
    """Hook permission decision values."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class DistributionChannel(enum.Enum):
    """How gaia-ops was installed and is being invoked."""

    NPM = "npm"
    PLUGIN = "plugin"


@dataclass(frozen=True)
class HookEvent:
    """Normalized hook event, CLI-agnostic.

    Produced by the adapter's parse_event() method.
    """

    event_type: HookEventType
    session_id: str
    payload: Dict[str, Any]
    channel: DistributionChannel
    plugin_root: Optional[Path] = None


@dataclass(frozen=True)
class ValidationRequest:
    """Pre-tool-use validation request extracted from a HookEvent."""

    tool_name: str
    command: str
    tool_input: Dict[str, Any]
    session_id: str


@dataclass(frozen=True)
class ValidationResult:
    """CLI-agnostic validation result from business logic.

    Business logic produces this; the adapter formats it for the CLI.
    """

    allowed: bool = True
    reason: str = ""
    tier: str = "T0"
    modified_input: Optional[Dict[str, Any]] = None
    suggestions: List[str] = field(default_factory=list)
    nonce: Optional[str] = None


@dataclass(frozen=True)
class ToolResult:
    """Post-tool-use result data extracted from a HookEvent."""

    tool_name: str
    command: str
    output: str
    exit_code: int
    session_id: str


@dataclass(frozen=True)
class AgentCompletion:
    """Subagent completion data extracted from a HookEvent."""

    agent_type: str
    agent_id: str
    transcript_path: str
    last_message: str
    session_id: str


@dataclass(frozen=True)
class CompletionResult:
    """Result of processing an agent completion event."""

    contract_valid: bool = True
    episode_id: Optional[str] = None
    context_updated: bool = False
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    repair_needed: bool = False


@dataclass(frozen=True)
class ContextResult:
    """Result of context injection processing."""

    context_injected: bool = False
    additional_context: Optional[str] = None
    sections_provided: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class BootstrapResult:
    """Result of project bootstrap/scanning."""

    project_scanned: bool = False
    context_path: Optional[Path] = None
    tools_detected: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class HookResponse:
    """CLI-specific hook response. Constructed by adapter, not business logic."""

    output: Dict[str, Any]
    exit_code: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary suitable for JSON output."""
        return {"output": self.output, "exit_code": self.exit_code}
