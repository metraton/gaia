"""
Abstract base class defining the adapter contract.

Each CLI backend (Claude Code, future CLIs) provides a concrete implementation
of HookAdapter. Business logic modules interact only with the normalized types;
they never see raw CLI JSON.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .types import (
    AgentCompletion,
    BootstrapResult,
    CompletionResult,
    ContextResult,
    DistributionChannel,
    HookEvent,
    HookResponse,
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
    def detect_channel(self) -> DistributionChannel:
        """Detect the distribution channel (NPM or PLUGIN).

        Checks environment variables and filesystem layout to determine
        how gaia-ops was installed.
        """
        ...
