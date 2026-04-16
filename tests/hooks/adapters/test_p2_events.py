#!/usr/bin/env python3
"""
Tests for P2 Event Adapters (Milestone 7c).

Validates:
1. adapt_stop -- quality passthrough with sensible defaults
2. adapt_task_completed -- verification passthrough with sensible defaults
3. adapt_subagent_start -- agent_type extraction, context stub
4. format_quality_response -- QualityResult -> HookResponse
5. format_verification_response -- VerificationResult -> HookResponse
6. Edge cases: missing fields, empty payloads

Run: python3 -m pytest tests/hooks/adapters/test_p2_events.py -v --tb=short
"""

import sys
import os
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import (
    ContextResult,
    HookResponse,
    QualityResult,
    VerificationResult,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def adapter():
    """Fresh ClaudeCodeAdapter instance with clean env."""
    old_val = os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
    a = ClaudeCodeAdapter()
    yield a
    if old_val is not None:
        os.environ["CLAUDE_PLUGIN_ROOT"] = old_val
    else:
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)


@pytest.fixture
def stop_payload():
    """Realistic Stop event payload."""
    return {
        "hook_event_name": "Stop",
        "session_id": "sess-stop-001",
        "stop_reason": "end_turn",
        "last_assistant_message": "Task complete.",
    }


@pytest.fixture
def task_completed_payload():
    """Realistic TaskCompleted event payload."""
    return {
        "hook_event_name": "TaskCompleted",
        "session_id": "sess-task-001",
        "task_id": "task-abc-123",
        "task_output": "All tests passing. 42 passed, 0 failed.",
    }


@pytest.fixture
def subagent_start_payload():
    """Realistic SubagentStart event payload."""
    return {
        "hook_event_name": "SubagentStart",
        "session_id": "sess-agent-001",
        "agent_type": "cloud-troubleshooter",
        "task_description": "Investigate pod crashloop in namespace prod",
    }


# ============================================================================
# T045: adapt_stop tests
# ============================================================================


class TestAdaptStop:
    """Test adapt_stop method."""

    def test_returns_quality_sufficient(self, adapter, stop_payload):
        """Stop event returns quality_sufficient=True by default."""
        result = adapter.adapt_stop(stop_payload)

        assert isinstance(result, QualityResult)
        assert result.quality_sufficient is True
        assert result.score == 1.0
        assert result.missing_elements == []
        assert result.recommendation == "continue"

    def test_empty_payload(self, adapter):
        """Empty payload defaults gracefully."""
        result = adapter.adapt_stop({})

        assert isinstance(result, QualityResult)
        assert result.quality_sufficient is True
        assert result.score == 1.0

    def test_missing_stop_reason(self, adapter):
        """Missing stop_reason does not raise."""
        payload = {"last_assistant_message": "Done."}
        result = adapter.adapt_stop(payload)

        assert isinstance(result, QualityResult)
        assert result.quality_sufficient is True

    def test_missing_last_message(self, adapter):
        """Missing last_assistant_message does not raise."""
        payload = {"stop_reason": "end_turn"}
        result = adapter.adapt_stop(payload)

        assert isinstance(result, QualityResult)
        assert result.quality_sufficient is True


# ============================================================================
# T046: adapt_task_completed tests
# ============================================================================


class TestAdaptTaskCompleted:
    """Test adapt_task_completed method."""

    def test_returns_criteria_met(self, adapter, task_completed_payload):
        """TaskCompleted returns criteria_met=True by default."""
        result = adapter.adapt_task_completed(task_completed_payload)

        assert isinstance(result, VerificationResult)
        assert result.criteria_met is True
        assert result.verified_items == []
        assert result.failed_items == []
        assert result.block_completion is False

    def test_empty_payload(self, adapter):
        """Empty payload defaults gracefully."""
        result = adapter.adapt_task_completed({})

        assert isinstance(result, VerificationResult)
        assert result.criteria_met is True
        assert result.block_completion is False

    def test_missing_task_id(self, adapter):
        """Missing task_id does not raise."""
        payload = {"task_output": "All good."}
        result = adapter.adapt_task_completed(payload)

        assert isinstance(result, VerificationResult)
        assert result.criteria_met is True

    def test_missing_task_output(self, adapter):
        """Missing task_output does not raise."""
        payload = {"task_id": "task-001"}
        result = adapter.adapt_task_completed(payload)

        assert isinstance(result, VerificationResult)
        assert result.criteria_met is True


# ============================================================================
# T047: adapt_subagent_start tests
# ============================================================================


