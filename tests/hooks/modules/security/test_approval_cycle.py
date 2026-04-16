#!/usr/bin/env python3
"""Tests for the full approval cycle: deny -> activate -> retry.

Validates the end-to-end approval flow introduced by the unified REVIEW
status and approval_id mechanism:

1. Subagent mutative command gets denied with approval_id
2. Orchestrator mutative command gets "ask" (no approval_id)
3. ElicitationResult activates grant for pending approval
4. Full cycle: deny -> approve -> retry succeeds
5. Negative response does NOT activate grant
6. Expired pending is not activated
7. Approval response pattern matching (elicitation_result._is_approval)
8. Subagent retry reuses existing pending nonce
"""

import sys
import time
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_grants import (
    ACTIVATION_ACTIVATED,
    ACTIVATION_EXPIRED,
    ACTIVATION_NOT_FOUND,
    DEFAULT_GRANT_TTL_MINUTES,
    ApprovalGrant,
    activate_grants_for_session,
    activate_pending_approval,
    check_approval_grant,
    confirm_grant,
    consume_grant,
    consume_session_grants,
    generate_nonce,
    get_pending_approvals_for_session,
    write_pending_approval,
)
from modules.tools.bash_validator import BashValidator, validate_bash_command


@pytest.fixture(autouse=True)
def clean_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for grants and clean up after each test."""
    import modules.security.approval_grants as ag

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-cycle-session")
    # Reset cleanup throttle and mkdir cache so each test starts clean
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False
    yield grants_dir


class TestSubagentMutativeDeny:
    """Test 1: Subagent mutative command gets denied with approval_id."""

    def test_subagent_mutative_gets_deny_with_approval_id(self):
        """Subagent context (is_subagent=True) returns deny with approval_id."""
        result = validate_bash_command(
            "terraform apply",
            is_subagent=True,
            session_id="test-cycle-session",
        )

        assert not result.allowed, "T3 command should be blocked"
        assert result.block_response is not None, "Should have structured response"

        hook_output = result.block_response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "deny", (
            f"Expected deny, got: {hook_output.get('permissionDecision')}"
        )

        # Verify approval_id is present in the deny reason
        reason = hook_output.get("permissionDecisionReason", "")
        assert "approval_id:" in reason, (
            f"Expected approval_id in deny reason, got: {reason}"
        )

    def test_subagent_deny_creates_pending_approval(self):
        """Subagent deny should create a pending approval file."""
        result = validate_bash_command(
            "git push origin main",
            is_subagent=True,
            session_id="test-cycle-session",
        )

        assert not result.allowed
        pending = get_pending_approvals_for_session("test-cycle-session")
        assert len(pending) >= 1, "Expected at least one pending approval"
        assert pending[0]["danger_verb"] == "push"


class TestOrchestratorMutativeAsk:
    """Test 2: Orchestrator mutative command gets "ask" (no approval_id)."""

    def test_orchestrator_mutative_gets_ask_no_approval_id(self):
        """Orchestrator context (is_subagent=False) returns ask without approval_id."""
        result = validate_bash_command(
            "terraform apply",
            is_subagent=False,
            session_id="test-cycle-session",
        )

        assert not result.allowed, "T3 command should be blocked"
        assert result.block_response is not None, "Should have structured response"

        hook_output = result.block_response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", (
            f"Expected ask, got: {hook_output.get('permissionDecision')}"
        )

        # Verify NO approval_id is present (orchestrator uses native dialog)
        reason = hook_output.get("permissionDecisionReason", "")
        assert "approval_id:" not in reason, (
            f"Orchestrator context should not have approval_id, got: {reason}"
        )


class TestElicitationResultActivatesGrant:
    """Test 3: ElicitationResult activates grant for pending approval."""

    def test_activate_grants_for_session(self):
        """Writing a pending approval then activating it creates a usable grant."""
        nonce = generate_nonce()
        command = "terraform apply"

        # 1. Write a pending approval
        path = write_pending_approval(
            nonce=nonce,
            command=command,
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id="test-cycle-session",
        )
        assert path is not None, "Failed to write pending approval"

        # 2. Activate grants for the session (simulates ElicitationResult)
        results = activate_grants_for_session("test-cycle-session")
        assert len(results) >= 1, "Expected at least one activation result"
        assert results[0].success, f"Activation should succeed: {results[0].reason}"

        # 3. Check that the grant is now active
        grant = check_approval_grant(command)
        assert grant is not None, "Grant should be active after activation"
        assert grant.approved_scope == command


class TestFullApprovalCycle:
    """Test 4: Full cycle -- deny, approve, retry succeeds (passthrough)."""

    def test_deny_activate_retry_succeeds(self):
        """Complete cycle: subagent denied, approval activated, retry passthrough.

        With grant passthrough, once a grant is activated (even unconfirmed),
        the validator returns allowed=True immediately. PostToolUse will
        confirm and consume the grant after execution.
        """
        command = "terraform apply"
        session_id = "test-cycle-session"

        # Step 1: Subagent command is denied with approval_id
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed
        hook_output = result1.block_response["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "deny"

        # Extract approval_id from the deny reason
        reason = hook_output["permissionDecisionReason"]
        import re
        match = re.search(r"approval_id:\s*(\w+)", reason)
        assert match, f"Could not extract approval_id from: {reason}"
        approval_id = match.group(1)

        # Step 2: Activate grants for the session (simulates ElicitationResult)
        results = activate_grants_for_session(session_id)
        assert len(results) >= 1
        assert results[0].success, f"Activation failed: {results[0].reason}"

        # Step 3: Retry the same command -- passthrough (grant exists)
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert result2.allowed, "Active grant should passthrough (allowed=True)"
        assert "Grant active" in result2.reason or "Grant confirmed" in result2.reason


class TestNegativeResponseDoesNotActivate:
    """Test 5: Negative response does NOT activate grant."""

    def test_is_approval_rejects_negative(self):
        """_is_approval returns False for negative inputs."""
        from elicitation_result import _is_approval

        assert not _is_approval("no"), "'no' should not be affirmative"
        assert not _is_approval("nope"), "'nope' should not be affirmative"
        assert not _is_approval("cancel"), "'cancel' should not be affirmative"
        assert not _is_approval("Reject"), "'Reject' should not be affirmative"
        assert not _is_approval("Modify"), "'Modify' should not be affirmative"

    def test_negative_response_leaves_pending_intact(self):
        """A negative response should not activate pending approvals."""
        from elicitation_result import _is_approval

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id="test-cycle-session",
        )

        # Simulate a negative response -- should NOT activate
        assert not _is_approval("Reject")

        # Pending should still be there
        pending = get_pending_approvals_for_session("test-cycle-session")
        assert len(pending) >= 1, "Pending should still exist after negative response"

        # Grant should NOT exist
        grant = check_approval_grant("terraform apply")
        assert grant is None, "No grant should exist after negative response"


class TestExpiredPendingNotActivated:
    """Test 6: Expired pending is not activated."""

    def test_expired_pending_fails_activation(self):
        """A pending approval with expired TTL should not activate."""
        import modules.security.approval_grants as ag

        nonce = generate_nonce()
        # Write a pending with a very short TTL
        # Note: ttl_minutes=0 means "no expiry" in the code, so use ttl_minutes=1
        # and backdate the timestamp so the 1-minute TTL has elapsed.
        path = write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id="test-cycle-session",
            ttl_minutes=1,
        )
        assert path is not None

        # Manually backdate the pending file timestamp to ensure expiry
        import json
        data = json.loads(path.read_text())
        data["timestamp"] = time.time() - 3600  # 1 hour ago
        path.write_text(json.dumps(data))

        # Attempt activation -- should fail due to expiry
        result = activate_pending_approval(
            nonce=nonce,
            session_id="test-cycle-session",
        )
        assert not result.success, "Expired pending should not activate"
        assert result.status == ACTIVATION_EXPIRED

        # Verify no grant was created
        grant = check_approval_grant("terraform apply")
        assert grant is None, "No grant should exist after expired pending activation"


class TestApprovalResponsePatterns:
    """Test 7: Approval response pattern matching (ElicitationResult)."""

    @pytest.fixture(autouse=True)
    def import_checker(self):
        """Import the _is_approval function from elicitation_result."""
        sys.path.insert(0, str(HOOKS_DIR))
        from elicitation_result import _is_approval
        self._is_approval = _is_approval

    @pytest.mark.parametrize("text", [
        "Approve", "approve", "Approved", "yes", "Yes",
        "accept", "Accept", "confirm", "Confirm", "allow", "Allow",
    ], ids=lambda t: f"approve:{t}")
    def test_approval_responses(self, text):
        """Approval responses (structured AskUserQuestion options) should match."""
        assert self._is_approval(text), f"'{text}' should be detected as approval"

    @pytest.mark.parametrize("text", [
        "Reject", "reject", "Modify", "modify", "no", "nope",
        "cancel", "", "maybe", "let me think",
    ], ids=lambda t: f"neg:{t}" if t else "neg:empty")
    def test_non_approval_responses(self, text):
        """Non-approval responses should NOT match."""
        if text == "":
            # Empty string edge case
            assert not self._is_approval(text), "empty should not be approval"
        else:
            assert not self._is_approval(text), f"'{text}' should not be approval"

    def test_approve_in_longer_text(self):
        """Approve keyword embedded in longer text should match."""
        assert self._is_approval("Approve -- Allow the operation to proceed")
        assert self._is_approval("I approve this change")

    def test_case_insensitive(self):
        """Matching should be case-insensitive."""
        assert self._is_approval("APPROVE")
        assert self._is_approval("YES")
        assert self._is_approval("Confirm")


class TestSubagentRetryReusesPendingNonce:
    """Test 8: Subagent retry reuses existing pending nonce."""

    def test_retry_reuses_existing_pending_approval(self):
        """When a pending approval exists, retry returns the same approval_id."""
        command = "git push origin main"
        session_id = "test-cycle-session"

        # First attempt: generates a new nonce
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed
        reason1 = result1.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        import re
        match1 = re.search(r"approval_id:\s*(\w+)", reason1)
        assert match1, f"Could not extract approval_id from first attempt: {reason1}"
        nonce1 = match1.group(1)

        # Second attempt (retry): should reuse the same nonce
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result2.allowed
        reason2 = result2.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        match2 = re.search(r"approval_id:\s*(\w+)", reason2)
        assert match2, f"Could not extract approval_id from retry: {reason2}"
        nonce2 = match2.group(1)

        assert nonce1 == nonce2, (
            f"Retry should reuse the same nonce: first={nonce1}, retry={nonce2}"
        )

    def test_footer_stripping_does_not_break_pending_reuse(self):
        """Push with footer stripped on first attempt matches on retry.

        Regression test: footer stripping must happen before
        write_pending_approval so the stored command matches the stripped
        command on retry (when the footer may or may not be present).

        Note: git commit was removed from MUTATIVE_VERBS in v5.
        This test now uses git push which is still mutative.
        """
        command_with_footer = (
            'git push origin feat/api\n\n'
            'Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>'
        )
        command_without_footer = 'git push origin feat/api'
        session_id = "test-cycle-session"

        # First attempt: command includes a Co-Authored-By footer
        result1 = validate_bash_command(
            command_with_footer, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed
        reason1 = result1.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        import re
        match1 = re.search(r"approval_id:\s*(\w+)", reason1)
        assert match1, f"Could not extract approval_id from first attempt: {reason1}"
        nonce1 = match1.group(1)

        # Footer should be stripped from the deny message
        assert "Co-Authored-By" not in reason1, (
            "Footer should be stripped before building the deny message"
        )

        # Second attempt: same command without footer (agent stopped adding it)
        result2 = validate_bash_command(
            command_without_footer, is_subagent=True, session_id=session_id,
        )
        assert not result2.allowed
        reason2 = result2.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        match2 = re.search(r"approval_id:\s*(\w+)", reason2)
        assert match2, f"Could not extract approval_id from retry: {reason2}"
        nonce2 = match2.group(1)

        assert nonce1 == nonce2, (
            f"Footer-stripped pending should match clean retry: "
            f"first={nonce1}, retry={nonce2}"
        )

    def test_t3_blocked_message_instructs_no_retry(self):
        """The T3_BLOCKED deny message must tell the subagent not to retry."""
        result = validate_bash_command(
            "terraform apply",
            is_subagent=True,
            session_id="test-cycle-session",
        )
        assert not result.allowed
        reason = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Do NOT retry" in reason, (
            f"T3_BLOCKED message should instruct not to retry, got: {reason}"
        )
        assert "REVIEW" in reason, (
            f"T3_BLOCKED message should mention REVIEW status, got: {reason}"
        )


class TestConsumeGrant:
    """Test 9: consume_grant() marks grant as used (single-use)."""

    def test_consume_grant_marks_used(self):
        """consume_grant() sets used=True and persists to disk."""
        nonce = generate_nonce()
        command = "terraform apply"
        session_id = "test-cycle-session"

        # Create a pending approval and activate it
        write_pending_approval(
            nonce=nonce,
            command=command,
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )
        result = activate_pending_approval(nonce=nonce, session_id=session_id)
        assert result.success, f"Activation should succeed: {result.reason}"

        # Verify grant exists before consume
        grant = check_approval_grant(command, session_id=session_id)
        assert grant is not None, "Grant should exist before consume"

        # Consume the grant
        consumed = consume_grant(command, session_id=session_id)
        assert consumed, "consume_grant() should return True"

        # After consume, check_approval_grant should return None (used=True)
        grant_after = check_approval_grant(command, session_id=session_id)
        assert grant_after is None, (
            "check_approval_grant() should return None after grant is consumed"
        )

    def test_consume_grant_second_call_returns_false(self):
        """Second call to consume_grant() returns False (already consumed)."""
        nonce = generate_nonce()
        command = "git push origin main"
        session_id = "test-cycle-session"

        write_pending_approval(
            nonce=nonce,
            command=command,
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_id,
        )
        activate_pending_approval(nonce=nonce, session_id=session_id)

        # First consume succeeds
        assert consume_grant(command, session_id=session_id) is True

        # Second consume fails (grant already used)
        assert consume_grant(command, session_id=session_id) is False

    def test_consume_nonexistent_grant_returns_false(self):
        """consume_grant() returns False when no matching grant exists."""
        consumed = consume_grant("terraform destroy", session_id="test-cycle-session")
        assert consumed is False


class TestDefaultTTL:
    """Test 10: DEFAULT_GRANT_TTL_MINUTES is 5."""

    def test_default_ttl_is_five_minutes(self):
        """DEFAULT_GRANT_TTL_MINUTES should be 5."""
        assert DEFAULT_GRANT_TTL_MINUTES == 5, (
            f"Expected TTL=5, got {DEFAULT_GRANT_TTL_MINUTES}"
        )


class TestConditionalActivation:
    """Test 11: Conditional activation based on answers in AskUserQuestion."""

    @pytest.fixture(autouse=True)
    def setup_adapter(self):
        """Import the adapter for testing."""
        ADAPTERS_DIR = HOOKS_DIR / "adapters"
        sys.path.insert(0, str(ADAPTERS_DIR))
        from adapters.claude_code import ClaudeCodeAdapter
        self.adapter = ClaudeCodeAdapter()

    def _make_hook_data(self, answers=None, session_id="test-cycle-session"):
        """Build a minimal AskUserQuestion PostToolUse hook_data."""
        data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {},
            "tool_response": {},
        }
        if answers is not None:
            data["tool_response"] = {"answers": answers}
        return data

    def test_approve_answer_activates_grants(self):
        """Answers containing 'Approve' should activate pending grants."""
        session_id = "test-cycle-session"
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        hook_data = self._make_hook_data(
            answers={"Proceed with terraform apply?": "Approve (Recommended)"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        # Grant should now be active
        grant = check_approval_grant("terraform apply", session_id=session_id)
        assert grant is not None, "Grant should be active after user approved"

    def test_reject_answer_does_not_activate_grants(self):
        """Answers containing 'Reject' should NOT activate pending grants."""
        session_id = "test-cycle-session"
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        hook_data = self._make_hook_data(
            answers={"Proceed with terraform apply?": "Reject"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        # Grant should NOT be active
        grant = check_approval_grant("terraform apply", session_id=session_id)
        assert grant is None, "Grant should NOT be active after user rejected"

    def test_modify_answer_does_not_activate_grants(self):
        """Answers containing 'Modify' should NOT activate pending grants."""
        session_id = "test-cycle-session"
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        hook_data = self._make_hook_data(
            answers={"Allow git push?": "Modify"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        grant = check_approval_grant("git push origin main", session_id=session_id)
        assert grant is None, "Grant should NOT be active after user chose Modify"

    def test_no_answers_does_not_activate_grants(self):
        """Missing answers field should NOT activate pending grants."""
        session_id = "test-cycle-session"
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        # No answers in payload
        hook_data = self._make_hook_data(answers=None, session_id=session_id)
        self.adapter._handle_ask_user_question_result(hook_data)

        grant = check_approval_grant("terraform apply", session_id=session_id)
        assert grant is None, "Grant should NOT be active when no answers present"

    def test_approve_recommended_matches(self):
        """'Approve (Recommended)' contains 'approve' and should match."""
        session_id = "test-cycle-session"
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        hook_data = self._make_hook_data(
            answers={"q1": "Approve (Recommended)"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        grant = check_approval_grant("terraform apply", session_id=session_id)
        assert grant is not None, "'Approve (Recommended)' should activate grant"

    def test_answers_from_tool_input_fallback(self):
        """Answers in tool_input (fallback) should also be checked."""
        session_id = "test-cycle-session"
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        # Answers in tool_input instead of tool_response
        hook_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {"answers": {"q1": "Approve"}},
            "tool_response": {},
        }
        self.adapter._handle_ask_user_question_result(hook_data)

        grant = check_approval_grant("terraform apply", session_id=session_id)
        assert grant is not None, "Answers from tool_input fallback should work"


class TestConsumeGrantAtSubagentStop:
    """Test 12: grants live for the full subagent session, consumed at SubagentStop."""

    def test_full_cycle_grant_consumed_at_subagent_stop(self):
        """After deny -> activate -> passthrough -> confirm -> SubagentStop consume.

        Grants survive PostToolUse (only confirmed there) and are consumed
        when the subagent session ends via consume_session_grants().
        """
        command = "terraform apply"
        session_id = "test-cycle-session"

        # Step 1: Subagent command denied
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed

        # Step 2: Activate grants
        results = activate_grants_for_session(session_id)
        assert len(results) >= 1
        assert results[0].success

        # Step 3: Retry - passthrough (active grant)
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert result2.allowed, "Active grant should passthrough"

        # Step 4: Confirm the grant (as PostToolUse would after execution)
        confirmed = confirm_grant(command, session_id=session_id)
        assert confirmed

        # Step 5: Grant is still usable (not consumed yet -- lives for session)
        result3 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert result3.allowed, "Confirmed grant should still be usable within the session"

        # Step 6: SubagentStop consumes all confirmed grants
        consumed = consume_session_grants(session_id)
        assert consumed >= 1, "SubagentStop should consume confirmed grants"

        # Step 7: Same command should now be blocked (grant consumed)
        result4 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result4.allowed, "Command should be blocked after SubagentStop consumed grant"
