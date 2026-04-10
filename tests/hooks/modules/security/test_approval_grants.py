#!/usr/bin/env python3
"""Tests for nonce-only approval grants."""

import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_grants import (
    ACTIVATION_ACTIVATED,
    ACTIVATION_EXPIRED,
    ACTIVATION_INVALID_PENDING,
    ACTIVATION_INVALID_SIGNATURE,
    ACTIVATION_NOT_FOUND,
    ACTIVATION_SESSION_MISMATCH,
    ApprovalGrant,
    activate_pending_approval,
    check_approval_grant,
    cleanup_expired_grants,
    confirm_grant,
    generate_nonce,
    get_latest_pending_approval,
    write_pending_approval,
)
from modules.security.approval_scopes import (
    SCOPE_EXACT_COMMAND,
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
)


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
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-session-123")
    # Reset cleanup throttle and mkdir cache so each test starts clean
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False
    yield grants_dir


def _write_active_grant(
    grants_dir: Path,
    command: str,
    *,
    scope_type: str = SCOPE_SEMANTIC_SIGNATURE,
    ttl_minutes: int = 10,
    granted_at: Optional[float] = None,
    used: bool = False,
    confirmed: bool = True,
    session_id: str = "test-session-123",
) -> Path:
    """Write an active grant file directly for grant-matching tests.

    Note: ``confirmed`` defaults to True so that grant-matching tests
    exercise the "auto-allow" path. Set ``confirmed=False`` to test the
    double-barrier "ask" flow.
    """
    signature = build_approval_signature(command, scope_type=scope_type)
    assert signature is not None
    grant = ApprovalGrant(
        session_id=session_id,
        approved_verbs=[signature.verb] if signature.verb else [],
        approved_scope=command,
        scope_type=signature.scope_type,
        scope_signature=signature.to_dict(),
        granted_at=granted_at if granted_at is not None else time.time(),
        ttl_minutes=ttl_minutes,
        used=used,
        confirmed=confirmed,
    )
    grant_file = grants_dir / f"grant-{session_id}-{int(time.time() * 1000)}.json"
    grant_file.write_text(json.dumps(asdict(grant), indent=2))
    return grant_file


class TestApprovalGrant:
    """ApprovalGrant methods should remain strictly scoped."""

    def test_valid_grant(self):
        grant = ApprovalGrant(
            session_id="test",
            approved_verbs=["commit"],
            approved_scope='git commit -m "feat: test"',
            scope_type=SCOPE_EXACT_COMMAND,
            scope_signature=build_approval_signature(
                'git commit -m "feat: test"',
                scope_type=SCOPE_EXACT_COMMAND,
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
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
            ).to_dict(),
            granted_at=time.time() - 700,
            ttl_minutes=10,
        )
        assert grant.is_expired()
        assert not grant.is_valid()

    def test_used_grant(self):
        grant = ApprovalGrant(
            session_id="test",
            approved_verbs=["commit"],
            approved_scope="git commit",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            scope_signature=build_approval_signature(
                "git commit",
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
            ).to_dict(),
            granted_at=time.time(),
            ttl_minutes=10,
            used=True,
        )
        assert not grant.is_valid()

    def test_exact_command_matches_same_tokenized_command(self):
        grant = ApprovalGrant(
            approved_verbs=["commit"],
            approved_scope='git commit -m "feat: add feature"',
            scope_type=SCOPE_EXACT_COMMAND,
            scope_signature=build_approval_signature(
                'git commit -m "feat: add feature"',
                scope_type=SCOPE_EXACT_COMMAND,
            ).to_dict(),
        )
        assert grant.matches_command("git   commit   -m 'feat: add feature'")

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

    def test_semantic_signature_rejects_more_dangerous_variant(self):
        grant = ApprovalGrant(
            approved_verbs=["push"],
            approved_scope="git push origin main",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            scope_signature=build_approval_signature(
                "git push origin main",
                scope_type=SCOPE_SEMANTIC_SIGNATURE,
            ).to_dict(),
        )
        assert not grant.matches_command("git push origin main --force")

    def test_missing_signature_never_matches(self):
        grant = ApprovalGrant(approved_verbs=["commit"])
        assert not grant.matches_command("git commit")


class TestNonceGeneration:
    """Nonce generation should stay cryptographically scoped and parseable."""

    def test_nonce_is_32_char_hex(self):
        nonce = generate_nonce()
        assert len(nonce) == 32
        assert re.match(r"^[a-f0-9]{32}$", nonce)

    def test_nonces_are_unique(self):
        nonces = {generate_nonce() for _ in range(100)}
        assert len(nonces) == 100

    def test_nonce_matches_approval_pattern(self):
        from modules.security.approval_constants import NONCE_APPROVAL_PATTERN

        nonce = generate_nonce()
        match = NONCE_APPROVAL_PATTERN.search(f"APPROVE:{nonce}")
        assert match is not None
        assert match.group(1) == nonce