class TestAdaptSubagentStart:
    """Test adapt_subagent_start method."""

    def test_extracts_agent_type(self, adapter, subagent_start_payload):
        """SubagentStart extracts agent_type and injects context for project agents."""
        result = adapter.adapt_subagent_start(subagent_start_payload)

        assert isinstance(result, ContextResult)
        # Project agents get context injected (via cache or on-demand rebuild)
        assert result.context_injected is True
        assert result.additional_context is not None
        assert isinstance(result.additional_context, str)
        assert result.sections_provided == []

    def test_empty_payload(self, adapter):
        """Empty payload defaults gracefully."""
        result = adapter.adapt_subagent_start({})

        assert isinstance(result, ContextResult)
        assert result.context_injected is False
        assert result.additional_context is None

    def test_missing_agent_type(self, adapter):
        """Missing agent_type does not raise."""
        payload = {"task_description": "Do something"}
        result = adapter.adapt_subagent_start(payload)

        assert isinstance(result, ContextResult)
        assert result.context_injected is False

    def test_missing_task_description(self, adapter):
        """Missing task_description does not raise."""
        payload = {"agent_type": "terraform-architect"}
        result = adapter.adapt_subagent_start(payload)

        assert isinstance(result, ContextResult)


# ============================================================================
# P2: format_quality_response tests
# ============================================================================


class TestFormatQualityResponse:
    """Test format_quality_response output shape."""

    def test_quality_sufficient(self, adapter):
        """Sufficient quality produces clean output."""
        result = QualityResult(
            quality_sufficient=True,
            score=0.95,
            recommendation="continue",
        )
        resp = adapter.format_quality_response(result)

        assert isinstance(resp, HookResponse)
        assert resp.exit_code == 0
        assert resp.output["quality_sufficient"] is True
        assert resp.output["score"] == 0.95
        assert resp.output["recommendation"] == "continue"
        assert "missing_elements" not in resp.output

    def test_quality_insufficient(self, adapter):
        """Insufficient quality includes missing_elements."""
        result = QualityResult(
            quality_sufficient=False,
            score=0.3,
            missing_elements=["EVIDENCE_REPORT", "AGENT_STATUS"],
            recommendation="request more evidence",
        )
        resp = adapter.format_quality_response(result)

        assert resp.exit_code == 0
        assert resp.output["quality_sufficient"] is False
        assert resp.output["score"] == 0.3
        assert resp.output["missing_elements"] == ["EVIDENCE_REPORT", "AGENT_STATUS"]

    def test_default_quality_result(self, adapter):
        """Default QualityResult formats correctly."""
        result = QualityResult()
        resp = adapter.format_quality_response(result)

        assert resp.exit_code == 0
        assert resp.output["quality_sufficient"] is True
        assert resp.output["score"] == 1.0


# ============================================================================
# P2: format_verification_response tests
# ============================================================================


class TestFormatVerificationResponse:
    """Test format_verification_response output shape."""

    def test_criteria_met(self, adapter):
        """Criteria met produces clean output."""
        result = VerificationResult(
            criteria_met=True,
            verified_items=["tests_passed", "lint_clean"],
        )
        resp = adapter.format_verification_response(result)

        assert isinstance(resp, HookResponse)
        assert resp.exit_code == 0
        assert resp.output["criteria_met"] is True
        assert resp.output["block_completion"] is False
        assert resp.output["verified_items"] == ["tests_passed", "lint_clean"]
        assert "failed_items" not in resp.output

    def test_criteria_not_met(self, adapter):
        """Failed criteria includes failed_items and block flag."""
        result = VerificationResult(
            criteria_met=False,
            verified_items=["lint_clean"],
            failed_items=["tests_failed", "missing_evidence"],
            block_completion=True,
        )
        resp = adapter.format_verification_response(result)

        assert resp.exit_code == 0
        assert resp.output["criteria_met"] is False
        assert resp.output["block_completion"] is True
        assert resp.output["verified_items"] == ["lint_clean"]
        assert resp.output["failed_items"] == ["tests_failed", "missing_evidence"]

    def test_default_verification_result(self, adapter):
        """Default VerificationResult formats correctly."""
        result = VerificationResult()
        resp = adapter.format_verification_response(result)

        assert resp.exit_code == 0
        assert resp.output["criteria_met"] is True
        assert resp.output["block_completion"] is False
        assert "verified_items" not in resp.output
        assert "failed_items" not in resp.output


# ============================================================================
# P2 adapter method existence tests
# ============================================================================


class TestP2AdapterMethodsExist:
    """Verify ClaudeCodeAdapter has all P2 adapter methods."""

    @pytest.fixture
    def adapter(self):
        """Fresh adapter instance."""
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        return ClaudeCodeAdapter()

    def test_has_adapt_stop(self, adapter):
        """Adapter has adapt_stop."""
        assert hasattr(adapter, "adapt_stop")
        assert callable(adapter.adapt_stop)

    def test_has_adapt_task_completed(self, adapter):
        """Adapter has adapt_task_completed."""
        assert hasattr(adapter, "adapt_task_completed")
        assert callable(adapter.adapt_task_completed)

    def test_has_adapt_subagent_start(self, adapter):
        """Adapter has adapt_subagent_start."""
        assert hasattr(adapter, "adapt_subagent_start")
        assert callable(adapter.adapt_subagent_start)

    def test_has_format_quality_response(self, adapter):
        """Adapter has format_quality_response."""
        assert hasattr(adapter, "format_quality_response")
        assert callable(adapter.format_quality_response)

    def test_has_format_verification_response(self, adapter):
        """Adapter has format_verification_response."""
        assert hasattr(adapter, "format_verification_response")
        assert callable(adapter.format_verification_response)
