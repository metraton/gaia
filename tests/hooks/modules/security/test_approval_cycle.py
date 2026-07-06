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

import hashlib
import json
import sqlite3
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
    activate_db_pending_by_prefix,
    check_approval_grant,
    confirm_grant,
    consume_grant,
    generate_nonce,
    get_pending_approvals_for_session,
)
from modules.tools.bash_validator import BashValidator, validate_bash_command
from tests.fixtures.db_helpers import apply_approvals_schema, seed_db_pending


# ---------------------------------------------------------------------------
# Shared helpers -- DB-side approval testing (T2.1 cutover)
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_schema_on(con: sqlite3.Connection) -> None:
    """Apply the v12 approval schema to an existing SQLite connection.

    Mirrors the helper in tests/hooks/test_approval_events.py.
    Kept local here to avoid cross-module import coupling.
    """
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS approvals (
            id           TEXT PRIMARY KEY,
            agent_id     TEXT,
            session_id   TEXT,
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','approved','rejected','revoked','expired')),
            fingerprint  TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            decided_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS approval_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id   TEXT NOT NULL,
            event_type    TEXT NOT NULL CHECK (event_type IN (
                              'REQUESTED','SHOWN','APPROVED','REJECTED',
                              'EXECUTED','FAILED','NOOP','REVOKED','REVERTED'
                          )),
            agent_id      TEXT,
            session_id    TEXT,
            payload_json  TEXT,
            fingerprint   TEXT,
            prev_hash     TEXT,
            this_hash     TEXT,
            metadata_json TEXT,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (approval_id) REFERENCES approvals(id)
        );

        CREATE TRIGGER IF NOT EXISTS ai_approval_events_hash
        AFTER INSERT ON approval_events
        BEGIN
            SELECT 1;
        END;

        CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
        BEFORE UPDATE ON approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END;
    """)


@pytest.fixture()
def approvals_test_db(tmp_path):
    """File-backed SQLite DB with v12 approval schema.

    Returns a (db_path, open_connection) tuple.  The db_path is used by the
    monkeypatched _open_db() factory so that multiple connections to the same
    file share durable state across commit/close cycles.  The open_connection
    is a long-lived handle for direct assertion queries.

    Using a file (not :memory:) is required because store._open_db() is called
    inside insert_requested() with owned=True, meaning it will commit and close
    the connection.  A :memory: DB is destroyed on close; a file survives it.
    """
    db_path = tmp_path / "approvals_v12_test.db"
    con = sqlite3.connect(str(db_path))
    _make_v12_schema_on(con)
    con.commit()
    yield db_path, con
    con.close()


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

    def test_subagent_deny_creates_pending_approval(self, approvals_test_db, monkeypatch):
        """Subagent deny should create a DB pending approval row.

        T2.1 cutover: the filesystem pending write was replaced by
        store.insert_requested (DB).  This test asserts the DB row exists
        instead of the retired filesystem file.
        """
        import gaia.approvals.store as astore

        db_path, assert_con = approvals_test_db

        # Patch store._open_db so insert_requested writes to our temp file DB.
        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )
        # Also patch _find_pending_in_db inside bash_validator so the retry
        # check uses the same temp DB.
        _orig_get_pending = astore.get_pending

        def _patched_get_pending(session_id=None, all_sessions=False, con=None):
            if con is None:
                con = sqlite3.connect(str(db_path))
            return _orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

        monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

        result = validate_bash_command(
            "git push origin main",
            is_subagent=True,
            session_id="test-cycle-session",
        )

        assert not result.allowed

        # Assert via DB: expect a pending row for this session.
        pending = astore.get_pending(session_id="test-cycle-session", con=assert_con)
        assert len(pending) >= 1, "Expected at least one pending approval in DB"

        # The sealed_payload contains exact_content == the command.
        payload = json.loads(pending[0]["payload_json"])
        assert "push" in payload.get("operation", ""), (
            f"Expected 'push' in operation field, got: {payload.get('operation')}"
        )
        assert payload.get("exact_content") == "git push origin main", (
            f"Expected exact_content='git push origin main', got: {payload.get('exact_content')}"
        )


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


def _isolate_writer_db(monkeypatch, tmp_path):
    """Redirect gaia.store.writer._connect to an isolated SQLite file that
    carries both the approvals plane (approvals/approval_events) and the
    grant plane (approval_grants), so DB-backed seeding/activation/grant
    checks never touch ~/.gaia/gaia.db.

    Returns the db path.
    """
    import gaia.store.writer as gwriter

    writer_db_path = tmp_path / "cycle_writer.db"

    def _make_writer_db():
        con = sqlite3.connect(str(writer_db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1, lambda v: _sha256(v), deterministic=True,
        )
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_grants (
                approval_id          TEXT PRIMARY KEY,
                agent_id             TEXT,
                session_id           TEXT,
                command_set_json     TEXT NOT NULL,
                scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
                created_at           TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                expires_at           TEXT,
                status               TEXT NOT NULL DEFAULT 'PENDING',
                consumed_indexes_json TEXT,
                consumed_at          TEXT,
                revoked_at           TEXT,
                multi_use            INTEGER NOT NULL DEFAULT 0,
                confirmed            INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        apply_approvals_schema(con)
        con.commit()
        return con

    monkeypatch.setattr(gwriter, "_connect", lambda db_path=None: _make_writer_db())
    return writer_db_path


class TestElicitationResultActivatesGrant:
    """Test 3: ElicitationResult activates grant for pending approval."""

    def test_activate_db_pending_creates_grant(self, monkeypatch, tmp_path):
        """Seeding a DB pending then activating it by nonce prefix creates a usable grant."""
        _isolate_writer_db(monkeypatch, tmp_path)

        nonce = generate_nonce()
        command = "terraform apply"
        session_id = "test-cycle-session"

        # 1. Seed a DB pending approval (replaces the retired FS write).
        seed_db_pending(
            command=command,
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce,
        )

        # 2. Activate the DB pending by nonce prefix (simulates approval).
        result = activate_db_pending_by_prefix(
            nonce[:8], current_session_id=session_id,
        )
        assert result.success, f"Activation should succeed: {result.reason}"

        # 3. Check that the grant is now active.
        grant = check_approval_grant(command, session_id=session_id)
        assert grant is not None, "Grant should be active after activation"
        assert grant.approved_scope == command


class TestFullApprovalCycle:
    """Test 4: Full cycle -- deny, approve, retry succeeds (passthrough)."""

    def test_deny_activate_retry_succeeds(self, approvals_test_db, monkeypatch, tmp_path):
        """Complete cycle: subagent denied, approval activated, retry passthrough.

        With grant passthrough, once a grant is activated (even unconfirmed),
        the validator returns allowed=True immediately. PostToolUse will
        confirm and consume the grant after execution.

        DB-only since the FS pending plane was retired: the deny writes a DB
        pending (insert_requested), the user-approval step activates that DB
        pending by its nonce prefix (activate_db_pending_by_prefix), and the
        retry passes through on the resulting DB grant.
        """
        import re
        import gaia.approvals.store as astore

        command = "terraform apply"
        session_id = "test-cycle-session"
        db_path, assert_con = approvals_test_db

        # Patch store._open_db so insert_requested writes to our temp file DB.
        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        _orig_get_pending = astore.get_pending

        def _patched_get_pending(session_id=None, all_sessions=False, con=None):
            if con is None:
                con = sqlite3.connect(str(db_path))
            return _orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

        monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

        # Isolate the grant plane (gaia.store.writer) so activation + grant
        # checks land in a test-local DB.
        _isolate_writer_db(monkeypatch, tmp_path)

        # Step 1: Subagent command is denied with approval_id (written to DB).
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed
        hook_output = result1.block_response["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "deny"

        # Extract approval_id from the deny reason.
        reason = hook_output["permissionDecisionReason"]
        match = re.search(r"approval_id:\s*(P-[\w-]+)", reason)
        assert match, f"Could not extract approval_id from: {reason}"
        approval_id = match.group(1)

        # DB-side assertion: confirm the pending row exists in the DB.
        pending_rows = astore.get_pending(session_id=session_id, con=assert_con)
        assert len(pending_rows) >= 1, "DB pending row must exist after deny"
        assert pending_rows[0]["id"] == approval_id, (
            f"DB approval_id mismatch: stored={pending_rows[0]['id']}, deny={approval_id}"
        )

        # Step 2: User approves -> activate the DB pending by its nonce prefix.
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]
        activation = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert activation.success, f"Activation failed: {activation.reason}"

        # Step 3: Retry the same command -- passthrough (DB grant exists).
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

    def test_negative_response_leaves_pending_intact(self, monkeypatch, tmp_path):
        """A negative response should not activate pending approvals."""
        from elicitation_result import _is_approval

        _isolate_writer_db(monkeypatch, tmp_path)

        nonce = generate_nonce()
        seed_db_pending(
            command="terraform apply",
            session_id="test-cycle-session",
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce,
        )

        # Simulate a negative response -- should NOT activate
        assert not _is_approval("Reject")

        # Pending should still be there
        pending = get_pending_approvals_for_session("test-cycle-session")
        assert len(pending) >= 1, "Pending should still exist after negative response"

        # Grant should NOT exist
        grant = check_approval_grant("terraform apply")
        assert grant is None, "No grant should exist after negative response"


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
        assert "APPROVAL_REQUEST" in reason, (
            f"T3_BLOCKED message should mention APPROVAL_REQUEST status, got: {reason}"
        )


class TestConsumeGrant:
    """Test 9: consume_grant() marks grant as used (single-use)."""

    def test_consume_grant_marks_used(self, monkeypatch, tmp_path):
        """consume_grant() marks the grant consumed so it no longer matches."""
        _isolate_writer_db(monkeypatch, tmp_path)

        nonce = generate_nonce()
        command = "terraform apply"
        session_id = "test-cycle-session"

        # Seed a DB pending approval and activate it by nonce prefix.
        seed_db_pending(
            command=command,
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce,
        )
        result = activate_db_pending_by_prefix(nonce[:8], current_session_id=session_id)
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

    def test_consume_grant_second_call_returns_false(self, monkeypatch, tmp_path):
        """Second call to consume_grant() returns False (already consumed)."""
        _isolate_writer_db(monkeypatch, tmp_path)

        nonce = generate_nonce()
        command = "git push origin main"
        session_id = "test-cycle-session"

        seed_db_pending(
            command=command,
            session_id=session_id,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce,
        )
        activate_db_pending_by_prefix(nonce[:8], current_session_id=session_id)

        # First consume succeeds
        assert consume_grant(command, session_id=session_id) is True

        # Second consume fails (grant already used)
        assert consume_grant(command, session_id=session_id) is False

    def test_consume_nonexistent_grant_returns_false(self, monkeypatch, tmp_path):
        """consume_grant() returns False when no matching grant exists."""
        _isolate_writer_db(monkeypatch, tmp_path)
        consumed = consume_grant("terraform destroy", session_id="test-cycle-session")
        assert consumed is False


class TestDefaultTTL:
    """Test 10: DEFAULT_GRANT_TTL_MINUTES is 5 (approvals redesign, M1).

    The grant is consumed at the match, so the active-grant retry window only
    needs to cover the block -> approve -> retry round trip.
    """

    def test_default_ttl_is_five_minutes(self):
        """DEFAULT_GRANT_TTL_MINUTES should be 5."""
        assert DEFAULT_GRANT_TTL_MINUTES == 5, (
            f"Expected grant TTL=5 (M1), got {DEFAULT_GRANT_TTL_MINUTES}"
        )


class TestConditionalActivation:
    """Test 11: Conditional activation based on answers in AskUserQuestion.

    DB-only since the grant-lifecycle FS retirement.  Activation is
    nonce-targeted: the orchestrator's Approve label carries a
    ``[P-<nonce8>]`` tag (mandated by orchestrator-present-approval:
    "Without the suffix no grant is created"), the PostToolUse handler
    extracts it and activates the specific DB pending via
    ``activate_db_pending_by_prefix``.

    The legacy "no-nonce session-wide activation" path (an unlabeled
    "Approve" activating ALL of a session's pendings) was dropped during
    the FS retirement: it has no production caller (every real Approve
    label is nonce-suffixed) and it violated informed consent by
    activating grants the user never specifically saw.  These tests now
    assert the nonce-targeted DB behavior; an approve answer WITHOUT a
    nonce activates nothing.
    """

    @pytest.fixture(autouse=True)
    def setup_adapter(self):
        """Import the adapter for testing."""
        ADAPTERS_DIR = HOOKS_DIR / "adapters"
        sys.path.insert(0, str(ADAPTERS_DIR))
        from adapters.claude_code import ClaudeCodeAdapter
        self.adapter = ClaudeCodeAdapter()

    @pytest.fixture(autouse=True)
    def isolate_db(self, approvals_test_db, monkeypatch, tmp_path):
        """Redirect every approval/grant DB access to isolated test DBs.

        Mirrors TestConsumeGrantAtSubagentStop: the approvals chain
        (insert_requested / get_pending) reads the v12 approvals DB, and
        the grant chain (check_db_semantic_grant / consume_db_semantic_grant
        / insert_semantic_grant) reads an isolated writer DB.  Neither
        touches ~/.gaia/gaia.db.
        """
        import gaia.approvals.store as astore
        import gaia.store.writer as gwriter

        db_path, assert_con = approvals_test_db
        self.assert_con = assert_con

        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        _orig_get_pending = astore.get_pending

        def _patched_get_pending(session_id=None, all_sessions=False, con=None):
            if con is None:
                con = sqlite3.connect(str(db_path))
            return _orig_get_pending(
                session_id=session_id, all_sessions=all_sessions, con=con,
            )

        monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

        writer_db_path = tmp_path / "writer_grants.db"

        def _make_writer_db():
            con = sqlite3.connect(str(writer_db_path))
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.create_function(
                "gaia_sha256", 1, lambda v: _sha256(v), deterministic=True,
            )
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_grants (
                    approval_id          TEXT PRIMARY KEY,
                    agent_id             TEXT,
                    session_id           TEXT,
                    command_set_json     TEXT NOT NULL,
                    scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
                    created_at           TEXT NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    expires_at           TEXT,
                    status               TEXT NOT NULL DEFAULT 'PENDING',
                    consumed_indexes_json TEXT,
                    consumed_at          TEXT,
                    revoked_at           TEXT,
                    multi_use            INTEGER NOT NULL DEFAULT 0,
                    confirmed            INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            con.commit()
            return con

        monkeypatch.setattr(
            gwriter, "_connect", lambda db_path=None: _make_writer_db(),
        )

    def _deny_creates_db_pending(self, command, session_id="test-cycle-session"):
        """Deny a subagent command -> DB pending row; return its approval_id."""
        import re

        result = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result.allowed, f"{command} should be blocked"
        reason = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        m = re.search(r"approval_id:\s*(P-[\w-]+)", reason)
        assert m, f"no approval_id in deny reason: {reason}"
        return m.group(1)

    def _make_hook_data(self, answers=None, session_id="test-cycle-session",
                        in_tool_input=False):
        """Build a minimal AskUserQuestion PostToolUse hook_data."""
        data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {},
            "tool_response": {},
        }
        if answers is not None:
            key = "tool_input" if in_tool_input else "tool_response"
            data[key] = {"answers": answers}
        return data

    def _grant_active(self, command, session_id="test-cycle-session"):
        """True iff a live DB semantic grant matches the command."""
        from gaia.store.writer import check_db_semantic_grant
        return check_db_semantic_grant(command, session_id=session_id) is not None

    def test_approve_answer_activates_grants(self):
        """A nonce-labeled Approve answer activates the targeted DB grant."""
        session_id = "test-cycle-session"
        approval_id = self._deny_creates_db_pending("terraform apply", session_id)
        nonce8 = approval_id[len("P-"):len("P-") + 8]

        hook_data = self._make_hook_data(
            answers={"Proceed with terraform apply?":
                     f"Approve -- terraform apply [P-{nonce8}]"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        assert self._grant_active("terraform apply", session_id), (
            "Grant should be active after nonce-labeled approval"
        )

    def test_reject_answer_does_not_activate_grants(self):
        """Answers containing 'Reject' should NOT activate pending grants."""
        session_id = "test-cycle-session"
        self._deny_creates_db_pending("terraform apply", session_id)

        hook_data = self._make_hook_data(
            answers={"Proceed with terraform apply?": "Reject"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        assert not self._grant_active("terraform apply", session_id), (
            "Grant should NOT be active after user rejected"
        )

    def test_modify_answer_does_not_activate_grants(self):
        """Answers containing 'Modify' should NOT activate pending grants."""
        session_id = "test-cycle-session"
        self._deny_creates_db_pending("git push origin main", session_id)

        hook_data = self._make_hook_data(
            answers={"Allow git push?": "Modify"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        assert not self._grant_active("git push origin main", session_id), (
            "Grant should NOT be active after user chose Modify"
        )

    def test_no_answers_does_not_activate_grants(self):
        """Missing answers field should NOT activate pending grants."""
        session_id = "test-cycle-session"
        self._deny_creates_db_pending("terraform apply", session_id)

        hook_data = self._make_hook_data(answers=None, session_id=session_id)
        self.adapter._handle_ask_user_question_result(hook_data)

        assert not self._grant_active("terraform apply", session_id), (
            "Grant should NOT be active when no answers present"
        )

    def test_no_nonce_approve_activates_nothing(self):
        """An Approve answer WITHOUT a nonce tag activates nothing.

        Documents the dropped legacy behavior: a bare 'Approve (Recommended)'
        no longer triggers session-wide activation.  Only the nonce-targeted
        DB path (test_approve_answer_activates_grants) creates a grant.
        """
        session_id = "test-cycle-session"
        self._deny_creates_db_pending("terraform apply", session_id)

        hook_data = self._make_hook_data(
            answers={"q1": "Approve (Recommended)"},
            session_id=session_id,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        assert not self._grant_active("terraform apply", session_id), (
            "A no-nonce approve must not activate any grant (legacy path dropped)"
        )

    def test_answers_from_tool_input_fallback(self):
        """A nonce-labeled answer in tool_input (fallback) also activates."""
        session_id = "test-cycle-session"
        approval_id = self._deny_creates_db_pending("terraform apply", session_id)
        nonce8 = approval_id[len("P-"):len("P-") + 8]

        hook_data = self._make_hook_data(
            answers={"q1": f"Approve -- terraform apply [P-{nonce8}]"},
            session_id=session_id,
            in_tool_input=True,
        )
        self.adapter._handle_ask_user_question_result(hook_data)

        assert self._grant_active("terraform apply", session_id), (
            "Answers from tool_input fallback should activate the targeted grant"
        )


class TestConsumeGrantAtSubagentStop:
    """Test 12: full DB-only grant cycle with single-use replay protection.

    Lifecycle under test (the canonical consume-on-retry model -- the same
    model proven in test_double_approval_redirect.py):

        deny (DB pending)
          -> activate (DB grant via activate_db_pending_by_prefix)
          -> retry ALLOWED + grant CONSUMED in the same step
          -> second retry RE-BLOCKED (the consumed grant cannot match again).

    (The former consume_session_grants() SubagentStop sweep was removed in the
    approvals redesign M1: grants are consumed at the match, so there is no
    session-end sweep to assert.)

    Single-use is the security invariant: a grant consumed by a matching
    retry must never match a second time within its TTL window. There is no
    "grant survives the session" semantics -- that was an obsolete model that
    contradicted replay protection.

    DB-only: both the approvals chain (insert_requested / get_pending) and the
    grant chain (check_db_semantic_grant / consume_db_semantic_grant) read an
    isolated test DB. No filesystem pending or filesystem grant is involved.
    """

    def test_full_cycle_grant_consumed_at_subagent_stop(self, approvals_test_db, monkeypatch, tmp_path):
        import re
        import gaia.approvals.store as astore
        import gaia.store.writer as gwriter
        from modules.security.approval_grants import activate_db_pending_by_prefix
        from gaia.store.writer import check_db_semantic_grant

        command = "terraform apply"
        session_id = "test-cycle-session"
        db_path, assert_con = approvals_test_db

        # Patch store._open_db so insert_requested writes to our temp file DB.
        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        _orig_get_pending = astore.get_pending

        def _patched_get_pending(session_id=None, all_sessions=False, con=None):
            if con is None:
                con = sqlite3.connect(str(db_path))
            return _orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

        monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

        # Build an isolated writer DB carrying the full v20 approval_grants
        # shape (confirmed + multi_use columns) so the grant lifecycle never
        # touches ~/.gaia/gaia.db.  Every gaia.store.writer DB access is
        # redirected here (insert_semantic_grant / check_db_semantic_grant /
        # consume_db_semantic_grant).
        writer_db_path = tmp_path / "writer_grants.db"

        def _make_writer_db():
            con = sqlite3.connect(str(writer_db_path))
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.create_function(
                "gaia_sha256", 1, lambda v: _sha256(v), deterministic=True,
            )
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS approval_grants (
                    approval_id          TEXT PRIMARY KEY,
                    agent_id             TEXT,
                    session_id           TEXT,
                    command_set_json     TEXT NOT NULL,
                    scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
                    created_at           TEXT NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                    expires_at           TEXT,
                    status               TEXT NOT NULL DEFAULT 'PENDING',
                    consumed_indexes_json TEXT,
                    consumed_at          TEXT,
                    revoked_at           TEXT,
                    multi_use            INTEGER NOT NULL DEFAULT 0,
                    confirmed            INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            con.commit()
            return con

        monkeypatch.setattr(
            gwriter, "_connect", lambda db_path=None: _make_writer_db(),
        )

        # Step 1: Subagent command denied -> DB pending row + approval_id.
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed
        reason = result1.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        m = re.search(r"approval_id:\s*(P-[\w-]+)", reason)
        assert m, f"no approval_id in deny reason: {reason}"
        approval_id = m.group(1)

        pending_rows = astore.get_pending(session_id=session_id, con=assert_con)
        assert len(pending_rows) >= 1, "DB pending row must exist after deny"

        # Step 2: User approves -> DB grant activated (no filesystem involved).
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]
        activation = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert activation.success, f"activation failed: {activation.reason}"

        # Step 3: Retry -> ALLOWED via the active grant, and CONSUMED in-step.
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert result2.allowed, f"active grant should allow the retry, got: {result2.reason}"

        # Step 4: Single-use replay protection -- the consumed grant must not
        # match again.  This is the security invariant the migration enforces.
        assert check_db_semantic_grant(command, session_id=session_id) is None, (
            "grant must be CONSUMED after the matching retry (single-use)"
        )

        # Step 5: A second retry re-blocks (no live grant remains).
        result3 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result3.allowed, "command must re-block after its grant is consumed"

        # (M1) There is no SubagentStop grant sweep: the grant was already
        # consumed at the match, and any unmatched grant would simply expire on
        # its short TTL rather than be swept at session end.
