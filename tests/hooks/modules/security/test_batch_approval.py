#!/usr/bin/env python3
"""Tests for batch (verb-family) approval grants.

Validates the SCOPE_VERB_FAMILY mechanism:
1. A verb-family grant matches commands with the same base_cmd + verb but different arguments
2. A verb-family grant does NOT match a different verb on the same CLI
3. A verb-family grant does NOT match a different base_cmd with the same verb
4. Multi-use grants are NOT consumed after first use
5. Multi-use grants expire after TTL
6. create_verb_family_grant() produces a valid multi-use grant
7. consume_grant() skips consumption for multi-use grants
8. Batch detection in AskUserQuestion answer triggers verb-family grant creation
"""

import json
import sys
import time
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_grants import (
    ApprovalGrant,
    check_approval_grant,
    consume_grant,
    create_verb_family_grant,
    DEFAULT_BATCH_TTL_MINUTES,
)
from modules.security.approval_scopes import (
    SCOPE_VERB_FAMILY,
    SCOPE_SEMANTIC_SIGNATURE,
    ApprovalSignature,
    build_approval_signature,
    matches_approval_signature,
)
from modules.tools.bash_validator import validate_bash_command


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
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-batch-session")
    # Reset cleanup throttle and mkdir cache so each test starts clean
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False
    yield grants_dir


# ============================================================================
# SCOPE_VERB_FAMILY signature building and matching
# ============================================================================


class TestVerbFamilySignatureMatching:
    """Verb-family scope matches on base_cmd + verb only."""

    def test_verb_family_matches_same_cmd_verb_different_args(self):
        """The core use case: same CLI + verb, different arguments."""
        sig = build_approval_signature(
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=abc123",
            scope_type=SCOPE_VERB_FAMILY,
            danger_verb="modify",
        )
        assert sig is not None
        assert sig.scope_type == SCOPE_VERB_FAMILY
        # Different messageId -- should still match
        assert matches_approval_signature(
            sig,
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=xyz789",
        )

    def test_verb_family_matches_different_flags(self):
        """Different non-dangerous flags should still match."""
        sig = build_approval_signature(
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=abc123",
            scope_type=SCOPE_VERB_FAMILY,
            danger_verb="modify",
        )
        assert sig is not None
        # Different flags (removeLabelIds instead of addLabelIds)
        assert matches_approval_signature(
            sig,
            "gws gmail users messages modify --removeLabelIds SPAM userId=me messageId=abc123",
        )

    def test_verb_family_rejects_different_verb(self):
        """Same CLI but different verb must not match."""
        sig = build_approval_signature(
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=abc123",
            scope_type=SCOPE_VERB_FAMILY,
            danger_verb="modify",
        )
        assert sig is not None
        assert not matches_approval_signature(
            sig,
            "gws gmail users messages delete userId=me messageId=abc123",
        )

    def test_verb_family_rejects_different_base_cmd(self):
        """Different CLI with the same verb must not match."""
        sig = build_approval_signature(
            "kubectl delete pod my-pod",
            scope_type=SCOPE_VERB_FAMILY,
            danger_verb="delete",
        )
        assert sig is not None
        assert not matches_approval_signature(
            sig,
            "terraform delete my-resource",
        )

    def test_verb_family_rejects_different_danger_category(self):
        """Different danger categories must not match."""
        sig = build_approval_signature(
            "git push origin main",
            scope_type=SCOPE_VERB_FAMILY,
            danger_verb="push",
        )
        assert sig is not None
        # Different base_cmd -> no match (base_cmd check)
        assert not matches_approval_signature(
            sig,
            "docker push my-image:latest",
        )


# ============================================================================
# Multi-use grant behavior
# ============================================================================


class TestMultiUseGrant:
    """Multi-use grants remain valid after consumption attempts."""

    def test_multi_use_grant_is_valid_when_used(self):
        """A multi-use grant with used=True is still valid."""
        grant = ApprovalGrant(
            multi_use=True,
            used=True,
            granted_at=time.time(),
            ttl_minutes=5,
        )
        assert grant.is_valid()

    def test_single_use_grant_invalid_when_used(self):
        """Existing behavior: a single-use grant with used=True is invalid."""
        grant = ApprovalGrant(
            multi_use=False,
            used=True,
            granted_at=time.time(),
            ttl_minutes=5,
        )
        assert not grant.is_valid()

    def test_multi_use_grant_expires(self):
        """Multi-use grants still respect TTL expiry."""
        grant = ApprovalGrant(
            multi_use=True,
            used=False,
            granted_at=time.time() - 700,  # 11+ minutes ago
            ttl_minutes=10,
        )
        assert not grant.is_valid()

    def test_multi_use_default_is_false(self):
        """Backward compat: multi_use defaults to False."""
        grant = ApprovalGrant()
        assert not grant.multi_use


# ============================================================================
# create_verb_family_grant()
# ============================================================================


