#!/usr/bin/env python3
"""
Tests for Workflow State Tracker.

Validates:
1. WorkflowPhase enum
2. WorkflowStateTracker class
3. Phase transitions
"""

import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.workflow.state_tracker import (
    WorkflowPhase,
    WorkflowStateTracker,
    get_tracker,
    get_current_phase,
    set_current_phase,
    STATE_FILE_NAME,
)
from modules.core.paths import clear_path_cache


class TestWorkflowPhaseEnum:
    """Test WorkflowPhase enum."""

    def test_phase_values(self):
        """Test phase enum values."""
        assert WorkflowPhase.CLARIFICATION == 0
        assert WorkflowPhase.ROUTING == 1
        assert WorkflowPhase.CONTEXT == 2
        assert WorkflowPhase.PLANNING == 3
        assert WorkflowPhase.APPROVAL == 4
        assert WorkflowPhase.REALIZATION == 5
        assert WorkflowPhase.SSOT_UPDATE == 6

    def test_name_display_property(self):
        """Test name_display property."""
        assert "Clarification" in WorkflowPhase.CLARIFICATION.name_display
        assert "Routing" in WorkflowPhase.ROUTING.name_display
        assert "Approval" in WorkflowPhase.APPROVAL.name_display
        assert "Realization" in WorkflowPhase.REALIZATION.name_display

    def test_phases_are_sequential(self):
        """Test phases have sequential integer values."""
        phases = list(WorkflowPhase)
        for i, phase in enumerate(phases):
            assert phase.value == i


class TestWorkflowStateTracker:
    """Test WorkflowStateTracker class."""

    @pytest.fixture
    def temp_state_file(self, tmp_path):
        """Create temporary state file location."""
        state_file = tmp_path / STATE_FILE_NAME
        return state_file

    @pytest.fixture
    def tracker(self, temp_state_file):
        """Create tracker with temp state file."""
        return WorkflowStateTracker(state_file=temp_state_file)

    def test_get_current_phase_no_state(self, tracker):
        """Test get_current_phase returns None when no state."""
        result = tracker.get_current_phase()
        assert result is None

    def test_set_and_get_phase(self, tracker):
        """Test setting and getting phase."""
        tracker.set_phase(WorkflowPhase.ROUTING)
        result = tracker.get_current_phase()
        assert result == WorkflowPhase.ROUTING

    def test_set_all_phases(self, tracker):
        """Test setting all phases."""
        for phase in WorkflowPhase:
            tracker.set_phase(phase)
            result = tracker.get_current_phase()
            assert result == phase

    def test_reset_clears_state(self, tracker, temp_state_file):
        """Test reset clears state."""
        tracker.set_phase(WorkflowPhase.PLANNING)
        assert temp_state_file.exists()

        tracker.reset()
        assert not temp_state_file.exists()
        assert tracker.get_current_phase() is None


class TestPhaseTransitions:
    """Test phase transition validation."""

    @pytest.fixture
    def tracker(self, tmp_path):
        """Create tracker with temp state file."""
        state_file = tmp_path / STATE_FILE_NAME
        return WorkflowStateTracker(state_file=state_file)

    def test_can_start_at_clarification(self, tracker):
        """Test can start at Phase 0."""
        assert tracker.can_transition_to(WorkflowPhase.CLARIFICATION) is True

    def test_can_start_at_routing(self, tracker):
        """Test can start at Phase 1."""
        assert tracker.can_transition_to(WorkflowPhase.ROUTING) is True

    def test_cannot_start_at_approval(self, tracker):
        """Test cannot start directly at Phase 4."""
        assert tracker.can_transition_to(WorkflowPhase.APPROVAL) is False

    def test_sequential_progression_allowed(self, tracker):
        """Test sequential progression is allowed."""
        tracker.set_phase(WorkflowPhase.ROUTING)
        assert tracker.can_transition_to(WorkflowPhase.CONTEXT) is True

        tracker.set_phase(WorkflowPhase.CONTEXT)
        assert tracker.can_transition_to(WorkflowPhase.PLANNING) is True

    def test_can_skip_clarification(self, tracker):
        """Test can skip from clarification to routing."""
        tracker.set_phase(WorkflowPhase.CLARIFICATION)
        assert tracker.can_transition_to(WorkflowPhase.ROUTING) is True

    def test_can_skip_to_planning_from_routing(self, tracker):
        """Test can skip from routing to planning."""
        tracker.set_phase(WorkflowPhase.ROUTING)
        assert tracker.can_transition_to(WorkflowPhase.PLANNING) is True


