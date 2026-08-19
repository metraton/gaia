"""
Adapter Normalized Types for Gaia Hooks.

CLI-agnostic frozen dataclasses and enums consumed by business logic modules.
The adapter layer translates between these types and CLI-specific JSON protocols.

No dependencies on any existing Gaia module -- this is standalone.
"""

from __future__ import annotations

import enum
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class HookEventType(enum.Enum):
    """Normalized lifecycle events used by the host adapters.

    The original member values remain Claude Code's event names for backwards
    compatibility. Other hosts map their native events onto the same lifecycle
    points instead of teaching policy modules a second vocabulary.
    """

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
    POST_COMPACT = "PostCompact"
    CONFIG_CHANGE = "ConfigChange"
    SESSION_END = "SessionEnd"
    INSTRUCTIONS_LOADED = "InstructionsLoaded"
    POST_TOOL_USE_FAILURE = "PostToolUseFailure"

    # P4
    NOTIFICATION = "Notification"

    # P5 - Additional events
    TEAMMATE_IDLE = "TeammateIdle"
    WORKTREE_CREATE = "WorktreeCreate"
    WORKTREE_REMOVE = "WorktreeRemove"
    PROMPT_SUBMIT = "PromptSubmit"  # Deprecated alias for USER_PROMPT_SUBMIT