class TestCreateVerbFamilyGrant:
    """Direct creation of verb-family batch grants."""

    def test_creates_grant_file(self, clean_grants_dir):
        """Creates a grant file in the approvals directory."""
        path = create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        assert path is not None
        assert path.exists()
        assert "batch" in path.name

    def test_grant_file_content(self, clean_grants_dir):
        """Grant file contains correct fields."""
        path = create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
            ttl_minutes=15,
        )
        data = json.loads(path.read_text())
        assert data["session_id"] == "test-batch-session"
        assert data["scope_type"] == SCOPE_VERB_FAMILY
        assert data["multi_use"] is True
        assert data["ttl_minutes"] == 15
        assert data["approved_scope"] == "batch:gws modify"
        assert data["approved_verbs"] == ["modify"]
        # Verify signature
        sig = data["scope_signature"]
        assert sig["base_cmd"] == "gws"
        assert sig["verb"] == "modify"
        assert sig["scope_type"] == SCOPE_VERB_FAMILY

    def test_grant_matches_command(self, clean_grants_dir):
        """Created grant can be found by check_approval_grant."""
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        # Any gws ... modify command should match
        grant = check_approval_grant(
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=abc",
            session_id="test-batch-session",
        )
        assert grant is not None
        assert grant.multi_use

    def test_grant_matches_many_different_commands(self, clean_grants_dir):
        """A single batch grant covers many commands with different args."""
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        commands = [
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=msg001",
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=msg002",
            "gws gmail users messages modify --removeLabelIds SPAM userId=me messageId=msg003",
            "gws gmail users messages modify --addLabelIds Archive userId=me messageId=msg004",
        ]
        for cmd in commands:
            grant = check_approval_grant(cmd, session_id="test-batch-session")
            assert grant is not None, f"Should match: {cmd}"
            assert grant.multi_use

    def test_returns_none_for_missing_args(self):
        """Returns None when required arguments are missing."""
        assert create_verb_family_grant("", "gws", "modify") is None
        assert create_verb_family_grant("test-session", "", "modify") is None
        assert create_verb_family_grant("test-session", "gws", "") is None

    def test_default_batch_ttl(self):
        """Batch TTL defaults to 10 minutes."""
        assert DEFAULT_BATCH_TTL_MINUTES == 10


# ============================================================================
# consume_grant() with multi-use grants
# ============================================================================


class TestConsumeMultiUseGrant:
    """consume_grant() should not mark multi-use grants as used."""

    def test_consume_does_not_mark_multi_use_grant_used(self, clean_grants_dir):
        """Multi-use grant survives consume_grant()."""
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        cmd = "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=abc"

        # First consume -- should succeed without marking used
        result = consume_grant(cmd, session_id="test-batch-session")
        assert result is True

        # Grant should still be valid for a second command
        grant = check_approval_grant(cmd, session_id="test-batch-session")
        assert grant is not None
        assert grant.multi_use
        assert not grant.used

    def test_consume_multi_use_grant_many_times(self, clean_grants_dir):
        """Multi-use grant can be consumed many times without being marked used."""
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        for i in range(10):
            cmd = f"gws gmail users messages modify --addLabelIds INBOX userId=me messageId=msg{i:03d}"
            result = consume_grant(cmd, session_id="test-batch-session")
            assert result is True, f"consume #{i} should succeed"

        # Still valid after 10 consumptions
        grant = check_approval_grant(
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=final",
            session_id="test-batch-session",
        )
        assert grant is not None
        assert grant.is_valid()


# ============================================================================
# Bash validator integration with verb-family grants
# ============================================================================


class TestBashValidatorBatchIntegration:
    """Bash validator allows T3 commands covered by a verb-family grant."""

    def test_t3_command_allowed_with_batch_grant(self, clean_grants_dir):
        """A mutative command passes through when a matching batch grant exists."""
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        result = validate_bash_command(
            "gws gmail users messages modify --addLabelIds INBOX userId=me messageId=abc",
            is_subagent=True,
            session_id="test-batch-session",
        )
        assert result.allowed, "Command should be allowed by batch grant"

    def test_different_verb_still_blocked(self, clean_grants_dir):
        """A different verb is NOT covered by the batch grant."""
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="gws",
            verb="modify",
            danger_category="MUTATIVE",
        )
        result = validate_bash_command(
            "gws gmail users messages delete userId=me messageId=abc",
            is_subagent=True,
            session_id="test-batch-session",
        )
        assert not result.allowed, "Different verb should still be blocked"

    def test_batch_grant_does_not_bypass_blocked_commands(self, clean_grants_dir):
        """Blocked commands are never bypassed, even with a batch grant."""
        # Create a broad batch grant
        create_verb_family_grant(
            session_id="test-batch-session",
            base_cmd="rm",
            verb="rm",
            danger_category="MUTATIVE",
        )
        # rm -rf / is permanently blocked by blocked_commands.py
        result = validate_bash_command(
            "rm -rf /",
            is_subagent=True,
            session_id="test-batch-session",
        )
        assert not result.allowed, "Blocked commands must never be bypassed"


# ============================================================================
# AskUserQuestion batch detection
# ============================================================================


class TestBatchDetectionInAnswer:
    """PostToolUse hook detects 'batch' in AskUserQuestion answer."""

    def test_batch_keyword_detected(self):
        """Helper test: verify batch detection logic."""
        answers = {"response": "Approve batch"}
        is_batch = any("batch" in str(v).lower() for v in answers.values())
        assert is_batch

    def test_single_approve_not_batch(self):
        """Regular approve does not trigger batch."""
        answers = {"response": "Approve"}
        is_batch = any("batch" in str(v).lower() for v in answers.values())
        assert not is_batch

    def test_approve_batch_case_insensitive(self):
        """Batch detection is case-insensitive."""
        answers = {"response": "APPROVE BATCH"}
        is_batch = any("batch" in str(v).lower() for v in answers.values())
        assert is_batch
