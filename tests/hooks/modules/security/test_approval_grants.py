#!/usr/bin/env python3
"""
Tests for approval grants -- T3 command passthrough after user approval.

PRIORITY: HIGH -- Critical for the approval flow to work end-to-end.

Validates:
1. Grant creation from approval scope
2. Verb extraction from scope strings
3. Grant matching against commands
4. Grant expiry and cleanup
5. Security properties (deny list bypass not possible)
6. Nonce generation and pending approval lifecycle
7. Nonce activation with session/TTL validation
"""

import json
import os
import re
import sys
import time
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_grants import (
    ApprovalGrant,
    write_approval_grant,
    check_approval_grant,
    consume_grant,
    cleanup_expired_grants,
    generate_nonce,
    write_pending_approval,
    activate_pending_approval,
    _extract_verbs_from_scope,
    _get_grants_dir,
    ACTIVATION_ACTIVATED,
    ACTIVATION_EXPIRED,
    ACTIVATION_NOT_FOUND,
    ACTIVATION_SESSION_MISMATCH,
)
from modules.security.approval_scopes import (
    SCOPE_RESOURCE_FAMILY,
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def clean_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for grants and clean up after each test."""
    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    # Patch find_claude_dir to return our temp .claude dir
    monkeypatch.setattr(
        "modules.security.approval_grants.find_claude_dir",
        lambda: tmp_path / ".claude",
    )
    # Set a predictable session ID
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-session-123")
    yield grants_dir


# ============================================================================
# Verb Extraction
# ============================================================================

class TestVerbExtraction:
    """Test _extract_verbs_from_scope."""

    def test_extracts_commit(self):
        verbs = _extract_verbs_from_scope("git commit")
        assert "commit" in verbs

    def test_extracts_push(self):
        verbs = _extract_verbs_from_scope("git push origin feature/branch")
        assert "push" in verbs

    def test_extracts_apply(self):
        verbs = _extract_verbs_from_scope("terraform apply prod/vpc")
        assert "apply" in verbs

    def test_extracts_kubectl_apply(self):
        verbs = _extract_verbs_from_scope("kubectl apply namespace payment-service")
        assert "apply" in verbs

    def test_extracts_multiple_verbs(self):
        verbs = _extract_verbs_from_scope("git commit and git push")
        assert "commit" in verbs
        assert "push" in verbs

    def test_extracts_delete(self):
        verbs = _extract_verbs_from_scope("kubectl delete pod my-pod")
        assert "delete" in verbs

    def test_empty_scope_returns_empty(self):
        assert _extract_verbs_from_scope("") == []

    def test_no_verbs_found(self):
        verbs = _extract_verbs_from_scope("review the code")
        assert verbs == []

    def test_extracts_destroy(self):
        verbs = _extract_verbs_from_scope("terraform destroy prod/vpc")
        assert "destroy" in verbs


# ============================================================================
# ApprovalGrant Dataclass
# ============================================================================

class TestApprovalGrant:
    """Test ApprovalGrant dataclass methods."""

    def test_valid_grant(self):
        grant = ApprovalGrant(
            session_id="test",
            approved_verbs=["commit"],
            approved_scope="git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_RESOURCE_FAMILY,
            ).to_dict(),
            granted_at=time.time(),
            ttl_minutes=10,
        )
        assert grant.is_valid()
        assert not grant.is_expired()
        assert not grant.used

    def test_expired_grant(self):
        grant = ApprovalGrant(
            session_id="test",
            approved_verbs=["commit"],
            approved_scope="git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_RESOURCE_FAMILY,
            ).to_dict(),
            granted_at=time.time() - 700,  # 11+ minutes ago
            ttl_minutes=10,
        )
        assert grant.is_expired()
        assert not grant.is_valid()

    def test_used_grant(self):
        grant = ApprovalGrant(
            session_id="test",
            approved_verbs=["commit"],
            approved_scope="git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_RESOURCE_FAMILY,
            ).to_dict(),
            granted_at=time.time(),
            ttl_minutes=10,
            used=True,
        )
        assert not grant.is_valid()

    def test_resource_family_matches_git_commit(self):
        grant = ApprovalGrant(
            approved_verbs=["commit"],
            approved_scope="git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_RESOURCE_FAMILY,
            ).to_dict(),
        )
        assert grant.matches_command("git commit -m 'feat: add feature'")

    def test_semantic_signature_matches_exact_retry(self):
        grant = ApprovalGrant(
            approved_verbs=["push"],
            approved_scope="git push origin feature/branch",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            scope_signature=build_approval_signature(
                "git push origin feature/branch",
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
            ).to_dict(),
        )
        assert grant.matches_command("git push origin feature/branch")

    def test_semantic_signature_rejects_cross_cli_same_verb(self):
        grant = ApprovalGrant(
            approved_verbs=["apply"],
            approved_scope="terraform apply prod/vpc",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            scope_signature=build_approval_signature(
                "terraform apply prod/vpc",
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
            ).to_dict(),
        )
        assert not grant.matches_command("kubectl apply -f prod.yaml")

    def test_does_not_match_unrelated_command(self):
        grant = ApprovalGrant(
            approved_verbs=["commit"],
            approved_scope="git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_RESOURCE_FAMILY,
            ).to_dict(),
        )
        assert not grant.matches_command("git push origin main")

    def test_does_not_match_missing_signature(self):
        grant = ApprovalGrant(approved_verbs=["commit"])
        assert not grant.matches_command("git commit")

    def test_resource_family_rejects_more_dangerous_variant(self):
        grant = ApprovalGrant(
            approved_verbs=["push"],
            approved_scope="git push origin main",
            scope_type=SCOPE_RESOURCE_FAMILY,
            scope_signature=build_approval_signature(
                "git push origin main",
                scope_type=SCOPE_RESOURCE_FAMILY,
            ).to_dict(),
        )
        assert not grant.matches_command("git push origin main --force")


# ============================================================================
# Nonce Generation
# ============================================================================

class TestNonceGeneration:
    """Test generate_nonce()."""

    def test_nonce_is_32_char_hex(self):
        nonce = generate_nonce()
        assert len(nonce) == 32
        assert re.match(r'^[a-f0-9]{32}$', nonce)

    def test_nonces_are_unique(self):
        nonces = {generate_nonce() for _ in range(100)}
        assert len(nonces) == 100, "Nonces should be unique"

    def test_nonce_matches_approval_pattern(self):
        """Nonce should match the NONCE_APPROVAL_PATTERN regex."""
        from modules.security.approval_constants import NONCE_APPROVAL_PATTERN
        nonce = generate_nonce()
        text = f"APPROVE:{nonce}"
        match = NONCE_APPROVAL_PATTERN.search(text)
        assert match is not None
        assert match.group(1) == nonce


# ============================================================================
# Pending Approval
# ============================================================================

class TestPendingApproval:
    """Test write_pending_approval()."""

    def test_creates_pending_file(self, clean_grants_dir):
        nonce = generate_nonce()
        path = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        assert path is not None
        assert path.exists()
        assert path.name == f"pending-{nonce}.json"

    def test_pending_file_content(self, clean_grants_dir):
        nonce = generate_nonce()
        path = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        data = json.loads(path.read_text())
        assert data["nonce"] == nonce
        assert data["session_id"] == "test-session-123"
        assert data["command"] == "git commit -m 'feat: test'"
        assert data["danger_verb"] == "commit"
        assert data["danger_category"] == "MUTATIVE"
        assert data["scope_type"] == SCOPE_SEMANTIC_SIGNATURE
        assert data["scope_signature"]["scope_type"] == SCOPE_SEMANTIC_SIGNATURE
        assert data["ttl_minutes"] == 10
        assert "timestamp" in data

    def test_pending_file_custom_session(self, clean_grants_dir):
        nonce = generate_nonce()
        path = write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id="custom-session",
        )
        data = json.loads(path.read_text())
        assert data["session_id"] == "custom-session"


# ============================================================================
# Pending Approval Activation
# ============================================================================

class TestActivatePendingApproval:
    """Test activate_pending_approval()."""

    def test_activates_pending_to_grant(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        result = activate_pending_approval(nonce)
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert result.grant_path is not None
        assert result.grant_path.exists()
        assert result.grant_path.name.startswith("grant-")

    def test_activation_deletes_pending_file(self, clean_grants_dir):
        nonce = generate_nonce()
        pending_path = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        activate_pending_approval(nonce)
        assert not pending_path.exists(), "Pending file should be deleted after activation"

    def test_activated_grant_matches_exact_command(self, clean_grants_dir):
        nonce = generate_nonce()
        command = "git commit -m 'feat: test'"
        write_pending_approval(
            nonce=nonce,
            command=command,
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        activate_pending_approval(nonce)
        # Now check the active grant
        grant = check_approval_grant(command)
        assert grant is not None
        assert "commit" in grant.approved_verbs

    def test_activation_fails_for_nonexistent_nonce(self, clean_grants_dir):
        result = activate_pending_approval("deadbeef" * 4)
        assert result.success is False
        assert result.status == ACTIVATION_NOT_FOUND

    def test_activation_fails_for_wrong_session(self, clean_grants_dir, monkeypatch):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        # Change session
        monkeypatch.setenv("CLAUDE_SESSION_ID", "different-session")
        result = activate_pending_approval(nonce)
        assert result.success is False, "Activation should fail with wrong session ID"
        assert result.status == ACTIVATION_SESSION_MISMATCH

    def test_activation_fails_for_expired_pending(self, clean_grants_dir):
        nonce = generate_nonce()
        # Write pending with 0 TTL (immediately expired)
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
            ttl_minutes=0,
        )
        time.sleep(0.1)  # ensure expiry
        result = activate_pending_approval(nonce)
        assert result.success is False, "Activation should fail for expired pending"
        assert result.status == ACTIVATION_EXPIRED

    def test_activation_is_one_time_only(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        # First activation succeeds
        result1 = activate_pending_approval(nonce)
        assert result1.success is True
        # Second activation fails (pending file already deleted)
        result2 = activate_pending_approval(nonce)
        assert result2.success is False, "Second activation of same nonce should fail"
        assert result2.status == ACTIVATION_NOT_FOUND

    def test_activation_uses_danger_verb_fallback(self, clean_grants_dir):
        """If verb extraction from command fails, use danger_verb directly."""
        nonce = generate_nonce()
        # Command without a recognized verb (edge case)
        write_pending_approval(
            nonce=nonce,
            command="some-custom-tool do-something",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        result = activate_pending_approval(nonce)
        assert result.success is True
        assert result.grant_path is not None
        # Grant should have the fallback verb
        data = json.loads(result.grant_path.read_text())
        assert "commit" in data["approved_verbs"]
        assert data["scope_type"] == SCOPE_SEMANTIC_SIGNATURE


# ============================================================================
# Write and Check Grants (legacy path)
# ============================================================================

class TestWriteAndCheckGrants:
    """Test the write/check/cleanup lifecycle."""

    def test_write_grant_creates_file(self, clean_grants_dir):
        path = write_approval_grant("git commit")
        assert path is not None
        assert path.exists()
        assert path.suffix == ".json"

    def test_write_grant_content(self, clean_grants_dir):
        path = write_approval_grant("git commit")
        data = json.loads(path.read_text())
        assert data["session_id"] == "test-session-123"
        assert "commit" in data["approved_verbs"]
        assert data["approved_scope"] == "git commit"
        assert data["scope_type"] == SCOPE_RESOURCE_FAMILY
        assert data["scope_signature"]["scope_type"] == SCOPE_RESOURCE_FAMILY
        assert data["ttl_minutes"] == 10
        assert data["used"] is False

    def test_write_grant_no_verbs_returns_none(self, clean_grants_dir):
        """If no verbs can be extracted, no grant should be written."""
        path = write_approval_grant("review the changes")
        assert path is None

    def test_check_finds_matching_grant(self, clean_grants_dir):
        write_approval_grant("git commit")
        grant = check_approval_grant("git commit -m 'feat: add feature'")
        assert grant is not None
        assert "commit" in grant.approved_verbs

    def test_check_rejects_force_push_variant_for_same_family(self, clean_grants_dir):
        write_approval_grant("git push origin main")
        grant = check_approval_grant("git push origin main --force")
        assert grant is None

    def test_check_rejects_cross_cli_same_verb(self, clean_grants_dir):
        write_approval_grant("terraform apply prod/vpc")
        grant = check_approval_grant("kubectl apply -f prod.yaml")
        assert grant is None

    def test_check_returns_none_when_no_match(self, clean_grants_dir):
        write_approval_grant("git commit")
        grant = check_approval_grant("git push origin main")
        assert grant is None

    def test_check_returns_none_for_expired_grant(self, clean_grants_dir):
        path = write_approval_grant("git commit", ttl_minutes=0)
        # Grant with ttl=0 is immediately expired
        # Wait a tiny bit to ensure expiry
        time.sleep(0.1)
        grant = check_approval_grant("git commit -m 'test'")
        assert grant is None

    def test_check_returns_none_when_no_grants_exist(self, clean_grants_dir):
        grant = check_approval_grant("git commit")
        assert grant is None

    def test_multiple_grants_work(self, clean_grants_dir):
        write_approval_grant("git commit")
        time.sleep(0.01)  # ensure different timestamps
        write_approval_grant("git push origin feature/branch")

        grant1 = check_approval_grant("git commit -m 'feat: test'")
        assert grant1 is not None

        grant2 = check_approval_grant("git push origin feature/branch")
        assert grant2 is not None

    def test_grant_only_matches_own_session(self, clean_grants_dir, monkeypatch):
        """Grants from a different session should not match."""
        write_approval_grant("git commit")

        # Change session ID
        monkeypatch.setenv("CLAUDE_SESSION_ID", "different-session")
        grant = check_approval_grant("git commit -m 'test'")
        assert grant is None


# ============================================================================
# Cleanup
# ============================================================================

class TestCleanup:
    """Test grant cleanup."""

    def test_cleanup_removes_expired(self, clean_grants_dir):
        # Create a grant that is already expired
        grant_file = clean_grants_dir / "grant-test-session-123-1000000.json"
        grant_file.write_text(json.dumps({
            "session_id": "test-session-123",
            "approved_verbs": ["commit"],
            "approved_scope": "git commit",
            "granted_at": 1000000.0,  # way in the past
            "ttl_minutes": 10,
            "used": False,
        }))

        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not grant_file.exists()

    def test_cleanup_preserves_valid(self, clean_grants_dir):
        path = write_approval_grant("git commit")
        cleaned = cleanup_expired_grants()
        assert cleaned == 0
        assert path.exists()

    def test_cleanup_removes_corrupt_files(self, clean_grants_dir):
        corrupt_file = clean_grants_dir / "grant-test-session-123-999.json"
        corrupt_file.write_text("not valid json{{{")
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not corrupt_file.exists()

    def test_cleanup_removes_expired_pending(self, clean_grants_dir):
        """Expired pending approval files should be cleaned up."""
        nonce = generate_nonce()
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        pending_file.write_text(json.dumps({
            "nonce": nonce,
            "session_id": "test-session-123",
            "command": "git commit -m 'test'",
            "danger_verb": "commit",
            "danger_category": "MUTATIVE",
            "timestamp": 1000000.0,  # way in the past
            "ttl_minutes": 10,
        }))
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not pending_file.exists()

    def test_cleanup_preserves_valid_pending(self, clean_grants_dir):
        """Non-expired pending files should be preserved."""
        nonce = generate_nonce()
        path = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        cleaned = cleanup_expired_grants()
        assert cleaned == 0
        assert path.exists()

    def test_cleanup_removes_corrupt_pending(self, clean_grants_dir):
        corrupt_file = clean_grants_dir / "pending-abcdef0123456789abcdef0123456789.json"
        corrupt_file.write_text("not valid json{{{")
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not corrupt_file.exists()


# ============================================================================
# End-to-End Nonce Flow
# ============================================================================

class TestNonceEndToEnd:
    """Test the full nonce-based approval flow."""

    def test_full_flow_block_activate_allow(self, clean_grants_dir):
        """Simulate the complete flow: block -> pending -> activate -> allow."""
        from modules.tools.bash_validator import BashValidator

        # Step 1: First attempt -- command is blocked, pending file is written
        validator = BashValidator()
        result = validator.validate('git commit -m "feat(auth): add login endpoint"')
        assert result.allowed is False
        assert result.block_response is not None

        # Extract nonce from the block response message
        block_msg = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        nonce_match = re.search(r"NONCE:([a-f0-9]{32})", block_msg)
        assert nonce_match is not None, f"Block response should contain NONCE, got: {block_msg}"
        nonce = nonce_match.group(1)

        # Verify pending file exists
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        assert pending_file.exists()

        # Step 2: Activate the pending approval (simulates orchestrator resume)
        activation = activate_pending_approval(nonce)
        assert activation.success is True

        # Pending file should be gone
        assert not pending_file.exists()

        # Step 3: Retry the command -- should be allowed now
        result2 = validator.validate('git commit -m "feat(auth): add login endpoint"')
        assert result2.allowed is True, (
            f"Command should be allowed after nonce activation, "
            f"but got: allowed={result2.allowed}, reason={result2.reason}"
        )


# ============================================================================
# Integration with BashValidator
# ============================================================================

class TestBashValidatorIntegration:
    """Test that approval grants integrate correctly with BashValidator."""

    def test_git_commit_allowed_with_grant(self, clean_grants_dir):
        """The critical test: git commit should be allowed when a grant exists."""
        from modules.tools.bash_validator import BashValidator

        # Write a grant for git commit
        write_approval_grant("git commit")

        # Now validate a git commit command
        validator = BashValidator()
        result = validator.validate('git commit -m "feat(auth): add login endpoint"')
        assert result.allowed is True, (
            f"git commit should be allowed with an active grant, "
            f"but got: allowed={result.allowed}, reason={result.reason}"
        )

    def test_git_push_allowed_with_grant(self, clean_grants_dir):
        """git push should be allowed when a grant exists."""
        from modules.tools.bash_validator import BashValidator

        write_approval_grant("git push origin feature/branch")
        validator = BashValidator()
        result = validator.validate("git push origin feature/branch")
        assert result.allowed is True

    def test_terraform_apply_allowed_with_grant(self, clean_grants_dir):
        """terraform apply should be allowed when a grant exists."""
        from modules.tools.bash_validator import BashValidator

        write_approval_grant("terraform apply")
        validator = BashValidator()
        result = validator.validate("terraform apply -auto-approve")
        assert result.allowed is True

    def test_nonce_grant_does_not_cross_cli_same_verb(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="terraform apply prod/vpc",
            danger_verb="apply",
            danger_category="MUTATIVE",
        )
        result = activate_pending_approval(nonce)
        assert result.success is True
        assert check_approval_grant("kubectl apply -f prod.yaml") is None

    def test_nonce_grant_does_not_escalate_to_more_dangerous_variant(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
        )
        result = activate_pending_approval(nonce)
        assert result.success is True
        assert check_approval_grant("git push origin main --force") is None

    def test_nonce_grant_does_not_jump_resource_kind(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="kubectl delete pod pod-1",
            danger_verb="delete",
            danger_category="DESTRUCTIVE",
        )
        result = activate_pending_approval(nonce)
        assert result.success is True
        assert check_approval_grant("kubectl delete namespace prod") is None

    def test_legacy_verb_only_grant_file_does_not_match(self, clean_grants_dir):
        legacy_file = clean_grants_dir / "grant-test-session-123-legacy.json"
        legacy_file.write_text(json.dumps({
            "session_id": "test-session-123",
            "approved_verbs": ["apply"],
            "approved_scope": "terraform apply prod/vpc",
            "granted_at": time.time(),
            "ttl_minutes": 10,
            "used": False,
        }))
        assert check_approval_grant("terraform apply prod/vpc") is None

    def test_git_commit_still_blocked_without_grant(self, clean_grants_dir):
        """Without a grant, git commit should still be blocked."""
        from modules.tools.bash_validator import BashValidator

        validator = BashValidator()
        result = validator.validate('git commit -m "feat: test"')
        assert result.allowed is False
        assert result.block_response is not None

    def test_block_response_contains_nonce(self, clean_grants_dir):
        """Block response should contain a NONCE for the agent to present."""
        from modules.tools.bash_validator import BashValidator

        validator = BashValidator()
        result = validator.validate('git commit -m "feat: test"')
        assert result.allowed is False
        assert result.block_response is not None

        block_msg = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        assert "NONCE:" in block_msg, f"Block response should contain NONCE, got: {block_msg}"

        # Verify nonce format
        nonce_match = re.search(r"NONCE:([a-f0-9]{32})", block_msg)
        assert nonce_match is not None

    def test_block_creates_pending_file(self, clean_grants_dir):
        """Blocking a command should create a pending approval file."""
        from modules.tools.bash_validator import BashValidator

        validator = BashValidator()
        result = validator.validate('git commit -m "feat: test"')

        # Extract nonce
        block_msg = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        nonce_match = re.search(r"NONCE:([a-f0-9]{32})", block_msg)
        nonce = nonce_match.group(1)

        # Verify pending file
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        assert pending_file.exists()

        data = json.loads(pending_file.read_text())
        assert data["nonce"] == nonce
        assert data["session_id"] == "test-session-123"
        assert "commit" in data["command"]

    def test_deny_list_not_bypassed(self, clean_grants_dir):
        """Deny list commands should NEVER be bypassed by grants."""
        from modules.tools.bash_validator import BashValidator

        # Even with a grant, deny-listed commands must stay blocked
        write_approval_grant("kubectl delete namespace production")
        validator = BashValidator()
        result = validator.validate("kubectl delete namespace production")
        # blocked_commands check runs BEFORE dangerous verb check,
        # so deny list is never bypassed
        assert result.allowed is False
