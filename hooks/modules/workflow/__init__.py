"""
Workflow module - Phase validation and state tracking.

Provides:
- phase_validator: Merged pre/post phase validation
- state_tracker: Track current workflow phase
"""

from .phase_validator import (
    validate_pre_phase,
    validate_post_phase,
    PhaseValidationResult,
)
from .state_tracker import (
    WorkflowStateTracker,
    get_current_phase,
    set_current_phase,
    WorkflowPhase,
)

__all__ = [
    # Phase validator
    "validate_pre_phase",
    "validate_post_phase",
    "PhaseValidationResult",
    # State tracker
    "WorkflowStateTracker",
    "get_current_phase",
    "set_current_phase",
    "WorkflowPhase",
]
