#!/usr/bin/env python3
"""Tests for the ElicitationResult hook.

Validates:
1. Approval response activates pending grants
2. Rejection response does NOT activate grants
3. Empty/malformed input exits 0 (no crash)
4. No pending approvals = no-op
5. Response extraction from various event schemas
"""

import json
import sys
import time
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from elicitation_result import _extract_response, _is_approval, _activate_grants
from modules.security.approval_grants import (
    check_approval_grant,
    generate_nonce,
    get_pending_approvals_for_session,
    write_pending_approval,
)
from modules.core.paths import clear_path_cache


@pytest.fixture(autouse=True)
def clean_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for grants and clean up after each test."""
    import modules.security.approval_grants as ag

    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-elicitation-session")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False
    yield grants_dir
    clear_path_cache()


class TestExtractResponse:
    """Test response extraction from various ElicitationResult event schemas."""

    def test_extract_from_result_field(self):
        event = {"result": "Approve"}
        assert _extract_response(event) == "Approve"

    def test_extract_from_answer_field(self):
        event = {"answer": "Approve"}
        assert _extract_response(event) == "Approve"

    def test_extract_from_selected_field(self):
        event = {"selected": "Reject"}
        assert _extract_response(event) == "Reject"

    def test_extract_from_nested_result(self):
        event = {"result": {"answer": "Approve"}}
        assert _extract_response(event) == "Approve"

    def test_extract_from_nested_selected(self):
        event = {"hookEventInput": {"selected": "Approve"}}
        assert _extract_response(event) == "Approve"

    def test_extract_from_answers_dict(self):
        event = {"result": {"answers": {"approval": "Approve"}}}
        assert _extract_response(event) == "Approve"

    def test_extract_returns_none_for_empty_event(self):
        assert _extract_response({}) is None

    def test_extract_returns_none_for_no_recognized_fields(self):
        event = {"unrelated_field": "something", "another": 42}
        assert _extract_response(event) is None

    def test_extract_skips_none_values(self):
        event = {"result": None, "answer": "Approve"}
        assert _extract_response(event) == "Approve"

    def test_extract_skips_empty_strings(self):
        event = {"result": "", "answer": "Approve"}
        assert _extract_response(event) == "Approve"


class TestIsApproval:
    """Test approval detection logic."""

    def test_approve_exact(self):
        assert _is_approval("Approve") is True

    def test_approve_lowercase(self):
        assert _is_approval("approve") is True

    def test_approved_past_tense(self):
        assert _is_approval("Approved") is True

    def test_yes(self):
        assert _is_approval("yes") is True

    def test_accept(self):
        assert _is_approval("Accept") is True

    def test_confirm(self):
        assert _is_approval("confirm") is True

    def test_allow(self):
        assert _is_approval("Allow") is True

    def test_reject_is_not_approval(self):
        assert _is_approval("Reject") is False

    def test_modify_is_not_approval(self):
        assert _is_approval("Modify") is False

    def test_no_is_not_approval(self):
        assert _is_approval("no") is False

    def test_empty_is_not_approval(self):
        assert _is_approval("") is False

    def test_whitespace_only_is_not_approval(self):
        assert _is_approval("   ") is False

    def test_cancel_is_not_approval(self):
        assert _is_approval("cancel") is False

    def test_approve_with_description(self):
        assert _is_approval("Approve -- Allow the operation to proceed") is True


class TestActivateGrants:
    """Test grant activation via _activate_grants."""

    def test_approval_activates_pending_grant(self):
        """Approval response should activate a pending grant."""
        session_id = "test-elicitation-session"
        command = "terraform apply"

        # Create a pending approval
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command=command,
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        # Verify pending exists
        pending = get_pending_approvals_for_session(session_id)
        assert len(pending) == 1

        # Activate grants
        _activate_grants(session_id)

        # Verify grant is now active
        grant = check_approval_grant(command)
        assert grant is not None, "Grant should be active after activation"
        assert grant.approved_scope == command

        # Verify pending is consumed
        pending_after = get_pending_approvals_for_session(session_id)
        assert len(pending_after) == 0, "Pending should be consumed after activation"

    def test_no_pending_is_noop(self):
        """No pending approvals should be a silent no-op."""
        session_id = "test-elicitation-session"

        # No pending approvals exist -- should not raise
        _activate_grants(session_id)

        # No grants created
        grant = check_approval_grant("terraform apply")
        assert grant is None

    def test_multiple_pending_all_activated(self):
        """Multiple pending approvals should all be activated."""
        from modules.security.approval_grants import activate_grants_for_session

        session_id = "test-elicitation-session"

        nonce1 = generate_nonce()
        write_pending_approval(
            nonce=nonce1,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        nonce2 = generate_nonce()
        write_pending_approval(
            nonce=nonce2,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        # Verify both pending exist before activation
        pending_before = get_pending_approvals_for_session(session_id)
        assert len(pending_before) == 2, "Should have 2 pending approvals"

        # Activate via the module function directly to check results
        results = activate_grants_for_session(session_id)
        activated = sum(1 for r in results if r.success)
        assert activated == 2, (
            f"Expected 2 activations, got {activated}. "
            f"Results: {[(r.status, r.reason) for r in results]}"
        )

        # Verify at least the first grant is findable
        grant1 = check_approval_grant("terraform apply")
        assert grant1 is not None, "First grant should be active"

        # Verify all pending consumed
        pending_after = get_pending_approvals_for_session(session_id)
        assert len(pending_after) == 0, "All pending should be consumed"


class TestMalformedInput:
    """Test that malformed/empty input does not crash the hook."""

    def test_empty_string_extracts_none(self):
        assert _extract_response({}) is None

    def test_non_dict_values_handled(self):
        event = {"result": 42, "answer": True}
        # Should not crash, should return None (no string match)
        result = _extract_response(event)
        assert result is None

    def test_deeply_nested_event_does_not_crash(self):
        event = {
            "result": {
                "nested": {
                    "deep": "value"
                }
            }
        }
        # Should not crash even with unexpected nesting
        _extract_response(event)
