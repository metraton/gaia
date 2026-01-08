"""
Workflow phase validation.

Merged pre/post phase validation logic from pre_phase_hook.py and post_phase_hook.py.
Validates phase transitions and requirements.
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PhaseValidationResult:
    """Result of phase validation."""
    allowed: bool
    reason: str
    phase: str = ""
    is_pre_hook: bool = True


# Agent context requirements by agent type
AGENT_CONTEXT_REQUIREMENTS = {
    "terraform-architect": ["project_details", "terraform_infrastructure", "operational_guidelines"],
    "gitops-operator": ["project_details", "gitops_configuration", "cluster_details"],
    "gcp-troubleshooter": ["project_details", "cluster_details"],
    "aws-troubleshooter": ["project_details", "cluster_details"],
    "devops-developer": ["project_details", "operational_guidelines"],
}


def validate_pre_phase(
    phase: int,
    **kwargs
) -> PhaseValidationResult:
    """
    Validate before entering a workflow phase.

    Args:
        phase: Phase number (0-6)
        **kwargs: Phase-specific parameters

    Returns:
        PhaseValidationResult
    """
    validators = {
        0: _validate_pre_phase_0,
        1: _validate_pre_phase_1,
        2: _validate_pre_phase_2,
        4: _validate_pre_phase_4,
        5: _validate_pre_phase_5,
        6: _validate_pre_phase_6,
    }

    validator = validators.get(phase)
    if validator:
        return validator(**kwargs)

    # No validation for this phase
    return PhaseValidationResult(
        allowed=True,
        reason=f"No pre-validation required for phase {phase}",
        phase=str(phase),
        is_pre_hook=True,
    )


def validate_post_phase(
    phase: int,
    **kwargs
) -> PhaseValidationResult:
    """
    Validate after completing a workflow phase.

    Args:
        phase: Phase number (0-6)
        **kwargs: Phase-specific parameters

    Returns:
        PhaseValidationResult
    """
    validators = {
        4: _validate_post_phase_4,
        6: _validate_post_phase_6,
    }

    validator = validators.get(phase)
    if validator:
        return validator(**kwargs)

    return PhaseValidationResult(
        allowed=True,
        reason=f"No post-validation required for phase {phase}",
        phase=str(phase),
        is_pre_hook=False,
    )


def _validate_pre_phase_0(
    ambiguity_score: float = 0.0,
    **kwargs
) -> PhaseValidationResult:
    """Validate before Phase 0 (Clarification)."""
    threshold = kwargs.get("threshold", 0.3)

    if ambiguity_score > threshold:
        return PhaseValidationResult(
            allowed=True,
            reason="Ambiguity detected, clarification needed",
            phase="0",
        )

    return PhaseValidationResult(
        allowed=True,
        reason="Low ambiguity, can skip clarification",
        phase="0",
    )


def _validate_pre_phase_1(
    agent_name: str = "",
    routing_confidence: float = 0.0,
    available_agents: List[str] = None,
    **kwargs
) -> PhaseValidationResult:
    """Validate before Phase 1 (Routing)."""
    min_confidence = kwargs.get("min_confidence", 0.5)

    # Check routing confidence
    if routing_confidence < min_confidence:
        return PhaseValidationResult(
            allowed=False,
            reason=f"Routing confidence {routing_confidence} below threshold {min_confidence}",
            phase="1",
        )

    # Check agent exists
    if available_agents and agent_name not in available_agents:
        return PhaseValidationResult(
            allowed=False,
            reason=f"Unknown agent: {agent_name}",
            phase="1",
        )

    return PhaseValidationResult(
        allowed=True,
        reason="Phase 1 guards passed",
        phase="1",
    )


def _validate_pre_phase_2(
    context_payload: Dict[str, Any] = None,
    agent_name: str = "",
    **kwargs
) -> PhaseValidationResult:
    """Validate before Phase 2 (Context Provisioning)."""
    if context_payload is None:
        context_payload = {}

    required_sections = AGENT_CONTEXT_REQUIREMENTS.get(agent_name, ["project_details"])

    # Check context completeness
    missing = []
    for section in required_sections:
        if section not in context_payload or not context_payload[section]:
            missing.append(section)

    if missing:
        return PhaseValidationResult(
            allowed=False,
            reason=f"Missing context sections for {agent_name}: {', '.join(missing)}",
            phase="2",
        )

    return PhaseValidationResult(
        allowed=True,
        reason="Phase 2 guards passed",
        phase="2",
    )


def _validate_pre_phase_4(
    tier: str = "",
    realization_package: Dict[str, Any] = None,
    **kwargs
) -> PhaseValidationResult:
    """Validate before Phase 4 (Approval Gate)."""
    if realization_package is None:
        realization_package = {}

    # Check that planning is complete (has files or git_operations)
    has_files = bool(realization_package.get("files"))
    has_git_ops = bool(realization_package.get("git_operations"))

    if not has_files and not has_git_ops:
        logger.warning("Realization package appears empty")

    return PhaseValidationResult(
        allowed=True,
        reason="Phase 4 pre-guards passed",
        phase="4",
    )


def _validate_pre_phase_5(
    tier: str = "",
    approval_validation: Dict[str, Any] = None,
    **kwargs
) -> PhaseValidationResult:
    """Validate before Phase 5 (Realization) - CRITICAL."""
    if approval_validation is None:
        approval_validation = {}

    # T3 operations MUST have approval
    if tier == "T3":
        if not approval_validation.get("approved", False):
            return PhaseValidationResult(
                allowed=False,
                reason="T3 operation requires approval before realization",
                phase="5",
            )

        # Check approval action
        action = approval_validation.get("action", "")
        if action not in ["proceed", "proceed_to_realization"]:
            return PhaseValidationResult(
                allowed=False,
                reason=f"Invalid approval action: {action}",
                phase="5",
            )

    return PhaseValidationResult(
        allowed=True,
        reason="Phase 5 guards passed",
        phase="5",
    )


def _validate_pre_phase_6(
    realization_success: bool = False,
    **kwargs
) -> PhaseValidationResult:
    """Validate before Phase 6 (SSOT Update)."""
    if not realization_success:
        return PhaseValidationResult(
            allowed=False,
            reason="Cannot update SSOT: Realization failed",
            phase="6",
        )

    return PhaseValidationResult(
        allowed=True,
        reason="Phase 6 pre-guards passed",
        phase="6",
    )


def _validate_post_phase_4(
    tier: str = "",
    validation_result: Dict[str, Any] = None,
    **kwargs
) -> PhaseValidationResult:
    """Validate after Phase 4 (Approval Gate)."""
    if validation_result is None:
        validation_result = {}

    if tier == "T3":
        if not validation_result.get("approved", False):
            return PhaseValidationResult(
                allowed=False,
                reason="T3 approval rejected",
                phase="4",
                is_pre_hook=False,
            )

    return PhaseValidationResult(
        allowed=True,
        reason="Approval validation passed",
        phase="4",
        is_pre_hook=False,
    )


def _validate_post_phase_6(
    tier: str = "",
    ssot_updated: bool = False,
    **kwargs
) -> PhaseValidationResult:
    """Validate after Phase 6 (SSOT Update)."""
    if tier == "T3" and not ssot_updated:
        return PhaseValidationResult(
            allowed=False,
            reason="T3 operation completed but SSOT not updated",
            phase="6",
            is_pre_hook=False,
        )

    return PhaseValidationResult(
        allowed=True,
        reason="SSOT update validation passed",
        phase="6",
        is_pre_hook=False,
    )
