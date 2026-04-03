#!/usr/bin/env python3
"""
T054: P2 Event Integration Tests.

Full flow integration for P2 hook events:
  Claude Code JSON -> adapter parse -> business logic -> adapter format -> JSON response.

Tests for each P2 event:
1. Stop event -> adapter parses -> quality check -> allow stop (exit 0)
2. TaskCompleted event -> adapter parses -> verify -> allow (exit 0)
3. SubagentStart with agent_type -> adapter parses -> context result
4. All P2 events handle malformed/empty payloads gracefully

Modules under test:
  - hooks/adapters/claude_code.py (ClaudeCodeAdapter)
  - hooks/adapters/types.py (QualityResult, VerificationResult, ContextResult)
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions from test_pretool_integration)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import (
    ContextResult,
    HookEventType,
    HookResponse,
    QualityResult,
    VerificationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_stdin(event_name: str, extra: dict = None) -> str:
    """Build a Claude Code stdin JSON payload for a given event type."""
    payload = {
        "hook_event_name": event_name,
        "session_id": "p2-integration-test",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload)


def _run_stop_flow(extra: dict = None) -> tuple:
    """Run the full Stop event flow.

    Returns:
        (event, quality_result, adapter_response) tuple.
    """
    adapter = ClaudeCodeAdapter()
    stdin_json = _build_stdin("Stop", extra)

    event = adapter.parse_event(stdin_json)
    quality_result = adapter.adapt_stop(event.payload)
    response = adapter.format_quality_response(quality_result)

    return event, quality_result, response


def _run_task_completed_flow(extra: dict = None) -> tuple:
    """Run the full TaskCompleted event flow.

    Returns:
        (event, verification_result, adapter_response) tuple.
    """
    adapter = ClaudeCodeAdapter()
    stdin_json = _build_stdin("TaskCompleted", extra)

    event = adapter.parse_event(stdin_json)
    verification_result = adapter.adapt_task_completed(event.payload)
    response = adapter.format_verification_response(verification_result)

    return event, verification_result, response


def _run_subagent_start_flow(extra: dict = None) -> tuple:
    """Run the full SubagentStart event flow.

    Returns:
        (event, context_result, adapter_response) tuple.
    """
    adapter = ClaudeCodeAdapter()
    stdin_json = _build_stdin("SubagentStart", extra)

    event = adapter.parse_event(stdin_json)
    context_result = adapter.adapt_subagent_start(event.payload)
    response = adapter.format_context_response(context_result)

    return event, context_result, response


# ============================================================================
# Test Suite 1: Stop event -> quality check -> allow
# ============================================================================


class TestStopEventFlow:
    """Integration: Stop event -> adapter parse -> quality check -> exit 0."""

    def test_stop_event_parses_correctly(self):
        """Stop event is parsed with correct event type."""
        event, result, response = _run_stop_flow()

        assert event.event_type == HookEventType.STOP

    def test_stop_event_quality_sufficient(self):
        """Default quality check passes (quality_sufficient=True)."""
        _, result, response = _run_stop_flow()

        assert isinstance(result, QualityResult)
        assert result.quality_sufficient is True
        assert result.score == 1.0
        assert result.missing_elements == []
        assert result.recommendation == "continue"

    def test_stop_event_exit_zero(self):
        """Stop event response has exit code 0."""
        _, _, response = _run_stop_flow()

        assert response.exit_code == 0
        assert response.output["quality_sufficient"] is True
        assert response.output["score"] == 1.0
        assert response.output["recommendation"] == "continue"

    def test_stop_event_with_reason(self):
        """Stop event with stop_reason payload."""
        event, result, response = _run_stop_flow({
            "stop_reason": "user_requested",
            "last_assistant_message": "Task complete.",
        })

        assert event.event_type == HookEventType.STOP
        assert result.quality_sufficient is True
        assert response.exit_code == 0


# ============================================================================
# Test Suite 4: TaskCompleted event -> verify -> allow
# ============================================================================


class TestTaskCompletedFlow:
    """Integration: TaskCompleted event -> adapter parse -> verify -> exit 0."""

    def test_task_completed_parses_correctly(self):
        """TaskCompleted event is parsed with correct event type."""
        event, result, response = _run_task_completed_flow({
            "task_id": "task-123",
        })

        assert event.event_type == HookEventType.TASK_COMPLETED

    def test_task_completed_criteria_met(self):
        """Default verification passes (criteria_met=True)."""
        _, result, response = _run_task_completed_flow({
            "task_id": "task-456",
            "task_output": "All tests passed.",
        })

        assert isinstance(result, VerificationResult)
        assert result.criteria_met is True
        assert result.failed_items == []
        assert result.block_completion is False

    def test_task_completed_exit_zero(self):
        """TaskCompleted response has exit code 0."""
        _, _, response = _run_task_completed_flow({
            "task_id": "task-789",
        })

        assert response.exit_code == 0
        assert response.output["criteria_met"] is True
        assert response.output["block_completion"] is False

    def test_task_completed_minimal_payload(self):
        """TaskCompleted with minimal payload (no task_id)."""
        event, result, response = _run_task_completed_flow()

        assert event.event_type == HookEventType.TASK_COMPLETED
        assert result.criteria_met is True
        assert response.exit_code == 0


# ============================================================================
# Test Suite 5: SubagentStart with agent_type -> context result
# ============================================================================


class TestSubagentStartFlow:
    """Integration: SubagentStart -> adapter parse -> context injection -> response."""

    def test_subagent_start_parses_correctly(self):
        """SubagentStart event is parsed with correct event type."""
        event, result, response = _run_subagent_start_flow({
            "agent_type": "cloud-troubleshooter",
        })

        assert event.event_type == HookEventType.SUBAGENT_START

    def test_subagent_start_returns_context_result(self):
        """SubagentStart produces a ContextResult."""
        _, result, response = _run_subagent_start_flow({
            "agent_type": "developer",
            "task_description": "Run npm audit",
        })

        assert isinstance(result, ContextResult)
        # Default: no additional context (passthrough until business logic wired)
        assert result.context_injected is False
        assert result.additional_context is None

    def test_subagent_start_exit_zero(self):
        """SubagentStart response has exit code 0."""
        _, _, response = _run_subagent_start_flow({
            "agent_type": "terraform-architect",
        })

        assert response.exit_code == 0

    def test_subagent_start_minimal_payload(self):
        """SubagentStart with minimal payload (no agent_type)."""
        event, result, response = _run_subagent_start_flow()

        assert event.event_type == HookEventType.SUBAGENT_START
        assert result.context_injected is False
        assert response.exit_code == 0


# ============================================================================
# Test Suite 6: Malformed/empty payload handling
# ============================================================================


class TestP2MalformedPayloads:
    """Integration: P2 events handle malformed and empty payloads gracefully."""

    def test_stop_event_empty_payload(self):
        """Stop event with no extra fields still works."""
        event, result, response = _run_stop_flow()

        assert result.quality_sufficient is True
        assert response.exit_code == 0

    def test_task_completed_empty_task_id(self):
        """TaskCompleted with empty task_id still works."""
        event, result, response = _run_task_completed_flow({"task_id": ""})

        assert result.criteria_met is True
        assert response.exit_code == 0

    def test_subagent_start_empty_agent_type(self):
        """SubagentStart with empty agent_type still works."""
        event, result, response = _run_subagent_start_flow({"agent_type": ""})

        assert result.context_injected is False
        assert response.exit_code == 0

    def test_unknown_event_type_raises(self):
        """Unknown event type raises ValueError during parse."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_stdin("NonExistentP2Event")

        with pytest.raises(ValueError, match="Unknown hook event type"):
            adapter.parse_event(stdin_json)

    def test_missing_hook_event_name_raises(self):
        """Missing hook_event_name raises ValueError."""
        adapter = ClaudeCodeAdapter()
        stdin_json = json.dumps({
            "session_id": "test",
            "tool_name": "Bash",
        })

        with pytest.raises(ValueError, match="Missing required field"):
            adapter.parse_event(stdin_json)

    def test_empty_stdin_raises(self):
        """Empty stdin raises ValueError."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(ValueError, match="Empty stdin"):
            adapter.parse_event("")

    def test_invalid_json_raises(self):
        """Invalid JSON raises ValueError."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(ValueError, match="Invalid JSON"):
            adapter.parse_event("{not valid json!!!}")
