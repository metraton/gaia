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
    activate_cross_session_pending,
    activate_grants_for_session,
    activate_pending_approval,
    check_approval_grant,
    cleanup_expired_grants,
    confirm_grant,
    generate_nonce,
    get_latest_pending_approval,
    write_pending_approval,
)

from modules.security.approval_grants import (
    extract_nonce_from_label,
    load_pending_by_nonce_prefix,
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
        pending_file = write_pending_approval(
            nonce=nonce,
            command="git commit -m 'feat: test'",
            danger_verb="commit",
            danger_category="MUTATIVE",
            ttl_minutes=1,
        )
        # Backdate the timestamp so the 1-minute TTL has elapsed
        data = json.loads(pending_file.read_text())
        data["timestamp"] = 1000000.0
        pending_file.write_text(json.dumps(data, indent=2))
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
            ttl_minutes=1,
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


class TestCrossSessionActivation:
    """activate_cross_session_pending should use explicit session_id when provided."""

    def test_uses_explicit_session_id(self, clean_grants_dir):
        """When session_id is passed, the grant is created under that ID."""
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id="prior-session-xyz",
        )
        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        pending_data = json.loads(pending_path.read_text())

        result = activate_cross_session_pending(
            pending_data,
            session_id="explicit-session-abc",
        )
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert result.grant_path is not None
        assert "grant-explicit-session-abc-" in result.grant_path.name
        grant_data = json.loads(result.grant_path.read_text())
        assert grant_data["session_id"] == "explicit-session-abc"

    def test_falls_back_to_env_session_id(self, clean_grants_dir):
        """Without explicit session_id, the env-based session ID is used."""
        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id="prior-session-xyz",
        )
        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        pending_data = json.loads(pending_path.read_text())

        result = activate_cross_session_pending(pending_data)
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert result.grant_path is not None
        assert "grant-test-session-123-" in result.grant_path.name
        grant_data = json.loads(result.grant_path.read_text())
        assert grant_data["session_id"] == "test-session-123"