class PermissionDecision(enum.Enum):
    """Hook permission decision values."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


CONSENT_PROTOCOL_VERSION = "1"


def _command_fingerprint(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RoleCapabilityContext:
    """Attested execution context carried across an adapter boundary.

    ``role`` and ``capabilities`` are runtime claims, not prompt metadata.  The
    adapter may transport them, but only a trusted runtime/CLI verifier may
    mark the context verified.  Keeping that distinction in the value object
    prevents a host from turning a free-form agent message into authority.
    """

    role: str
    capabilities: tuple[str, ...] = ()
    issuer: str = ""
    attestation: str = ""
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.role or not isinstance(self.role, str):
            raise ValueError("role must be a non-empty string")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("capabilities must not contain duplicates")
        if any(not isinstance(capability, str) or not capability for capability in self.capabilities):
            raise ValueError("capabilities must contain non-empty strings")

    @property
    def is_verified_control_plane(self) -> bool:
        return self.verified and self.role == "gaia-orchestrator" and bool(self.attestation)


@dataclass(frozen=True)
class ConsentBinding:
    """Immutable correlation identity for one consent attempt."""

    agent_id: str
    session_id: str
    call_id: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (
            self.agent_id, self.session_id, self.call_id
        )):
            raise ValueError("agent_id, session_id, and call_id are required")


@dataclass(frozen=True)
class ConsentRequestEnvelope:
    """Harness-neutral, versioned request sealed before native presentation."""

    correlation_id: str
    operation: str
    commands: tuple[str, ...]
    scope: str
    impact: str
    risk: str
    rollback: str
    verification: str
    binding: ConsentBinding
    role_context: RoleCapabilityContext
    approval_id: str | None = None
    protocol_version: str = CONSENT_PROTOCOL_VERSION
    fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.protocol_version != CONSENT_PROTOCOL_VERSION:
            raise ValueError(f"unsupported consent protocol version: {self.protocol_version}")
        if not self.correlation_id or not self.operation:
            raise ValueError("correlation_id and operation are required")
        if not self.commands or any(not isinstance(command, str) or not command for command in self.commands):
            raise ValueError("commands must contain at least one exact command")
        expected = tuple(_command_fingerprint(command) for command in self.commands)
        if self.fingerprints and self.fingerprints != expected:
            raise ValueError("command fingerprints do not match exact command bytes")
        object.__setattr__(self, "fingerprints", expected)
        for name in ("scope", "impact", "risk", "rollback", "verification"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")

    def canonical_payload(self) -> str:
        return json.dumps({
            "protocol_version": self.protocol_version,
            "correlation_id": self.correlation_id,
            "approval_id": self.approval_id,
            "operation": self.operation,
            "commands": list(self.commands),
            "fingerprints": list(self.fingerprints),
            "scope": self.scope,
            "impact": self.impact,
            "risk": self.risk,
            "rollback": self.rollback,
            "verification": self.verification,
            "binding": self.binding.__dict__,
            "role_context": {
                "role": self.role_context.role,
                "capabilities": list(self.role_context.capabilities),
                "issuer": self.role_context.issuer,
                "attestation": self.role_context.attestation,
                "verified": self.role_context.verified,
            },
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ConsentDecision(enum.Enum):
    ONCE = "once"
    REJECT = "reject"
    ALWAYS = "always"


@dataclass(frozen=True)
class ConsentDecisionReceived:
    """Normalized decision emitted exactly once by any host adapter."""

    correlation_id: str
    decision: ConsentDecision
    binding: ConsentBinding
    request_fingerprint: str
    protocol_version: str = CONSENT_PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.protocol_version != CONSENT_PROTOCOL_VERSION:
            raise ValueError(f"unsupported consent protocol version: {self.protocol_version}")
        if not self.correlation_id or not self.request_fingerprint:
            raise ValueError("correlation_id and request_fingerprint are required")


@dataclass(frozen=True)
class ConsentResult:
    """Neutral outcome of decision handling; grant activation is explicit."""

    correlation_id: str
    status: str
    grant_activated: bool = False
    reserved_index: int | None = None
    frozen: bool = False
    reason: str = ""
    protocol_version: str = CONSENT_PROTOCOL_VERSION


@dataclass(frozen=True)
class HostDistribution:
    """How a host distributes and invokes gaia -- declared by the adapter.

    The CLI-agnostic value object that replaces a core-owned enumeration of
    distribution channels. The core never enumerates a host's channels nor
    names a host-specific "root"; it carries an opaque :class:`HostDistribution`
    that the concrete adapter PRODUCES from its own distribution model (see
    :meth:`HookAdapter.detect_distribution`). A host with a distribution model
    the core has never heard of declares it here without any change to the core
    vocabulary -- the same seam established for capabilities
    (:class:`HostCapability`) and consent (:class:`ConsentRequest`).

    The concrete channel names (Claude Code's "npm" / "plugin") and the meaning
    of ``root`` (Claude Code's plugin root) live ONLY in the host's adapter; the
    core treats both as opaque values.

    Fields:
        channel: The host's own opaque channel identifier (e.g. Claude Code
            uses "npm" / "plugin"). The core compares or logs it but never
            branches on a value it enumerated -- the host owns the vocabulary.
        root: The distribution root for this channel when the host has the
            notion of one (Claude Code: the plugin root directory); ``None``
            when the channel has no such root. The core does not interpret it.
    """

    channel: str
    root: Optional[Path] = None


class HostCapability(enum.Enum):
    """A named capability a host (CLI backend) may or may not offer.

    The CLI-agnostic vocabulary business logic uses to ASK a host whether it
    can do a thing -- without naming any host. Each concrete adapter DECLARES
    which of these it supports (see :meth:`HookAdapter.capabilities`); the core
    queries that declaration via :meth:`HookAdapter.supports` and, when a
    capability is absent, degrades in a *declared* way (a
    :class:`CapabilityDegradation`) rather than crashing or branching on the
    host's identity. Claude Code supports all of these today; a future host
    (Codex, Antigravity) that lacks one drives the degradation path.

    Members:
        INTERACTIVE_CONSENT: the host can gather the user's consent inline,
            in-session (Claude Code: the native AskUserQuestion prompt).
        OUT_OF_BAND_APPROVAL: the host can run an approval cycle keyed to a
            persisted identifier the decision is later matched against
            (Claude Code: the orchestrator approval-id hand-off).
        STRUCTURED_PERMISSION_DECISION: the host accepts a structured
            allow/deny/ask permission decision (vs. only an exit code).
        UPDATED_INPUT: the host can apply adapter-modified tool input
            transparently (e.g. a footer-stripped command).
        CONTEXT_INJECTION: the host can inject additional context into the
            session at hook time (SessionStart / SubagentStart context).
        TRANSCRIPT_ACCESS: the host exposes the agent transcript for
            post-hoc inspection (contract / anomaly analysis).
    """

    INTERACTIVE_CONSENT = "interactive_consent"
    OUT_OF_BAND_APPROVAL = "out_of_band_approval"
    STRUCTURED_PERMISSION_DECISION = "structured_permission_decision"
    UPDATED_INPUT = "updated_input"
    CONTEXT_INJECTION = "context_injection"
    TRANSCRIPT_ACCESS = "transcript_access"


@dataclass(frozen=True)
class HookEvent:
    """Normalized hook event, CLI-agnostic.

    Produced by the adapter's parse_event() method.

    ``distribution`` is the host-declared :class:`HostDistribution` (the host's
    own channel and, when applicable, its distribution root). It replaces the
    former ``channel`` / ``plugin_root`` pair so the core never enumerates a
    host's distribution channels. It is ``None`` when the adapter does not
    declare a distribution model for the event.
    """

    event_type: HookEventType
    session_id: str
    payload: Dict[str, Any]
    distribution: Optional[HostDistribution] = None
    host_agent_id: Optional[str] = None
    dispatch_id: Optional[str] = None
    parent_dispatch_id: Optional[str] = None
    call_id: Optional[str] = None


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
class ConsentRequest:
    """CLI-agnostic description of an operation that needs the user's consent.

    Business logic produces this when it has classified an operation as
    requiring approval (a T3 mutation, a protected-path write). It states only
    the *facts* of what needs consent -- never how to ask. The adapter's
    :meth:`HookAdapter.request_consent` turns it into the host's consent
    mechanism (a native permission prompt, an approval-id hand-off, ...), so the
    core never names the host's specific consent flow.

    Fields:
        operation: The thing needing consent -- a shell command, or a file path.
        kind: Coarse classification of ``operation`` ("bash", "file", ...).
            Lets the adapter tailor the prompt wording without parsing.
        reason: Human-readable explanation of why consent is required, already
            assembled by business logic (tier banner, verb, command excerpt).
        tier: The security tier string (e.g. "T3_BLOCKED"); informational.
        approval_id: When the host runs an out-of-band approval flow (an
            orchestrator driving the approval cycle), this is the persisted
            identifier the user's decision is keyed to. None means the host
            should gather consent inline (e.g. a native prompt).
        updated_input: Optional modified tool input (e.g. footer-stripped
            command) the host must preserve through the consent step.
    """

    operation: str
    kind: str = "bash"
    reason: str = ""
    tier: str = "T3_BLOCKED"
    approval_id: Optional[str] = None
    updated_input: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class CapabilityDegradation:
    """The DECLARED outcome of querying a host for a capability.

    Returned by :meth:`HookAdapter.degrade_when_missing`. It is the explicit,
    observable answer to "does this host offer capability X, and if not, what
    safe thing happens instead?" -- the controlled alternative to a crash or an
    implicit ``if host == "claude_code"`` branch. Business logic receives this
    value, reads :attr:`available`, and follows :attr:`fallback` when the
    capability is absent. Nothing here knows which host produced it.

    Fields:
        capability: The :class:`HostCapability` that was queried.
        available: True when the host declared support for ``capability``.
            When True, ``fallback`` is the empty string and ``reason`` is
            informational only -- the caller uses the full capability.
        fallback: The semantic name of the safe behavior to take when the
            capability is NOT available (e.g. "deny", "skip", "log_only").
            Chosen by the caller and echoed back so the degradation is a
            value the caller declared, not a side effect it must remember.
        reason: Human-readable explanation of the degradation, suitable for
            surfacing in a log or a denial message.
    """

    capability: HostCapability
    available: bool
    fallback: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ToolResult:
    """Post-tool-use result data extracted from a HookEvent."""

    tool_name: str
    command: str
    output: str
    exit_code: int
    session_id: str
    call_id: Optional[str] = None


@dataclass(frozen=True)
class AgentCompletion:
    """Subagent completion data extracted from a HookEvent."""

    agent_type: str
    agent_id: str
    transcript_path: str
    last_message: str
    session_id: str
    dispatch_id: Optional[str] = None
    parent_session_id: Optional[str] = None


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
    # Reserved for future adapter use
    prompt_text: str = ""


@dataclass(frozen=True)
class BootstrapResult:
    """Result of project bootstrap/scanning."""

    project_scanned: bool = False
    context_path: Optional[Path] = None
    tools_detected: List[str] = field(default_factory=list)
    # P1 fields: SessionStart adapter populates these
    should_scan: bool = False
    should_refresh: bool = False
    session_type: str = "startup"


@dataclass(frozen=True)
class QualityResult:
    """Result of a Stop event -- whether evidence quality meets threshold."""

    quality_sufficient: bool = True
    score: float = 1.0
    missing_elements: List[str] = field(default_factory=list)
    recommendation: str = "continue"


@dataclass(frozen=True)
class VerificationResult:
    """Result of a TaskCompleted event -- whether completion criteria are met."""

    criteria_met: bool = True
    verified_items: List[str] = field(default_factory=list)
    failed_items: List[str] = field(default_factory=list)
    block_completion: bool = False


@dataclass(frozen=True)
class HookResponse:
    """CLI-specific hook response. Constructed by adapter, not business logic."""

    output: Dict[str, Any]
    exit_code: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dictionary suitable for JSON output."""
        return {"output": self.output, "exit_code": self.exit_code}
