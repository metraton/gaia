#!/usr/bin/env python3
"""Tests for nonce-only approval grants."""

import json
import re
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_grants import (
    ApprovalGrant,
    activate_db_pending_by_prefix,
    capture_environment_snapshot,
    check_approval_grant,
    cleanup_expired_grants,
    confirm_grant,
    generate_nonce,
)

from modules.security.approval_grants import (
    DEFAULT_PENDING_TTL_MINUTES,
    extract_nonce_from_label,
    get_pending_approvals_for_session,
    load_pending_by_nonce_prefix,
)
from tests.fixtures.db_helpers import seed_db_pending
from modules.security.approval_scopes import (
    SCOPE_EXACT_COMMAND,
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
)


@pytest.fixture(autouse=True)
def clean_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for grants and an isolated writer DB.

    Test isolation note (Brief 71, Change 4): check_approval_grant()'s DB-primary
    path and its filesystem-fallback CONSUMED replay guard both call
    gaia.store.writer._connect(). The replay guard is now session-agnostic, so if
    _connect() reached the real ~/.gaia/gaia.db it could match a CONSUMED grant
    from actual prior usage and spuriously suppress a legitimate test grant. We
    patch _connect() to a per-test SQLite file so the grant DB is empty and
    isolated, exactly as test_activation_db_bridge.py does.
    """
    import sqlite3
    import hashlib

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

    writer_db_path = tmp_path / "writer_isolation.db"

    def _make_writer_db() -> sqlite3.Connection:
        con = sqlite3.connect(str(writer_db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1,
            lambda v: hashlib.sha256((v or "").encode()).hexdigest(),
            deterministic=True,
        )
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_grants (
                approval_id           TEXT PRIMARY KEY,
                agent_id              TEXT,
                session_id            TEXT,
                command_set_json      TEXT NOT NULL,
                scope                 TEXT NOT NULL DEFAULT 'COMMAND_SET',
                created_at            TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                expires_at            TEXT,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                consumed_indexes_json TEXT,
                consumed_at           TEXT,
                revoked_at            TEXT,
                multi_use             INTEGER NOT NULL DEFAULT 0,
                confirmed             INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # The DB-backed pending plane (insert_requested / get_pending) reads the
        # approvals + approval_events tables. gaia.approvals.store._open_db()
        # delegates to gaia.store.writer._connect(), so the same isolation DB
        # must materialize these tables for the migrated pending functions.
        from tests.fixtures.db_helpers import apply_approvals_schema
        apply_approvals_schema(con)
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", lambda db_path_arg=None: _make_writer_db())

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
    """Insert an active semantic grant into the isolated writer DB for grant-matching tests.

    DB-primary (Brief 71 / G2 cutover): check_approval_grant() reads the DB only.
    Returns a sentinel Path whose name encodes the approval_id so callers can
    distinguish success (non-None) from failure (None) and retrieve the id when
    needed.  The returned path is NOT a real file on disk.
    """
    import secrets as _secrets
    from datetime import datetime, timezone, timedelta
    import gaia.store.writer as _sw

    signature = build_approval_signature(command, scope_type=scope_type)
    assert signature is not None

    approval_id = f"P-{_secrets.token_hex(16)}"
    grant_data = {"command": command, "scope_signature": signature.to_dict()}

    grant_at = granted_at if granted_at is not None else time.time()
    expires_at = (
        datetime.fromtimestamp(grant_at, tz=timezone.utc)
        + timedelta(minutes=ttl_minutes)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    con = _sw._connect()
    try:
        con.execute("BEGIN")
        status_val = "CONSUMED" if used else "PENDING"
        con.execute(
            """
            INSERT OR IGNORE INTO approval_grants
                (approval_id, agent_id, session_id, command_set_json,
                 scope, expires_at, status, consumed_indexes_json,
                 multi_use, confirmed)
            VALUES (?, NULL, ?, ?, 'SCOPE_SEMANTIC_SIGNATURE', ?, ?, '[]', 0, ?)
            """,
            (
                approval_id,
                session_id,
                json.dumps(grant_data),
                expires_at,
                status_val,
                1 if confirmed else 0,
            ),
        )
        con.commit()
    finally:
        con.close()

    # Return a sentinel path whose name encodes the approval_id so callers
    # that need to identify the grant can extract it.
    sentinel = grants_dir / f"grant-sentinel-{approval_id}.ref"
    return sentinel


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
        import gaia.store.writer as _sw
        path = _write_active_grant(clean_grants_dir, "git commit")
        # Extract the approval_id from the sentinel filename (grant-sentinel-P-<id>.ref)
        approval_id = path.stem.replace("grant-sentinel-", "")
        cleaned = cleanup_expired_grants()
        assert cleaned == 0
        # Verify the DB row was NOT expired (still PENDING)
        con = _sw._connect()
        try:
            row = con.execute(
                "SELECT status FROM approval_grants WHERE approval_id = ?",
                (approval_id,),
            ).fetchone()
        finally:
            con.close()
        assert row is not None, "Grant row must still exist in DB"
        assert row[0] == "PENDING", f"Expected PENDING, got {row[0]}"

    def test_cleanup_removes_unsupported_grants(self, clean_grants_dir):
        """DB grants with unsupported scope types are not matched by
        check_db_semantic_grant (which filters on scope='SCOPE_SEMANTIC_SIGNATURE').
        cleanup_expired_grants sweeps DB rows; a resource_family scoped row inserted
        directly is NOT visible to check_approval_grant -- verify it returns None."""
        import secrets as _sec
        import gaia.store.writer as _sw
        from datetime import datetime, timezone, timedelta

        approval_id = f"P-{_sec.token_hex(16)}"
        grant_data = {"command": "terraform apply", "scope_signature": {"scope_type": "resource_family"}}
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")

        con = _sw._connect()
        try:
            con.execute("BEGIN")
            con.execute(
                """INSERT OR IGNORE INTO approval_grants
                   (approval_id, session_id, command_set_json, scope, expires_at, status, consumed_indexes_json)
                   VALUES (?, 'test-session-123', ?, 'resource_family', ?, 'PENDING', '[]')""",
                (approval_id, json.dumps(grant_data), expires_at),
            )
            con.commit()
        finally:
            con.close()

        # DB-primary: check_approval_grant only matches SCOPE_SEMANTIC_SIGNATURE rows
        assert check_approval_grant("terraform apply") is None

    # ------------------------------------------------------------------
    # Phase 2: force=True bypasses the 60s throttle
    # ------------------------------------------------------------------

    def test_throttle_blocks_repeat_cleanup_without_force(self, clean_grants_dir):
        """Within the throttle window, a second non-force call must skip."""
        import modules.security.approval_grants as ag

        # First call primes the throttle and returns whatever it cleans.
        cleanup_expired_grants()
        # Throttle now active; a non-force call within the window is a no-op.
        result = cleanup_expired_grants()
        assert result == 0, (
            "Throttle must short-circuit a repeat call inside the "
            "_CLEANUP_INTERVAL_SECONDS window when force=False."
        )

    def test_force_true_bypasses_throttle(self, clean_grants_dir):
        """force=True must run cleanup even when the throttle is fresh."""
        import modules.security.approval_grants as ag

        # Simulate a recent cleanup so the throttle would otherwise skip.
        ag._last_cleanup_time = time.time()

        # Seed an expired DB grant that cleanup should sweep.
        _write_active_grant(
            clean_grants_dir,
            "git commit",
            granted_at=time.time() - 700,
            ttl_minutes=10,
        )

        # Reset the throttle stamp to something in the past so the forced
        # call sees a non-zero baseline gap (avoids a 0.0 edge case).
        ag._last_cleanup_time = time.time() - 1

        cleaned = cleanup_expired_grants(force=True)
        assert cleaned >= 1, (
            "force=True must bypass the throttle and run the sweep. "
            "Without force, a session start that happens <60s after the "
            "last cleanup would silently skip and leave stale artefacts."
        )


class TestNonceEndToEnd:
    """The full nonce flow should still work end-to-end.

    The bash_validator now returns 'ask' (native dialog) for T3 commands
    without generating nonces. The nonce flow is driven by pre_tool_use.py.
    These tests exercise the approval_grants module directly.
    """

    def test_full_flow_block_activate_passthrough(self, clean_grants_dir):
        """Block a T3 command, seed the post-approval DB grant, verify passthrough.

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

        # Seed the DB grant the activation path would create after user approval.
        _write_active_grant(clean_grants_dir, command, session_id=session_id)

        # After activation, grant passthrough: GAIA approved, no second dialog
        result2 = validator.validate(command, session_id=session_id)
        assert result2.allowed is True
        assert "grant" in result2.reason.lower()

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
        assert get_pending_approvals_for_session("test-session-123") == []


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
        _write_active_grant(clean_grants_dir, "terraform apply prod/vpc")
        assert check_approval_grant("kubectl apply -f prod.yaml") is None

    def test_nonce_grant_does_not_escalate_to_more_dangerous_variant(self, clean_grants_dir):
        _write_active_grant(clean_grants_dir, "git push origin main")
        assert check_approval_grant("git push origin main --force") is None

    def test_nonce_grant_does_not_jump_resource_kind(self, clean_grants_dir):
        _write_active_grant(clean_grants_dir, "kubectl delete pod pod-1")
        assert check_approval_grant("kubectl delete namespace prod") is None

    def test_unsupported_grant_file_does_not_match(self, clean_grants_dir):
        """DB-primary: check_approval_grant only matches SCOPE_SEMANTIC_SIGNATURE rows.
        A DB row with an unsupported scope is invisible to the check path."""
        import secrets as _sec
        import gaia.store.writer as _sw
        from datetime import datetime, timezone, timedelta

        approval_id = f"P-{_sec.token_hex(16)}"
        grant_data = {
            "command": "terraform apply prod/vpc",
            "scope_signature": {"version": 2, "scope_type": "resource_family"},
        }
        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        con = _sw._connect()
        try:
            con.execute("BEGIN")
            con.execute(
                """INSERT OR IGNORE INTO approval_grants
                   (approval_id, session_id, command_set_json, scope, expires_at, status, consumed_indexes_json)
                   VALUES (?, 'test-session-123', ?, 'resource_family', ?, 'PENDING', '[]')""",
                (approval_id, json.dumps(grant_data), expires_at),
            )
            con.commit()
        finally:
            con.close()

        assert check_approval_grant("terraform apply prod/vpc") is None

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
        """Grants use TTL-based expiry; check_approval_grant alone does not consume the row."""
        import gaia.store.writer as _sw

        _write_active_grant(clean_grants_dir, "git push origin feature/branch")
        grant = check_approval_grant("git push origin feature/branch")
        assert grant is not None

        # Verify the DB row is still PENDING (not consumed) after a plain check
        con = _sw._connect()
        try:
            rows = con.execute(
                "SELECT status FROM approval_grants WHERE status='PENDING'",
            ).fetchall()
        finally:
            con.close()
        assert len(rows) >= 1, "Grant row must remain PENDING after check_approval_grant"


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
        the DB grant row is recorded with session_id=session_B."""
        import gaia.store.writer as _sw
        from modules.security.approval_grants import ACTIVATION_ACTIVATED

        session_a = "session-A-originator"
        session_b = "session-B-current"

        nonce = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_a,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce,
        )

        # Activate by nonce prefix under session_B (cross-session: the DB
        # lookup is all-sessions, the grant is created under current session).
        result = activate_db_pending_by_prefix(
            nonce[:8], current_session_id=session_b,
        )
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

        # DB row is recorded under session_B
        con = _sw._connect()
        try:
            row = con.execute(
                "SELECT session_id FROM approval_grants WHERE status='PENDING'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row[0] == session_b

        # Grant IS findable via check_approval_grant (DB lookup is session-agnostic)
        grant = check_approval_grant("git push origin main", session_id=session_b)
        assert grant is not None
        assert "push" in grant.approved_verbs

    # ------------------------------------------------------------------ #
    # 3. Cross-session grant matches exact command
    # ------------------------------------------------------------------ #

    def test_cross_session_grant_matches_exact_command(self, clean_grants_dir):
        """A cross-session grant for 'git push origin main' must NOT match
        'git push origin develop'."""
        session_a = "session-A-exact"
        session_b = "session-B-exact"

        nonce = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_a,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce,
        )
        result = activate_db_pending_by_prefix(
            nonce[:8], current_session_id=session_b,
        )
        assert result.success is True

        # Exact command matches
        assert check_approval_grant("git push origin main", session_id=session_b) is not None

        # Different target branch does NOT match
        assert check_approval_grant("git push origin develop", session_id=session_b) is None

    # ------------------------------------------------------------------ #
    # 4. Cross-session activation preserves scope signature
    # ------------------------------------------------------------------ #

    def test_cross_session_activation_preserves_scope_signature(self, clean_grants_dir):
        """The scope_signature sealed into the DB pending must survive intact
        through cross-session activation into the DB grant row."""
        import gaia.store.writer as _sw
        from modules.security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
        )

        session_a = "session-A-sig"
        session_b = "session-B-sig"

        original_signature = build_approval_signature(
            "git push origin main",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push",
            danger_category="MUTATIVE",
        ).to_dict()

        nonce = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_a,
            danger_verb="push",
            danger_category="MUTATIVE",
            scope_signature=original_signature,
            nonce=nonce,
        )

        result = activate_db_pending_by_prefix(
            nonce[:8], current_session_id=session_b,
        )
        assert result.success is True

        # Read back the scope_signature from the DB row's command_set_json
        con = _sw._connect()
        try:
            row = con.execute(
                "SELECT command_set_json FROM approval_grants WHERE status='PENDING'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        grant_data = json.loads(row[0])
        grant_signature = grant_data["scope_signature"]

        # Key fields must match exactly
        assert grant_signature["scope_type"] == original_signature["scope_type"]
        assert grant_signature["base_cmd"] == original_signature["base_cmd"]
        assert grant_signature["verb"] == original_signature["verb"]
        assert grant_signature["cli_family"] == original_signature["cli_family"]
        assert list(grant_signature["semantic_tokens"]) == list(original_signature["semantic_tokens"])
        assert grant_signature["danger_category"] == original_signature["danger_category"]

    # ------------------------------------------------------------------ #
    # 5. Nonce-targeted activation works regardless of session
    # ------------------------------------------------------------------ #

    def test_nonce_targeted_activation_works_regardless_of_session(
        self, clean_grants_dir,
    ):
        """Nonce-targeted (cross-session) activation does NOT care which session
        created the pending. It looks the pending up by nonce prefix across all
        sessions and creates the grant under the specified current session."""
        import gaia.store.writer as _sw
        from modules.security.approval_grants import ACTIVATION_ACTIVATED

        session_a = "session-A-any"
        session_b = "session-B-any"

        nonce = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_a,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce,
        )

        # Activate under session_B even though pending belongs to session_A
        result = activate_db_pending_by_prefix(
            nonce[:8], current_session_id=session_b,
        )
        assert result.success is True
        assert result.status == ACTIVATION_ACTIVATED

        # Grant IS findable (DB lookup is session-agnostic -- any session_id works)
        grant = check_approval_grant("git push origin main", session_id=session_b)
        assert grant is not None
        assert grant.confirmed is True  # cross-session grants are pre-confirmed

        # DB row is stored under session_B
        con = _sw._connect()
        try:
            row = con.execute(
                "SELECT session_id FROM approval_grants WHERE status='PENDING'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row[0] == session_b


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
        """A DB pending can be found by the first 8 chars of its nonce."""
        nonce = generate_nonce()
        prefix = nonce[:8]

        seed_db_pending(
            command="git push origin main",
            session_id="session-prefix-test",
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce,
        )

        result = load_pending_by_nonce_prefix(prefix)
        assert result is not None
        assert result["nonce"] == nonce
        assert result["command"] == "git push origin main"
        assert result["session_id"] == "session-prefix-test"

    def test_load_pending_by_nonce_prefix_returns_none_for_no_match(
        self, clean_grants_dir,
    ):
        """When no pending row matches the prefix, None is returned."""
        result = load_pending_by_nonce_prefix("deadbeef")
        assert result is None

    # NOTE: the nonce-targeted ACTIVATION flow (same-session, cross-session,
    # and session-wide fallback) is now driven entirely by the DB bridge
    # (activate_db_pending_by_prefix) and is covered by
    # test_activation_db_bridge.py. The former filesystem activation helpers
    # (activate_pending_approval / activate_cross_session_pending /
    # activate_grants_for_session) were retired with the FS pending plane.


class TestCaptureEnvironmentSnapshot:
    """Environment snapshot capture for pending approvals."""

    # ------------------------------------------------------------------ #
    # 1. Git command: captures HEAD, branch, remote HEAD
    # ------------------------------------------------------------------ #

    @patch("modules.security.approval_grants.subprocess.run")
    def test_capture_env_snapshot_for_git_command(self, mock_run):
        """For git commands, capture local HEAD, branch, and remote HEAD."""
        def _fake_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            cmd = " ".join(args)
            if "rev-parse HEAD" in cmd and "--abbrev-ref" not in cmd:
                result.stdout = "abc123def456\n"
            elif "--abbrev-ref HEAD" in cmd:
                result.stdout = "main\n"
            elif "origin/main" in cmd:
                result.stdout = "789xyz000111\n"
            else:
                result.returncode = 1
                result.stdout = ""
            return result

        mock_run.side_effect = _fake_run

        snapshot = capture_environment_snapshot("git push origin main")

        assert snapshot["command_class"] == "git"
        assert snapshot["local_head"] == "abc123def456"
        assert snapshot["branch"] == "main"
        assert snapshot["remote_head"] == "789xyz000111"

    # ------------------------------------------------------------------ #
    # 2. Non-git command: returns empty dict
    # ------------------------------------------------------------------ #

    def test_capture_env_snapshot_for_non_git_command(self):
        """Non-git commands return an empty dict (extensible later)."""
        snapshot = capture_environment_snapshot("kubectl apply -f deploy.yaml")
        assert snapshot == {}

        snapshot = capture_environment_snapshot("terraform apply")
        assert snapshot == {}

    # ------------------------------------------------------------------ #
    # 3. Subprocess failure: returns empty dict gracefully
    # ------------------------------------------------------------------ #

    @patch("modules.security.approval_grants.subprocess.run")
    def test_capture_env_snapshot_handles_failure(self, mock_run):
        """When subprocess calls fail, return empty dict without raising."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=2)

        snapshot = capture_environment_snapshot("git push origin main")

        # Should return a dict with command_class but no git details
        # (all individual queries failed gracefully)
        assert isinstance(snapshot, dict)
        assert snapshot.get("command_class") == "git"
        assert "local_head" not in snapshot
        assert "branch" not in snapshot
        assert "remote_head" not in snapshot


# ====================================================================== #
# TestPendingTTLDefault -- pending TTL default constant
# ====================================================================== #


class TestPendingTTLDefault:
    """The default pending TTL constant must remain 24 hours."""

    def test_ttl_default_is_24_hours(self):
        """DEFAULT_PENDING_TTL_MINUTES must be 1440 (24 hours)."""
        assert DEFAULT_PENDING_TTL_MINUTES == 1440