class TestCrossSessionNonceTargeted:
    """Nonce-targeted cross-session activation: the orchestrator extracts a nonce
    from the AskUserQuestion option label and activates that specific pending
    approval under the CURRENT session, regardless of which session created it.
    """

    # ------------------------------------------------------------------ #
    # 1. extract_nonce_from_label
    # ------------------------------------------------------------------ #

    def test_extract_nonce_from_approve_label(self):
        """Nonce is extracted from the [P-xxxxxxxx] tag in the approve label."""
        # Standard approve label with 8-char hex nonce
        label = "Approve -- git push origin main [P-e68be5b8]"
        assert extract_nonce_from_label(label) == "e68be5b8"

    def test_extract_nonce_from_label_without_nonce_returns_none(self):
        """Labels without a [P-...] tag return None."""
        assert extract_nonce_from_label("Approve -- git push origin main") is None

    def test_extract_nonce_from_reject_label_returns_none(self):
        """Reject labels never contain a nonce."""
        assert extract_nonce_from_label("Reject") is None
        assert extract_nonce_from_label("Reject [P-e68be5b8]") is None

    # ------------------------------------------------------------------ #
    # 2. Targeted activation creates grant under current session
    # ------------------------------------------------------------------ #

    def test_cross_session_targeted_activation_creates_grant_under_current_session(
        self, clean_grants_dir, monkeypatch,
    ):
        """Pending created under session_A, activated with explicit session_B:
        the grant must live under session_B, not session_A or 'default'."""
        session_a = "session-A-originator"
        session_b = "session-B-current"

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_a,
        )

        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        pending_data = json.loads(pending_path.read_text())
        assert pending_data["session_id"] == session_a

        result = activate_cross_session_pending(
            pending_data,
            session_id=session_b,
        )
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED
        assert result.grant_path is not None

        # Grant file is named with session_B
        assert f"grant-{session_b}-" in result.grant_path.name

        # Grant data records session_B
        grant_data = json.loads(result.grant_path.read_text())
        assert grant_data["session_id"] == session_b

        # Grant is NOT findable under session_A
        assert check_approval_grant("git push origin main", session_id=session_a) is None

        # Grant IS findable under session_B
        grant = check_approval_grant("git push origin main", session_id=session_b)
        assert grant is not None
        assert "push" in grant.approved_verbs

    # ------------------------------------------------------------------ #
    # 3. Targeted activation cleans pending file
    # ------------------------------------------------------------------ #

    def test_cross_session_targeted_activation_cleans_pending_file(
        self, clean_grants_dir,
    ):
        """After cross-session activation the pending file and its session_A
        index must be cleaned up."""
        session_a = "session-A-cleanup"
        session_b = "session-B-cleanup"

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_a,
        )

        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        index_path = clean_grants_dir / f"pending-index-{session_a}.json"
        assert pending_path.exists()
        assert index_path.exists()

        pending_data = json.loads(pending_path.read_text())
        result = activate_cross_session_pending(pending_data, session_id=session_b)
        assert result.success is True

        # Pending file removed
        assert not pending_path.exists()

        # Index for session_A rebuilt (should be gone since that was the only pending)
        assert not index_path.exists()

    # ------------------------------------------------------------------ #
    # 4. Cross-session grant matches exact command
    # ------------------------------------------------------------------ #

    def test_cross_session_grant_matches_exact_command(self, clean_grants_dir):
        """A cross-session grant for 'git push origin main' must NOT match
        'git push origin develop'."""
        session_a = "session-A-exact"
        session_b = "session-B-exact"

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_a,
        )
        pending_data = json.loads(
            (clean_grants_dir / f"pending-{nonce}.json").read_text()
        )
        result = activate_cross_session_pending(pending_data, session_id=session_b)
        assert result.success is True

        # Exact command matches
        assert check_approval_grant("git push origin main", session_id=session_b) is not None

        # Different target branch does NOT match
        assert check_approval_grant("git push origin develop", session_id=session_b) is None

    # ------------------------------------------------------------------ #
    # 5. Cross-session activation preserves scope signature
    # ------------------------------------------------------------------ #

    def test_cross_session_activation_preserves_scope_signature(self, clean_grants_dir):
        """The scope_signature written by write_pending_approval must survive
        intact through cross-session activation into the grant file."""
        session_a = "session-A-sig"
        session_b = "session-B-sig"

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_a,
        )
        pending_data = json.loads(
            (clean_grants_dir / f"pending-{nonce}.json").read_text()
        )
        original_signature = pending_data["scope_signature"]

        result = activate_cross_session_pending(pending_data, session_id=session_b)
        assert result.success is True

        grant_data = json.loads(result.grant_path.read_text())
        grant_signature = grant_data["scope_signature"]

        # Key fields must match exactly
        assert grant_signature["scope_type"] == original_signature["scope_type"]
        assert grant_signature["base_cmd"] == original_signature["base_cmd"]
        assert grant_signature["verb"] == original_signature["verb"]
        assert grant_signature["cli_family"] == original_signature["cli_family"]
        assert grant_signature["semantic_tokens"] == original_signature["semantic_tokens"]
        assert grant_signature["danger_category"] == original_signature["danger_category"]

    # ------------------------------------------------------------------ #
    # 6. Pending file stores cwd
    # ------------------------------------------------------------------ #

    def test_pending_file_stores_cwd(self, clean_grants_dir):
        """write_pending_approval() persists the cwd parameter in the pending JSON file."""
        nonce = generate_nonce()
        target_cwd = "/home/jorge/ws/me/gaia-ops-dev"

        # NEW API — cwd parameter does not exist yet
        path = write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            cwd=target_cwd,
        )
        assert path is not None
        assert path.exists()

        data = json.loads(path.read_text())
        assert data["cwd"] == target_cwd

    # ------------------------------------------------------------------ #
    # 7. Session-wide activation does NOT activate cross-session pendings
    # ------------------------------------------------------------------ #

    def test_session_wide_activation_does_not_activate_cross_session_pendings(
        self, clean_grants_dir, monkeypatch,
    ):
        """activate_grants_for_session('session_B') must NOT activate pending
        approvals that belong to session_A. This documents that the existing
        session-wide flow stays session-scoped — it does not reach across
        sessions."""
        session_a = "session-A-scoped"
        session_b = "session-B-scoped"

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_a,
        )

        # Attempt session-wide activation under session_B
        monkeypatch.setenv("CLAUDE_SESSION_ID", session_b)
        results = activate_grants_for_session(session_b)

        # No grants should have been activated (pending belongs to session_A)
        assert len(results) == 0

        # Pending file is still there (untouched)
        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        assert pending_path.exists()

        # No grant exists under session_B
        assert check_approval_grant("git push origin main", session_id=session_b) is None

    # ------------------------------------------------------------------ #
    # 8. Nonce-targeted activation works regardless of session
    # ------------------------------------------------------------------ #

    def test_nonce_targeted_activation_works_regardless_of_session(
        self, clean_grants_dir,
    ):
        """Unlike session-wide activation, nonce-targeted (cross-session)
        activation does NOT care which session created the pending. It loads
        pending data directly and creates the grant under the specified
        current session."""
        session_a = "session-A-any"
        session_b = "session-B-any"

        nonce = generate_nonce()
        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_a,
        )

        # Load pending data directly (nonce-targeted — no session filtering)
        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        pending_data = json.loads(pending_path.read_text())

        # Activate under session_B even though pending belongs to session_A
        result = activate_cross_session_pending(pending_data, session_id=session_b)
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

        # Grant is findable under session_B
        grant = check_approval_grant("git push origin main", session_id=session_b)
        assert grant is not None
        assert grant.confirmed is True  # cross-session grants are pre-confirmed

        # Grant is NOT findable under session_A
        assert check_approval_grant("git push origin main", session_id=session_a) is None


# ====================================================================== #
# TestNonceTargetedHookActivation -- hook-level nonce-targeted activation
# ====================================================================== #


