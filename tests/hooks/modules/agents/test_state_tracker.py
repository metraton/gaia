"""Tests for state transition tracking (state_tracker module)."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from hooks.modules.agents.state_tracker import (
    TransitionResult,
    track_transition,
    get_agent_state,
    clear_agent_state,
    _STATE_FILE,
)


@pytest.fixture(autouse=True)
def clean_state_file():
    """Ensure the state file is clean before and after each test."""
    if _STATE_FILE.exists():
        _STATE_FILE.unlink()
    yield
    if _STATE_FILE.exists():
        _STATE_FILE.unlink()


class TestFirstTransition:
    """First state for a new agent is always valid."""

    def test_first_in_progress(self):
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True
        assert result.previous_state == ""
        assert result.current_state == "IN_PROGRESS"
        assert result.error == ""

    def test_first_complete(self):
        result = track_transition("a12345", "COMPLETE")
        assert result.valid is True
        assert result.previous_state == ""

    def test_no_agent_id_skips_tracking(self):
        result = track_transition("", "IN_PROGRESS")
        assert result.valid is True
        assert "skipped" in result.warning.lower()


class TestLegalTransitions:
    """Test all legal transitions from agent-protocol."""

    def test_in_progress_to_complete(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "COMPLETE")
        assert result.valid is True
        assert result.previous_state == "IN_PROGRESS"
        assert result.current_state == "COMPLETE"

    def test_in_progress_to_review(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "APPROVAL_REQUEST")
        assert result.valid is True

    def test_review_to_in_progress(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "APPROVAL_REQUEST")
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True

    def test_in_progress_to_blocked(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "BLOCKED")
        assert result.valid is True

    def test_in_progress_to_needs_input(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "NEEDS_INPUT")
        assert result.valid is True

    def test_full_t3_flow(self):
        """IN_PROGRESS -> APPROVAL_REQUEST -> IN_PROGRESS -> COMPLETE"""
        r1 = track_transition("a12345", "IN_PROGRESS")
        assert r1.valid is True
        r2 = track_transition("a12345", "APPROVAL_REQUEST")
        assert r2.valid is True
        r3 = track_transition("a12345", "IN_PROGRESS")
        assert r3.valid is True
        r4 = track_transition("a12345", "COMPLETE")
        assert r4.valid is True


class TestIllegalTransitions:
    """Test transitions that should be rejected."""

    def test_review_to_complete(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "APPROVAL_REQUEST")
        result = track_transition("a12345", "COMPLETE")
        assert result.valid is False
        assert "Illegal state transition" in result.error

    def test_review_to_blocked(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "APPROVAL_REQUEST")
        result = track_transition("a12345", "BLOCKED")
        assert result.valid is False

    def test_review_to_review(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "APPROVAL_REQUEST")
        result = track_transition("a12345", "APPROVAL_REQUEST")
        assert result.valid is False


class TestRetryLimits:
    """IN_PROGRESS -> IN_PROGRESS is capped at max 2."""

    def test_first_retry_ok(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True
        assert result.in_progress_count == 2

    def test_second_retry_warning(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True
        assert "retry count" in result.warning.lower()

    def test_third_retry_rejected(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "IN_PROGRESS")  # count=2
        result = track_transition("a12345", "IN_PROGRESS")  # count=3, exceeds max
        assert result.valid is False
        assert "retry limit exceeded" in result.error.lower()

    def test_retry_count_resets_after_review(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "IN_PROGRESS")  # count=2
        track_transition("a12345", "APPROVAL_REQUEST")
        result = track_transition("a12345", "IN_PROGRESS")  # reset to 1
        assert result.valid is True
        assert result.in_progress_count == 1


class TestReviewWarning:
    """IN_PROGRESS -> COMPLETE without REVIEW warns when has_review_phase=True."""

    def test_skip_review_with_flag(self):
        track_transition("a12345", "IN_PROGRESS", has_review_phase=True)
        result = track_transition("a12345", "COMPLETE", has_review_phase=True)
        assert result.valid is True  # Valid but with warning
        assert "without an intervening APPROVAL_REQUEST" in result.warning

    def test_skip_review_without_flag(self):
        track_transition("a12345", "IN_PROGRESS")
        result = track_transition("a12345", "COMPLETE")
        assert result.valid is True
        assert result.warning == ""

    def test_no_warning_when_review_seen(self):
        track_transition("a12345", "IN_PROGRESS", has_review_phase=True)
        track_transition("a12345", "APPROVAL_REQUEST", has_review_phase=True)
        track_transition("a12345", "IN_PROGRESS", has_review_phase=True)
        result = track_transition("a12345", "COMPLETE", has_review_phase=True)
        assert result.valid is True
        assert "without an intervening REVIEW" not in result.warning


class TestStatePersistence:
    """State is persisted to file and survives across calls."""

    def test_get_agent_state(self):
        track_transition("a12345", "IN_PROGRESS")
        state = get_agent_state("a12345")
        assert state is not None
        assert state["state"] == "IN_PROGRESS"

    def test_get_unknown_agent(self):
        state = get_agent_state("unknown")
        assert state is None

    def test_clear_agent_state(self):
        track_transition("a12345", "IN_PROGRESS")
        clear_agent_state("a12345")
        state = get_agent_state("a12345")
        assert state is None

    def test_multiple_agents_independent(self):
        track_transition("a11111", "IN_PROGRESS")
        track_transition("a22222", "IN_PROGRESS")
        track_transition("a11111", "APPROVAL_REQUEST")
        # a22222 should still be IN_PROGRESS
        state = get_agent_state("a22222")
        assert state["state"] == "IN_PROGRESS"
        # a11111 should be APPROVAL_REQUEST
        state = get_agent_state("a11111")
        assert state["state"] == "APPROVAL_REQUEST"


class TestTerminalStateRecovery:
    """Terminal states allow new task cycles starting from IN_PROGRESS."""

    def test_complete_to_in_progress(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "COMPLETE")
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True

    def test_blocked_to_in_progress(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "BLOCKED")
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True

    def test_needs_input_to_in_progress(self):
        track_transition("a12345", "IN_PROGRESS")
        track_transition("a12345", "NEEDS_INPUT")
        result = track_transition("a12345", "IN_PROGRESS")
        assert result.valid is True


class TestCaseInsensitivity:
    """State values should be normalized to uppercase."""

    def test_lowercase_input(self):
        result = track_transition("a12345", "in_progress")
        assert result.valid is True
        assert result.current_state == "IN_PROGRESS"

    def test_mixed_case_input(self):
        track_transition("a12345", "In_Progress")
        result = track_transition("a12345", "Approval_Request")
        assert result.valid is True
        assert result.current_state == "APPROVAL_REQUEST"
