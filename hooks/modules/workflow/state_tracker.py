"""
Workflow state tracking.

Tracks the current phase of workflow execution.
Used to enforce phase ordering and detect violations.
"""

import os
import json
import logging
from pathlib import Path
from enum import IntEnum
from typing import Optional
from datetime import datetime

from ..core.paths import find_claude_dir

logger = logging.getLogger(__name__)


class WorkflowPhase(IntEnum):
    """Workflow phases."""
    CLARIFICATION = 0
    ROUTING = 1
    CONTEXT = 2
    PLANNING = 3
    APPROVAL = 4
    REALIZATION = 5
    SSOT_UPDATE = 6

    @property
    def name_display(self) -> str:
        """Human-readable phase name."""
        names = {
            0: "Clarification",
            1: "Routing",
            2: "Context Provisioning",
            3: "Planning",
            4: "Approval Gate",
            5: "Realization",
            6: "SSOT Update",
        }
        return names.get(self.value, f"Phase {self.value}")


STATE_FILE_NAME = ".workflow_state.json"


class WorkflowStateTracker:
    """Track and persist workflow state."""

    def __init__(self, state_file: Optional[Path] = None):
        """
        Initialize state tracker.

        Args:
            state_file: Override state file path (for testing)
        """
        if state_file:
            self.state_file = state_file
        else:
            self.state_file = find_claude_dir() / STATE_FILE_NAME

    def get_current_phase(self) -> Optional[WorkflowPhase]:
        """Get the current workflow phase."""
        state = self._load_state()
        if state and "phase" in state:
            return WorkflowPhase(state["phase"])
        return None

    def set_phase(self, phase: WorkflowPhase) -> bool:
        """
        Set the current workflow phase.

        Args:
            phase: Phase to set

        Returns:
            True if successful
        """
        state = self._load_state() or {}
        state["phase"] = phase.value
        state["phase_name"] = phase.name_display
        state["updated_at"] = datetime.now().isoformat()
        return self._save_state(state)

    def can_transition_to(self, target_phase: WorkflowPhase) -> bool:
        """
        Check if transition to target phase is allowed.

        Rules:
        - Can always start at Phase 0 or 1
        - Must follow sequential order (mostly)
        - Phase 4 cannot be skipped for T3 operations

        Args:
            target_phase: Phase to transition to

        Returns:
            True if transition is allowed
        """
        current = self.get_current_phase()

        # Can always start fresh
        if current is None:
            return target_phase in [WorkflowPhase.CLARIFICATION, WorkflowPhase.ROUTING]

        # Sequential progression is always allowed
        if target_phase.value == current.value + 1:
            return True

        # Can skip clarification (Phase 0)
        if current == WorkflowPhase.CLARIFICATION and target_phase == WorkflowPhase.ROUTING:
            return True

        # Can skip from routing to planning (implicit context)
        if current == WorkflowPhase.ROUTING and target_phase == WorkflowPhase.PLANNING:
            return True

        return False

    def reset(self) -> bool:
        """Reset workflow state."""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
            return True
        except Exception as e:
            logger.error(f"Error resetting workflow state: {e}")
            return False

    def _load_state(self) -> Optional[dict]:
        """Load state from file."""
        try:
            if self.state_file.exists():
                with open(self.state_file, "r") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Could not load workflow state: {e}")
        return None

    def _save_state(self, state: dict) -> bool:
        """Save state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            return True
        except Exception as e:
            logger.error(f"Error saving workflow state: {e}")
            return False


# Singleton tracker
_tracker: Optional[WorkflowStateTracker] = None


def get_tracker() -> WorkflowStateTracker:
    """Get singleton state tracker."""
    global _tracker
    if _tracker is None:
        _tracker = WorkflowStateTracker()
    return _tracker


def get_current_phase() -> Optional[WorkflowPhase]:
    """Get current workflow phase (convenience function)."""
    return get_tracker().get_current_phase()


def set_current_phase(phase: WorkflowPhase) -> bool:
    """Set current workflow phase (convenience function)."""
    return get_tracker().set_phase(phase)