class TestNonceTargetedHookActivation:
    """Tests for the nonce-targeted activation flow used by
    _handle_ask_user_question_result in the PostToolUse hook.

    This class tests the building blocks: load_pending_by_nonce_prefix,
    same-session activation via prefix, cross-session activation via
    prefix, and the session-wide fallback path.
    """

    # ------------------------------------------------------------------ #
    # 1. load_pending_by_nonce_prefix
    # ------------------------------------------------------------------ #

    def test_load_pending_by_nonce_prefix_finds_matching_file(
        self, clean_grants_dir,
    ):
        """A pending file can be found by the first 8 chars of its nonce."""
        nonce = generate_nonce()
        prefix = nonce[:8]

        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id="session-prefix-test",
        )

        result = load_pending_by_nonce_prefix(prefix)
        assert result is not None
        assert result["nonce"] == nonce
        assert result["command"] == "git push origin main"
        assert result["session_id"] == "session-prefix-test"

    def test_load_pending_by_nonce_prefix_returns_none_for_no_match(
        self, clean_grants_dir,
    ):
        """When no pending file matches the prefix, None is returned."""
        result = load_pending_by_nonce_prefix("deadbeef")
        assert result is None

    # ------------------------------------------------------------------ #
    # 2. Same-session nonce-targeted activation via prefix
    # ------------------------------------------------------------------ #

    def test_nonce_targeted_same_session_activation_via_prefix(
        self, clean_grants_dir,
    ):
        """Simulates the hook flow for same-session approval:
        extract prefix -> load pending -> activate_pending_approval."""
        session_id = "session-same-nonce"
        nonce = generate_nonce()
        prefix = nonce[:8]

        write_pending_approval(
            nonce=nonce,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        # Step 1: load by prefix (simulates what hook does after extract_nonce_from_label)
        pending_data = load_pending_by_nonce_prefix(prefix)
        assert pending_data is not None
        assert pending_data["session_id"] == session_id  # same session

        # Step 2: same session -> activate_pending_approval
        result = activate_pending_approval(
            nonce=pending_data["nonce"],
            session_id=session_id,
        )
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

        # Step 3: grant is findable under this session
        grant = check_approval_grant("git push origin main", session_id=session_id)
        assert grant is not None
        assert "push" in grant.approved_verbs

        # Pending file is cleaned up
        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        assert not pending_path.exists()

    # ------------------------------------------------------------------ #
    # 3. Cross-session nonce-targeted activation via prefix
    # ------------------------------------------------------------------ #

    def test_nonce_targeted_cross_session_activation_via_prefix(
        self, clean_grants_dir,
    ):
        """Simulates the hook flow for cross-session approval:
        extract prefix -> load pending -> detect different session ->
        activate_cross_session_pending under current session."""
        session_a = "session-A-originator"
        session_b = "session-B-current"

        nonce = generate_nonce()
        prefix = nonce[:8]

        write_pending_approval(
            nonce=nonce,
            command="terraform apply",
            danger_verb="apply",
            danger_category="MUTATIVE",
            session_id=session_a,
        )

        # Step 1: load by prefix
        pending_data = load_pending_by_nonce_prefix(prefix)
        assert pending_data is not None
        assert pending_data["session_id"] == session_a  # different from current

        # Step 2: cross session -> activate_cross_session_pending
        result = activate_cross_session_pending(
            pending_data,
            session_id=session_b,
        )
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

        # Step 3: grant is under session_B (the current session)
        grant = check_approval_grant("terraform apply", session_id=session_b)
        assert grant is not None
        assert grant.confirmed is True  # cross-session grants are pre-confirmed
        assert "apply" in grant.approved_verbs

        # Grant is NOT under session_A
        assert check_approval_grant("terraform apply", session_id=session_a) is None

        # Pending file is cleaned up
        pending_path = clean_grants_dir / f"pending-{nonce}.json"
        assert not pending_path.exists()

    # ------------------------------------------------------------------ #
    # 4. Session-wide fallback (no nonce in label)
    # ------------------------------------------------------------------ #

    def test_session_wide_fallback_when_no_nonce(
        self, clean_grants_dir,
    ):
        """When no nonce prefix is available, activate_grants_for_session
        activates ALL pending approvals for the session (backward compat)."""
        session_id = "session-fallback"

        # Create two pending approvals for the same session
        nonce1 = generate_nonce()
        nonce2 = generate_nonce()

        write_pending_approval(
            nonce=nonce1,
            command="git push origin main",
            danger_verb="push",
            danger_category="MUTATIVE",
            session_id=session_id,
        )
        write_pending_approval(
            nonce=nonce2,
            command="git tag v1.0",
            danger_verb="tag",
            danger_category="MUTATIVE",
            session_id=session_id,
        )

        # Session-wide activation (the fallback path)
        results = activate_grants_for_session(session_id)
        activated = sum(1 for r in results if r.success)
        assert activated == 2

        # Both grants are findable
        assert check_approval_grant("git push origin main", session_id=session_id) is not None
        assert check_approval_grant("git tag v1.0", session_id=session_id) is not None

        # Both pending files are cleaned up
        assert not (clean_grants_dir / f"pending-{nonce1}.json").exists()
        assert not (clean_grants_dir / f"pending-{nonce2}.json").exists()
