"""
Abstract base class defining the adapter contract.

Each CLI backend (Claude Code, future CLIs) provides a concrete implementation
of HookAdapter. Business logic modules interact only with the normalized types;
they never see raw CLI JSON.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import FrozenSet

from .types import (
    AgentCompletion,
    BootstrapResult,
    CapabilityDegradation,
    CompletionResult,
    ConsentRequest,
    ContextResult,
    HookEvent,
    HookResponse,
    HostCapability,
    HostDistribution,
    QualityResult,
    ValidationResult,
    VerificationResult,
)


class HookAdapter(ABC):
    """Abstract adapter between CLI-specific JSON and normalized types.

    Invariants (from adapter-interface contract):
    1. Business logic modules NEVER see HookResponse.
    2. The adapter NEVER modifies business logic results -- only translates format.
    3. Adding a new hook event requires ONLY a new adapter method.
    """

    @abstractmethod
    def parse_event(self, stdin_data: str) -> HookEvent:
        """Parse raw stdin JSON into a normalized HookEvent.

        Preconditions:
            - stdin_data is a valid JSON string
            - JSON contains at minimum: hook_event_name, session_id

        Postconditions:
            - Returns HookEvent with event_type set to a valid HookEventType
            - Returns HookEvent with session_id populated
            - payload contains the full raw event data

        Raises:
            ValueError: If JSON is invalid or event type is unknown.
        """
        ...

    @abstractmethod
    def format_validation_response(self, result: ValidationResult) -> HookResponse:
        """Format a ValidationResult for CLI consumption.

        Preconditions:
            - result.allowed is a valid boolean
            - result.reason is a non-empty string

        Postconditions:
            - HookResponse.output is a valid JSON-serializable dict
            - HookResponse.exit_code is 0 (corrective deny) or 2 (permanent block)
            - If result.allowed is True, output contains permissionDecision: allow
            - If result.allowed is False, output contains permissionDecision: deny
            - If result.modified_input is set, output contains updatedInput
        """
        ...

    @abstractmethod
    def request_consent(self, request: ConsentRequest) -> HookResponse:
        """Ask the user to consent to an operation, via the host's mechanism.

        The single entry point through which business logic requests user
        consent for an operation it has classified as approval-requiring (a T3
        mutation, a protected-path write). Business logic hands over the
        CLI-agnostic facts (:class:`ConsentRequest`) and never names how the
        host gathers consent: the concrete adapter owns that mechanism entirely
        (a native permission prompt, an out-of-band approval-id hand-off, ...).
        Adding or changing the host's consent flow is a change to this method
        alone -- the core's tier classification, grants, and validation stay
        untouched.

        Preconditions:
            - request.operation is a non-empty string
            - request.reason is the human-readable explanation to surface

        Postconditions:
            - Returns a HookResponse that drives the host to obtain consent
              (it does not silently allow or permanently block)
            - When request.approval_id is set, the response keys the user's
              decision to that identifier; when None, the host gathers consent
              inline
            - When request.updated_input is set, the response preserves it
              through the consent step
        """
        ...

    # ------------------------------------------------------------------ #
    # Host capabilities: declaration + agnostic query + declared degradation
    # ------------------------------------------------------------------ #

    @abstractmethod
    def capabilities(self) -> FrozenSet[HostCapability]:
        """DECLARE which host capabilities this adapter supports.

        The single place a host states what it can do. Each concrete adapter
        returns the exact set of :class:`HostCapability` members its host
        offers -- nothing inferred, nothing implicit. Business logic never
        reads this set directly; it asks through :meth:`supports` /
        :meth:`degrade_when_missing`, which are host-agnostic.

        Adding a host (Codex, Antigravity) that lacks a capability is a change
        to this declaration alone: the core's query and degradation logic stay
        untouched, and the absence drives the declared degradation path.

        Postconditions:
            - Returns a frozenset of HostCapability members (possibly empty).
            - The result is stable for the lifetime of the adapter instance.
        """
        ...

    def supports(self, capability: HostCapability) -> bool:
        """Ask, host-agnostically, whether ``capability`` is available.

        A concrete query over :meth:`capabilities` so business logic can branch
        on *what the host can do* rather than *which host it is*. Defined here
        (not abstract) so every adapter shares one query semantics; only the
        underlying declaration differs.
        """
        return capability in self.capabilities()

    def degrade_when_missing(
        self,
        capability: HostCapability,
        fallback: str,
        reason: str = "",
    ) -> CapabilityDegradation:
        """Return the DECLARED degradation for ``capability`` on this host.

        The host-agnostic entry point for safe degradation. Business logic that
        needs an optional capability calls this with the ``fallback`` it will
        take if the capability is absent and an optional ``reason``. The result
        is an explicit, observable :class:`CapabilityDegradation`:

        - capability present -> ``available=True``, ``fallback=""`` (use it).
        - capability absent  -> ``available=False`` carrying the caller's
          ``fallback`` and a ``reason`` (degrade in the declared, safe way).

        This replaces the two failure modes the brief forbids: a crash when a
        host lacks a capability, and an implicit ``if host == ...`` branch. The
        degradation is a value, returned the same way for every host.
        """
        if self.supports(capability):
            return CapabilityDegradation(
                capability=capability,
                available=True,
                fallback="",
                reason=reason,
            )
        return CapabilityDegradation(
            capability=capability,
            available=False,
            fallback=fallback,
            reason=reason
            or f"host does not support {capability.value}; degrading to '{fallback}'",
        )

    @abstractmethod
    def format_completion_response(self, result: CompletionResult) -> HookResponse:
        """Format a CompletionResult for CLI consumption.

        Postconditions:
            - HookResponse.output contains contract_valid, anomalies_detected
            - HookResponse.exit_code is always 0
        """
        ...

    @abstractmethod
    def format_context_response(self, result: ContextResult) -> HookResponse:
        """Format a ContextResult for CLI consumption."""
        ...

    @abstractmethod
    def format_bootstrap_response(self, result: BootstrapResult) -> HookResponse:
        """Format a BootstrapResult for CLI consumption.

        Returns session bootstrap status for SessionStart events.
        """
        ...

    @abstractmethod
    def adapt_session_start(self, raw: dict) -> BootstrapResult:
        """Parse SessionStart event and return bootstrap actions.

        Preconditions:
            - raw is the HookEvent.payload dict for a SessionStart event

        Postconditions:
            - Returns BootstrapResult with should_scan and should_refresh set
              based on session_type
        """
        ...

    # ------------------------------------------------------------------ #
    # P2 event adapters
    # ------------------------------------------------------------------ #

    @abstractmethod
    def adapt_stop(self, raw: dict) -> QualityResult:
        """Parse Stop event and assess response quality.

        Preconditions:
            - raw is the HookEvent.payload dict for a Stop event

        Postconditions:
            - Returns QualityResult with quality assessment
        """
        ...

    @abstractmethod
    def adapt_task_completed(self, raw: dict) -> VerificationResult:
        """Parse TaskCompleted event and verify completion criteria.

        Preconditions:
            - raw is the HookEvent.payload dict for a TaskCompleted event

        Postconditions:
            - Returns VerificationResult with criteria assessment
        """
        ...

    @abstractmethod
    def adapt_subagent_start(self, raw: dict) -> ContextResult:
        """Parse SubagentStart event and prepare agent context.

        Preconditions:
            - raw is the HookEvent.payload dict for a SubagentStart event

        Postconditions:
            - Returns ContextResult with agent-specific context
        """
        ...

    # ------------------------------------------------------------------ #
    # P2 formatters
    # ------------------------------------------------------------------ #

    @abstractmethod
    def format_quality_response(self, result: QualityResult) -> HookResponse:
        """Format a QualityResult for CLI consumption."""
        ...

    @abstractmethod
    def format_verification_response(self, result: VerificationResult) -> HookResponse:
        """Format a VerificationResult for CLI consumption."""
        ...

    @abstractmethod
    def detect_distribution(self) -> HostDistribution:
        """DECLARE the host's distribution model for the current invocation.

        The single place a host states HOW it distributes and invokes gaia-ops:
        its own channel identifier and, when it has one, the distribution root.
        The core never enumerates a host's channels nor reads a host-specific
        env var to learn them -- it receives an opaque :class:`HostDistribution`
        and carries it on the :class:`HookEvent`.

        Adding a host with a different distribution model (a native extension
        with its own root, a single canonical channel, ...) is a change to this
        method alone: the concrete adapter declares its own channel names and
        root resolution, and the core vocabulary stays untouched -- the same
        seam used for :meth:`capabilities` and :meth:`request_consent`.

        Postconditions:
            - Returns a HostDistribution whose ``channel`` is the host's own
              channel identifier and whose ``root`` is the distribution root
              for that channel, or None when the channel has no root.
        """
        ...

    # ------------------------------------------------------------------ #
    # Full hook lifecycle adapters (thin-gate pattern)
    # ------------------------------------------------------------------ #

    @abstractmethod
    def adapt_pre_tool_use(self, event: HookEvent) -> HookResponse:
        """Run all pre-tool-use business logic and return a formatted response.

        Orchestrates: routing (bash vs task), validation, state management,
        context injection, approval handling, and response formatting.

        Preconditions:
            - event is a parsed HookEvent with event_type PRE_TOOL_USE

        Postconditions:
            - Returns HookResponse ready for stdout + sys.exit()
        """
        ...

    @abstractmethod
    def adapt_post_tool_use(self, event: HookEvent) -> HookResponse:
        """Run all post-tool-use business logic and return a formatted response.

        Orchestrates: state retrieval, duration computation, audit logging,
        T3 grant confirmation, critical event detection, session context
        writing, and state cleanup.

        Preconditions:
            - event is a parsed HookEvent with event_type POST_TOOL_USE

        Postconditions:
            - Returns HookResponse (always exit 0, post-hook never blocks)
        """
        ...

    @abstractmethod
    def adapt_subagent_stop(self, event: HookEvent) -> HookResponse:
        """Run all subagent-stop business logic and return a formatted response.

        Orchestrates: contract parsing and validation, approval cleanup,
        context updates, workflow recording, response contract validation,
        anomaly detection, episodic memory, and result assembly.

        Preconditions:
            - event is a parsed HookEvent with event_type SUBAGENT_STOP

        Postconditions:
            - Returns HookResponse (exit 0 for success, exit 2 for contract rejection)
        """
        ...
