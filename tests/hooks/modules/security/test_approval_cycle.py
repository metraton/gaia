#!/usr/bin/env python3
"""Tests for the full approval cycle: deny -> activate -> retry.

Validates the end-to-end approval flow introduced by the unified REVIEW
status and approval_id mechanism:

1. Subagent mutative command gets denied with approval_id
2. Orchestrator mutative command gets "ask" (no approval_id)
3. UserPromptSubmit activates grant for pending approval
4. Full cycle: deny -> approve -> retry succeeds
5. Negative response does NOT activate grant
6. Expired pending is not activated
7. Approval response pattern matching (_is_approval_response)
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
    ApprovalGrant,
    activate_grants_for_session,
    activate_pending_approval,
    check_approval_grant,
    confirm_grant,
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


class TestUserPromptSubmitActivatesGrant:
    """Test 3: UserPromptSubmit activates grant for pending approval."""

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

        # 2. Activate grants for the session (simulates UserPromptSubmit)
        results = activate_grants_for_session("test-cycle-session")
        assert len(results) >= 1, "Expected at least one activation result"
        assert results[0].success, f"Activation should succeed: {results[0].reason}"

        # 3. Check that the grant is now active
        grant = check_approval_grant(command)
        assert grant is not None, "Grant should be active after activation"
        assert grant.approved_scope == command


class TestFullApprovalCycle:
    """Test 4: Full cycle -- deny, approve, retry succeeds."""

    def test_deny_activate_retry_succeeds(self):
        """Complete cycle: subagent denied, approval activated, retry allowed."""
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

        # Step 2: Activate grants for the session (simulates UserPromptSubmit)
        results = activate_grants_for_session(session_id)
        assert len(results) >= 1
        assert results[0].success, f"Activation failed: {results[0].reason}"

        # Step 3: Retry the same command -- should get "ask" (unconfirmed grant)
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result2.allowed, "Unconfirmed grant should return ask"
        hook_output2 = result2.block_response["hookSpecificOutput"]
        assert hook_output2["permissionDecision"] == "ask", (
            f"Expected ask for unconfirmed grant, got: {hook_output2['permissionDecision']}"
        )
        assert "Confirm execution" in hook_output2["permissionDecisionReason"]

        # Step 4: Confirm the grant (simulates post_tool_use after native dialog)
        confirmed = confirm_grant(command)
        assert confirmed, "Grant confirmation should succeed"

        # Step 5: Retry again -- should be fully allowed
        result3 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert result3.allowed, "Confirmed grant should allow the command"


class TestNegativeResponseDoesNotActivate:
    """Test 5: Negative response does NOT activate grant."""

    def test_is_approval_response_rejects_negative(self):
        """_is_approval_response returns False for negative inputs."""
        # Import from the UserPromptSubmit hook
        sys.path.insert(0, str(HOOKS_DIR))
        from user_prompt_submit import _is_approval_response

        assert not _is_approval_response("no"), "'no' should not be affirmative"
        assert not _is_approval_response("nope"), "'nope' should not be affirmative"
        assert not _is_approval_response("cancel"), "'cancel' should not be affirmative"
        assert not _is_approval_response(""), "empty should not be affirmative"

    def test_negative_response_leaves_pending_intact(self):
        """A negative response should not activate pending approvals."""
        from user_prompt_submit import _is_approval_response

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id="test-cycle-session",
        )

        # Simulate a negative response -- should NOT activate
        assert not _is_approval_response("no")

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
        path = write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id="test-cycle-session",
            ttl_minutes=0,  # Already expired (0 minutes TTL)
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
    """Test 7: Approval response pattern matching."""

    @pytest.fixture(autouse=True)
    def import_checker(self):
        """Import the _is_approval_response function from UserPromptSubmit."""
        sys.path.insert(0, str(HOOKS_DIR))
        from user_prompt_submit import _is_approval_response
        self._is_approval_response = _is_approval_response

    @pytest.mark.parametrize("text", [
        "yes", "y", "ok", "approve", "sure", "confirm",
        "do it", "go ahead", "proceed", "go", "execute",
    ], ids=lambda t: f"en:{t}")
    def test_english_affirmative(self, text):
        """English affirmative responses should match."""
        assert self._is_approval_response(text), f"'{text}' should be affirmative"

    @pytest.mark.parametrize("text", [
        "si", "dale", "apruebo", "adelante",
        "hazlo", "confirmo", "ejecuta",
    ], ids=lambda t: f"es:{t}")
    def test_spanish_affirmative(self, text):
        """Spanish affirmative responses should match."""
        assert self._is_approval_response(text), f"'{text}' should be affirmative"

    def test_si_with_accent(self):
        """'si' with accent mark should match."""
        assert self._is_approval_response("si"), "'si' should be affirmative"

    @pytest.mark.parametrize("text", [
        "no", "nope", "cancel",
        "", "maybe", "let me think",
    ], ids=lambda t: f"neg:{t}" if t else "neg:empty")
    def test_negative_or_ambiguous(self, text):
        """Negative or ambiguous responses should NOT match."""
        assert not self._is_approval_response(text), f"'{text}' should not be affirmative"

    def test_affirmative_with_trailing_punctuation(self):
        """Trailing punctuation should be stripped before matching."""
        assert self._is_approval_response("yes!"), "'yes!' should be affirmative"
        assert self._is_approval_response("ok."), "'ok.' should be affirmative"
        assert self._is_approval_response("sure,"), "'sure,' should be affirmative"

    def test_affirmative_with_continuation(self):
        """Affirmative prefix with continuation should match."""
        assert self._is_approval_response("yes, go ahead"), "'yes, go ahead' should be affirmative"
        assert self._is_approval_response("ok proceed"), "'ok proceed' should be affirmative"