class TestPendingApproval:
    """Pending approval files should persist the semantic signature contract."""

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
        assert data["scope_type"] == SCOPE_SEMANTIC_SIGNATURE
        assert data["scope_signature"]["scope_type"] == SCOPE_SEMANTIC_SIGNATURE

    def test_latest_pending_index_tracks_newest_nonce(self, clean_grants_dir):
        first_nonce = generate_nonce()
        second_nonce = generate_nonce()
        write_pending_approval(
            nonce=first_nonce,
            command="git commit -m 'feat: first'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        time.sleep(0.01)
        write_pending_approval(
            nonce=second_nonce,
            command="git commit -m 'feat: second'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )

        index_file = clean_grants_dir / "pending-index-test-session-123.json"
        assert index_file.exists()

        index_data = json.loads(index_file.read_text())
        assert index_data["session_id"] == "test-session-123"
        assert index_data["latest_nonce"] == second_nonce
        assert [entry["nonce"] for entry in index_data["entries"]] == [second_nonce, first_nonce]

    def test_get_latest_pending_approval_dereferences_pending_file(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: latest'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )

        pending = get_latest_pending_approval()
        assert pending is not None
        assert pending["nonce"] == nonce
        assert pending["command"] == "git commit -m 'feat: latest'"
        assert pending["session_id"] == "test-session-123"


class TestActivatePendingApproval:
    """Pending approvals should activate only for valid nonce-backed records."""

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

    def test_activation_deletes_pending_file(self, clean_grants_dir):
        nonce = generate_nonce()
        pending_path = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        activate_pending_approval(nonce)
        assert not pending_path.exists()

    def test_activation_removes_latest_pending_index_when_last_nonce_used(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        activate_pending_approval(nonce)
        assert not (clean_grants_dir / "pending-index-test-session-123.json").exists()

    def test_activation_rebuilds_index_to_previous_pending_nonce(self, clean_grants_dir):
        first_nonce = generate_nonce()
        second_nonce = generate_nonce()
        write_pending_approval(
            nonce=first_nonce,
            command="git commit -m 'feat: first'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        time.sleep(0.01)
        write_pending_approval(
            nonce=second_nonce,
            command="git commit -m 'feat: second'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )

        activate_pending_approval(second_nonce)
        latest = get_latest_pending_approval()
        assert latest is not None
        assert latest["nonce"] == first_nonce

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
        monkeypatch.setenv("CLAUDE_SESSION_ID", "different-session")
        result = activate_pending_approval(nonce)
        assert result.success is False
        assert result.status == ACTIVATION_SESSION_MISMATCH

    def test_activation_fails_for_expired_pending(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
            ttl_minutes=0,
        )
        time.sleep(0.1)
        result = activate_pending_approval(nonce)
        assert result.success is False
        assert result.status == ACTIVATION_EXPIRED

    def test_activation_fails_for_missing_scope_signature(self, clean_grants_dir):
        nonce = generate_nonce()
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        pending_file.write_text(json.dumps({
            "nonce": nonce,
            "session_id": "test-session-123",
            "command": "git commit -m 'feat: test'",
            "danger_verb": "commit",
            "danger_category": "MUTATIVE",
            "timestamp": time.time(),
            "ttl_minutes": 10,
        }))
        result = activate_pending_approval(nonce)
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_PENDING
        assert not pending_file.exists()

    def test_activation_fails_for_unsupported_scope_type(self, clean_grants_dir):
        nonce = generate_nonce()
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        pending_file.write_text(json.dumps({
            "nonce": nonce,
            "session_id": "test-session-123",
            "command": "git commit -m 'feat: test'",
            "danger_verb": "commit",
            "danger_category": "MUTATIVE",
            "scope_signature": {
                "version": 2,
                "scope_type": "resource_family",
                "base_cmd": "git",
                "cli_family": "git",
                "danger_category": "MUTATIVE",
                "verb": "commit",
                "semantic_tokens": ["git", "commit"],
                "normalized_flags": [],
                "dangerous_flags": [],
                "exact_tokens": [],
            },
            "timestamp": time.time(),
            "ttl_minutes": 10,
        }))
        result = activate_pending_approval(nonce)
        assert result.success is False
        assert result.status == ACTIVATION_INVALID_SIGNATURE
        assert not pending_file.exists()

    def test_activation_is_one_time_only(self, clean_grants_dir):
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        result1 = activate_pending_approval(nonce)
        result2 = activate_pending_approval(nonce)
        assert result1.success is True
        assert result2.success is False
        assert result2.status == ACTIVATION_NOT_FOUND


class TestCleanup:
    """Cleanup should remove expired or unsupported approval artifacts."""

    def test_cleanup_removes_expired_grants(self, clean_grants_dir):
        _write_active_grant(
            clean_grants_dir,
            "git commit",
            granted_at=time.time() - 700,
            ttl_minutes=10,
        )
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1

    def test_cleanup_preserves_valid_grants(self, clean_grants_dir):
        path = _write_active_grant(clean_grants_dir, "git commit")
        cleaned = cleanup_expired_grants()
        assert cleaned == 0
        assert path.exists()

    def test_cleanup_removes_unsupported_grants(self, clean_grants_dir):
        grant_file = clean_grants_dir / "grant-test-session-123-legacy.json"
        grant_file.write_text(json.dumps({
            "session_id": "test-session-123",
            "approved_verbs": ["apply"],
            "approved_scope": "terraform apply",
            "scope_type": "resource_family",
            "scope_signature": {
                "version": 2,
                "scope_type": "resource_family",
            },
            "granted_at": time.time(),
            "ttl_minutes": 10,
            "used": False,
        }))
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not grant_file.exists()

    def test_cleanup_removes_expired_pending(self, clean_grants_dir):
        nonce = generate_nonce()
        pending_file = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        data = json.loads(pending_file.read_text())
        data["timestamp"] = 1000000.0
        pending_file.write_text(json.dumps(data, indent=2))
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not pending_file.exists()
        assert not (clean_grants_dir / "pending-index-test-session-123.json").exists()

    def test_cleanup_removes_pending_without_signature(self, clean_grants_dir):
        nonce = generate_nonce()
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        pending_file.write_text(json.dumps({
            "nonce": nonce,
            "session_id": "test-session-123",
            "command": "git commit -m 'test'",
            "timestamp": time.time(),
            "ttl_minutes": 10,
        }))
        cleaned = cleanup_expired_grants()
        assert cleaned >= 1
        assert not pending_file.exists()

    def test_get_latest_pending_approval_recovers_from_stale_index(self, clean_grants_dir):
        first_nonce = generate_nonce()
        second_nonce = generate_nonce()
        write_pending_approval(
            nonce=first_nonce,
            command="git commit -m 'feat: first'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        time.sleep(0.01)
        write_pending_approval(
            nonce=second_nonce,
            command="git commit -m 'feat: stale'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        index_file = clean_grants_dir / "pending-index-test-session-123.json"
        pending_file = clean_grants_dir / f"pending-{second_nonce}.json"
        pending_file.unlink()

        latest = get_latest_pending_approval()
        assert latest is not None
        assert latest["nonce"] == first_nonce
        rebuilt = json.loads(index_file.read_text())
        assert rebuilt["latest_nonce"] == first_nonce

    def test_get_latest_pending_approval_skips_expired_entries(self, clean_grants_dir):
        expired_nonce = generate_nonce()
        fresh_nonce = generate_nonce()
        expired_file = write_pending_approval(
            nonce=expired_nonce,
            command="git commit -m 'feat: old'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )
        expired_data = json.loads(expired_file.read_text())
        expired_data["timestamp"] = 1000000.0
        expired_file.write_text(json.dumps(expired_data, indent=2))

        time.sleep(0.01)
        write_pending_approval(
            nonce=fresh_nonce,
            command="git commit -m 'feat: fresh'",
            danger_verb="commit",
            danger_category="MUTATIVE",
        )

        latest = get_latest_pending_approval()
        assert latest is not None
        assert latest["nonce"] == fresh_nonce


class TestNonceEndToEnd:
    """The full nonce flow should still work end-to-end.

    The bash_validator now returns 'ask' (native dialog) for T3 commands
    without generating nonces. The nonce flow is driven by pre_tool_use.py.
    These tests exercise the approval_grants module directly.
    """

    def test_full_flow_block_activate_passthrough(self, clean_grants_dir):
        """Manually write a pending approval, activate, and verify grant passthrough.

        Note: git commit removed from MUTATIVE_VERBS in v5; uses git push instead.
        """
        from modules.tools.bash_validator import BashValidator

        command = "git push origin feat/auth"
        session_id = "test-nonce-flow"
        validator = BashValidator()

        # bash_validator returns "ask" for T3 commands (no nonce, orchestrator context)
        result = validator.validate(command)
        assert result.allowed is False
        assert result.block_response is not None
        assert result.block_response["hookSpecificOutput"]["permissionDecision"] == "ask"

        # Simulate the nonce flow that pre_tool_use.py would drive
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command=command,
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_id,
        )
        pending_file = clean_grants_dir / f"pending-{nonce}.json"
        assert pending_file.exists()

        activation = activate_pending_approval(nonce, session_id=session_id)
        assert activation.success is True
        assert not pending_file.exists()

        # After activation, grant passthrough: GAIA approved, no second dialog
        result2 = validator.validate(command, session_id=session_id)
        assert result2.allowed is True
        assert "grant active" in result2.reason.lower()

    def test_blocked_t3_returns_ask_without_nonce(self, clean_grants_dir):
        """BashValidator returns 'ask' for T3 commands without creating pending approvals.

        Note: git commit removed from MUTATIVE_VERBS in v5; uses git push instead.
        """
        from modules.tools.bash_validator import BashValidator

        result = BashValidator().validate("git push origin feat/auth")
        assert result.allowed is False
        assert result.block_response is not None
        assert result.block_response["hookSpecificOutput"]["permissionDecision"] == "ask"

        # No pending approval is created by bash_validator directly
        latest = get_latest_pending_approval()
        assert latest is None


class TestBashValidatorIntegration:
    """BashValidator must honor nonce-only grants and deny-list precedence."""

    def test_git_commit_allowed_with_matching_active_grant(self, clean_grants_dir):
        from modules.tools.bash_validator import BashValidator

        session_id = "test-session-123"
        _write_active_grant(clean_grants_dir, 'git commit -m "feat(auth): add login endpoint"', session_id=session_id)
        result = BashValidator().validate('git commit -m "feat(auth): add login endpoint"', session_id=session_id)
        assert result.allowed is True

    def test_git_push_allowed_with_matching_active_grant(self, clean_grants_dir):
        from modules.tools.bash_validator import BashValidator

        session_id = "test-session-123"
        _write_active_grant(clean_grants_dir, "git push origin feature/branch", session_id=session_id)
        result = BashValidator().validate("git push origin feature/branch", session_id=session_id)
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

    def test_unsupported_grant_file_does_not_match(self, clean_grants_dir):
        legacy_file = clean_grants_dir / "grant-test-session-123-legacy.json"
        legacy_file.write_text(json.dumps({
            "session_id": "test-session-123",
            "approved_verbs": ["apply"],
            "approved_scope": "terraform apply prod/vpc",
            "scope_type": "resource_family",
            "scope_signature": {"version": 2, "scope_type": "resource_family"},
            "granted_at": time.time(),
            "ttl_minutes": 10,
            "used": False,
        }))
        assert check_approval_grant("terraform apply prod/vpc") is None
        assert not legacy_file.exists()

    def test_block_response_returns_ask(self, clean_grants_dir):
        """BashValidator returns 'ask' for T3 commands (no nonce in response).

        Note: git commit removed from MUTATIVE_VERBS in v5; uses git push instead.
        """
        from modules.tools.bash_validator import BashValidator

        result = BashValidator().validate("git push origin feat/test")
        assert result.allowed is False
        assert result.block_response is not None
        assert result.block_response["hookSpecificOutput"]["permissionDecision"] == "ask"
        # No NONCE in the reason (nonce flow is driven by pre_tool_use.py)
        block_msg = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        assert "NONCE:" not in block_msg

    def test_block_does_not_create_pending_file(self, clean_grants_dir):
        """BashValidator no longer creates pending approval files directly."""
        from modules.tools.bash_validator import BashValidator

        BashValidator().validate('git commit -m "feat: test"')
        # No pending files should be created by bash_validator
        pending_files = list(clean_grants_dir.glob("pending-*.json"))
        assert len(pending_files) == 0

    def test_deny_list_not_bypassed(self, clean_grants_dir):
        from modules.tools.bash_validator import BashValidator

        _write_active_grant(clean_grants_dir, "kubectl delete namespace production")
        result = BashValidator().validate("kubectl delete namespace production")
        assert result.allowed is False

    def test_grant_not_marked_used_on_match(self, clean_grants_dir):
        """Grants use TTL-based expiry; the file should remain with used=False after matching."""
        grant_file = _write_active_grant(clean_grants_dir, "git push origin feature/branch")
        grant = check_approval_grant("git push origin feature/branch")
        assert grant is not None
        refreshed = json.loads(grant_file.read_text())
        assert refreshed["used"] is False