class TestStatePersistence:
    """Test state persistence to file."""

    @pytest.fixture
    def tracker(self, tmp_path):
        """Create tracker with temp state file."""
        state_file = tmp_path / STATE_FILE_NAME
        return WorkflowStateTracker(state_file=state_file)

    def test_state_persists_to_file(self, tracker, tmp_path):
        """Test state is written to file."""
        tracker.set_phase(WorkflowPhase.APPROVAL)
        state_file = tmp_path / STATE_FILE_NAME
        assert state_file.exists()

        with open(state_file) as f:
            data = json.load(f)
        assert data["phase"] == 4

    def test_state_survives_new_tracker(self, tmp_path):
        """Test state survives creating new tracker."""
        state_file = tmp_path / STATE_FILE_NAME
        tracker1 = WorkflowStateTracker(state_file=state_file)
        tracker1.set_phase(WorkflowPhase.REALIZATION)

        tracker2 = WorkflowStateTracker(state_file=state_file)
        assert tracker2.get_current_phase() == WorkflowPhase.REALIZATION

    def test_state_includes_timestamp(self, tracker, tmp_path):
        """Test state includes timestamp."""
        tracker.set_phase(WorkflowPhase.PLANNING)
        state_file = tmp_path / STATE_FILE_NAME

        with open(state_file) as f:
            data = json.load(f)
        assert "updated_at" in data

    def test_state_includes_phase_name(self, tracker, tmp_path):
        """Test state includes phase name."""
        tracker.set_phase(WorkflowPhase.CONTEXT)
        state_file = tmp_path / STATE_FILE_NAME

        with open(state_file) as f:
            data = json.load(f)
        assert "phase_name" in data
        assert "Context" in data["phase_name"]


class TestConvenienceFunctions:
    """Test convenience functions."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        clear_path_cache()
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.workflow.state_tracker.find_claude_dir", return_value=claude_dir):
            # Reset global tracker
            import modules.workflow.state_tracker as tracker_module
            tracker_module._tracker = None
            yield

    def test_get_tracker_returns_tracker(self):
        """Test get_tracker returns tracker instance."""
        tracker = get_tracker()
        assert isinstance(tracker, WorkflowStateTracker)

    def test_get_tracker_is_singleton(self):
        """Test get_tracker returns same instance."""
        tracker1 = get_tracker()
        tracker2 = get_tracker()
        assert tracker1 is tracker2

    def test_get_current_phase_function(self):
        """Test get_current_phase convenience function."""
        result = get_current_phase()
        # Initially None
        assert result is None

    def test_set_current_phase_function(self):
        """Test set_current_phase convenience function."""
        success = set_current_phase(WorkflowPhase.ROUTING)
        assert success is True
        assert get_current_phase() == WorkflowPhase.ROUTING


class TestErrorHandling:
    """Test error handling."""

    def test_handles_corrupt_state_file(self, tmp_path):
        """Test handles corrupt state file."""
        state_file = tmp_path / STATE_FILE_NAME
        with open(state_file, "w") as f:
            f.write("not valid json")

        tracker = WorkflowStateTracker(state_file=state_file)
        result = tracker.get_current_phase()
        # Should return None on error
        assert result is None

    def test_handles_missing_phase_in_state(self, tmp_path):
        """Test handles missing phase key in state."""
        state_file = tmp_path / STATE_FILE_NAME
        with open(state_file, "w") as f:
            json.dump({"other": "data"}, f)

        tracker = WorkflowStateTracker(state_file=state_file)
        result = tracker.get_current_phase()
        assert result is None
