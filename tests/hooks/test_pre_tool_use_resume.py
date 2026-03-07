#!/usr/bin/env python3
"""Tests for Task resume approval handling in pre_tool_use."""

import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import pre_tool_use
from modules.security.approval_grants import (
    ApprovalActivationResult,
    ACTIVATION_ACTIVATED,
    ACTIVATION_EXPIRED,
)


@pytest.fixture
def saved_states(monkeypatch):
    """Capture pre-hook states without writing to disk."""
    captured = []

    def _save(state):
        captured.append(state)
        return True

    monkeypatch.setattr(pre_tool_use, "save_hook_state", _save)
    return captured


class TestHandleTaskResumeApproval:
    """Approval handling for Task resume should be fail-closed and coherent."""

    def test_valid_nonce_allows_resume_and_marks_approval(self, monkeypatch, saved_states):
        nonce = "deadbeef" * 4
        monkeypatch.setattr(
            pre_tool_use,
            "activate_pending_approval",
            lambda value: ApprovalActivationResult(
                success=True,
                status=ACTIVATION_ACTIVATED,
                reason="Pending approval activated.",
                grant_path=Path("/tmp/grant-test.json"),
            ),
        )

        result = pre_tool_use._handle_task(
            "Task",
            {"resume": "a12345", "prompt": f"APPROVE:{nonce}"},
        )

        assert result is None
        assert len(saved_states) == 1
        assert saved_states[0].metadata["has_approval"] is True

    def test_failed_nonce_activation_denies_resume_and_skips_state(self, monkeypatch, saved_states):
        nonce = "deadbeef" * 4
        monkeypatch.setattr(
            pre_tool_use,
            "activate_pending_approval",
            lambda value: ApprovalActivationResult(
                success=False,
                status=ACTIVATION_EXPIRED,
                reason="Approval nonce expired before activation.",
            ),
        )

        result = pre_tool_use._handle_task(
            "Task",
            {"resume": "a12345", "prompt": f"APPROVE:{nonce}"},
        )

        assert isinstance(result, str)
        assert "Approval activation failed" in result
        assert "expired" in result.lower()
        assert saved_states == []

    def test_malformed_nonce_token_denies_resume_and_skips_state(self, saved_states):
        result = pre_tool_use._handle_task(
            "Task",
            {"resume": "a12345", "prompt": "APPROVE:deadbeef continue"},
        )

        assert isinstance(result, str)
        assert "Invalid approval token" in result
        assert saved_states == []

    def test_deprecated_approval_phrase_denies_resume_and_skips_state(self, saved_states):
        result = pre_tool_use._handle_task(
            "Task",
            {"resume": "a12345", "prompt": "User approved: terraform apply prod/vpc"},
        )

        assert isinstance(result, str)
        assert "Deprecated approval format" in result
        assert saved_states == []

    def test_resume_without_approval_token_allows_and_marks_false(self, saved_states):
        result = pre_tool_use._handle_task(
            "Task",
            {"resume": "a12345", "prompt": "Continue with the investigation."},
        )

        assert result is None
        assert len(saved_states) == 1
        assert saved_states[0].metadata["has_approval"] is False
