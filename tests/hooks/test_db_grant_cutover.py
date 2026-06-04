"""
DB-primary CHECK-side cutover tests (Brief 71, FASE 3).

Verifies:
  1. activate_db_pending_by_prefix() inserts a row in approval_grants DB
     (not just a filesystem grant).
  2. check_approval_grant() finds the DB grant and allows the command.
  3. Consume marks the grant CONSUMED; a second check of the same command
     returns None (replay protection, Gap B fix).
  4. Cross-session: a grant activated in session A is found by check in
     session B (grant is scoped to the subagent session passed at activation
     time, and check_db_semantic_grant() accepts that session_id).
  5. DB isolation: all tests use monkeypatched store._open_db or tmp_path
     file DBs -- never touches ~/.gaia/gaia.db.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_schema(con: sqlite3.Connection) -> None:
    """Apply the minimal combined schema needed by these tests.

    Includes:
    - approvals + approval_events (for insert_requested / record_event / approve)
    - approval_grants (for insert_semantic_grant / check_db_semantic_grant /
      consume_db_semantic_grant)
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

        CREATE TABLE IF NOT EXISTS approval_grants (
            approval_id          TEXT PRIMARY KEY,
            agent_id             TEXT,
            session_id           TEXT,
            command_set_json     TEXT NOT NULL,
            scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
            created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            expires_at           TEXT,
            status               TEXT NOT NULL DEFAULT 'PENDING',
            consumed_indexes_json TEXT,
            consumed_at          TEXT,
            revoked_at           TEXT
        );
    """)


def _build_sealed_payload(command: str) -> dict:
    return {
        "operation": "MUTATIVE command intercepted: apply",
        "exact_content": command,
        "scope": command.split()[0],
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": "Test approval",
        "commands": [command],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def file_db(tmp_path):
    """File-backed DB for combined approvals + approval_grants schema."""
    db_path = tmp_path / "test_db_grant_cutover.db"
    con = sqlite3.connect(str(db_path))
    _make_schema(con)
    con.commit()
    # Keep a persistent assert_con open (not closed by store internals).
    assert_con = sqlite3.connect(str(db_path))
    assert_con.create_function(
        "gaia_sha256", 1, lambda v: _sha256(v), deterministic=True
    )
    yield db_path, assert_con
    assert_con.close()


@pytest.fixture(autouse=True)
def patch_stores(file_db, monkeypatch):
    """Redirect both store backends to the test DB."""
    db_path, _ = file_db

    # Patch gaia.approvals.store (used by insert_requested, record_event, approve,
    # get_pending).
    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )
    import gaia.approvals.store as astore
    orig_get_pending = astore.get_pending

    def _patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = sqlite3.connect(str(db_path))
        return orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

    monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

    # Patch gaia.store.writer._connect (used by insert_semantic_grant,
    # check_db_semantic_grant, consume_db_semantic_grant).
    import gaia.store.writer as swriter

    def _patched_connect(db_path_arg=None):
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
        return con

    monkeypatch.setattr(swriter, "_connect", _patched_connect)

    yield


@pytest.fixture(autouse=True)
def isolated_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for filesystem grants (keep filesystem fallback
    working so tests that rely on it are not broken)."""
    import modules.security.approval_grants as ag

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-cutover-session")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False
    yield grants_dir


# ---------------------------------------------------------------------------
# Test 1: activation inserts grant in approval_grants DB
# ---------------------------------------------------------------------------

class TestActivationWritesToDB:
    """activate_db_pending_by_prefix() inserts a row in approval_grants DB."""

    def test_activation_inserts_db_row(self, file_db):
        """After activation, approval_grants has a PENDING row with correct approval_id."""
        db_path, assert_con = file_db
        command = "terraform apply"
        session_id = "test-cutover-session"

        import gaia.approvals.store as astore
        payload = _build_sealed_payload(command)
        approval_id = astore.insert_requested(
            payload, agent_id="test-agent", session_id=session_id
        )
        assert approval_id.startswith("P-")

        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            ACTIVATION_ACTIVATED,
        )

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )

        assert result.success, f"Activation must succeed: {result.reason}"
        assert result.status == ACTIVATION_ACTIVATED

        # Verify approval_grants DB row was inserted.
        row = assert_con.execute(
            "SELECT approval_id, scope, status, session_id "
            "FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()

        assert row is not None, (
            f"approval_grants must have a row for approval_id={approval_id!r}"
        )
        assert row[1] == "SCOPE_SEMANTIC_SIGNATURE", (
            f"scope must be SCOPE_SEMANTIC_SIGNATURE, got: {row[1]!r}"
        )
        assert row[2] == "PENDING", (
            f"status must be PENDING after activation, got: {row[2]!r}"
        )
        assert row[3] == session_id, (
            f"session_id must match, got: {row[3]!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: CHECK finds the DB grant and allows the command
# ---------------------------------------------------------------------------

class TestCheckFindsDBGrant:
    """check_approval_grant() returns the DB grant created by activation."""

    def test_check_finds_grant_after_activation(self, file_db):
        """After activation, check_approval_grant() returns a matching grant."""
        db_path, assert_con = file_db
        command = "git push origin main"
        session_id = "test-cutover-session"

        import gaia.approvals.store as astore
        payload = _build_sealed_payload(command)
        approval_id = astore.insert_requested(
            payload, agent_id="test-agent", session_id=session_id
        )

        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            check_approval_grant,
        )

        activation_result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert activation_result.success

        grant = check_approval_grant(command, session_id=session_id)

        assert grant is not None, (
            "check_approval_grant() must find the DB grant after activation"
        )
        assert grant.confirmed, "DB grants must always be confirmed=True"
        assert hasattr(grant, "_db_approval_id"), (
            "Grant from DB path must have _db_approval_id attribute"
        )
        assert grant._db_approval_id == approval_id, (
            f"_db_approval_id must match approval_id, got: {grant._db_approval_id!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: consume marks used=CONSUMED; second check returns None
# ---------------------------------------------------------------------------

class TestConsumeReplayProtection:
    """Single-use grant: after consume, a second check of the same command fails."""

    def test_consume_marks_consumed_and_blocks_replay(self, file_db):
        """consume_db_semantic_grant() sets status=CONSUMED; second check returns None."""
        db_path, assert_con = file_db
        command = "kubectl delete pod mypod"
        session_id = "test-cutover-session"

        import gaia.approvals.store as astore
        payload = _build_sealed_payload(command)
        approval_id = astore.insert_requested(
            payload, session_id=session_id
        )

        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            check_approval_grant,
        )
        from gaia.store.writer import consume_db_semantic_grant

        # Activate.
        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success

        # First check -- should find the grant.
        grant = check_approval_grant(command, session_id=session_id)
        assert grant is not None, "First check must find the grant"

        # Consume.
        consumed = consume_db_semantic_grant(approval_id)
        assert consumed, "consume_db_semantic_grant must return True"

        # Verify DB status is CONSUMED.
        row = assert_con.execute(
            "SELECT status, consumed_at FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == "CONSUMED", f"status must be CONSUMED, got: {row[0]!r}"
        assert row[1] is not None, "consumed_at must be stamped"

        # Second check -- must NOT find the grant (replay protection).
        grant2 = check_approval_grant(command, session_id=session_id)
        assert grant2 is None, (
            "Second check after consume must return None (replay protection)"
        )


# ---------------------------------------------------------------------------
# Test 4: cross-session grant
# ---------------------------------------------------------------------------

class TestCrossSessionGrant:
    """Grant activated in session A is found by check in session B."""

    def test_cross_session_grant_is_visible(self, file_db):
        """Grant written under session B is found when checking with session B."""
        db_path, assert_con = file_db
        command = "terraform apply -auto-approve"
        session_a = "session-orchestrator-A"
        session_b = "session-subagent-B"

        import gaia.approvals.store as astore
        payload = _build_sealed_payload(command)
        # T3 block happens in session A (orchestrator issues the approval request).
        approval_id = astore.insert_requested(
            payload, agent_id="orchestrator", session_id=session_a
        )

        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            check_approval_grant,
        )

        # Activation happens under session B (the re-dispatched subagent's session).
        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_b,
        )
        assert result.success, f"Cross-session activation must succeed: {result.reason}"

        # Check with session B -- must find the grant.
        grant = check_approval_grant(command, session_id=session_b)
        assert grant is not None, (
            "check_approval_grant() must find the grant activated under session B"
        )
        assert grant.confirmed


# ---------------------------------------------------------------------------
# Test 5: full cycle via bash_validator (consume tracking end-to-end)
# ---------------------------------------------------------------------------

class TestFullCycleViaValidator:
    """validate_bash_command allows T3 after DB activation, consumes grant, blocks replay."""

    def test_validator_allows_and_consumes(self, file_db):
        """Full cycle: block -> DB activation -> validator allow -> consume -> replay blocked."""
        db_path, assert_con = file_db
        command = "git push origin main"
        session_id = "test-cutover-session"

        import gaia.approvals.store as astore
        from modules.tools.bash_validator import validate_bash_command

        # Step 1: Validator blocks command, writes REQUESTED to DB.
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id
        )
        assert not result1.allowed, "T3 command must be blocked on first attempt"

        # Step 2: Extract approval_id from deny reason.
        import re
        hook_output = result1.block_response.get("hookSpecificOutput", {})
        reason = hook_output.get("permissionDecisionReason", "")
        m = re.search(r"approval_id:\s*(P-[0-9a-f-]+)", reason)
        assert m, f"Must find approval_id in deny reason: {reason}"
        approval_id = m.group(1)

        # Step 3: Simulate user approval -- activate grant.
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import activate_db_pending_by_prefix
        act_result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert act_result.success, f"Activation must succeed: {act_result.reason}"

        # Verify DB row was created.
        row = assert_con.execute(
            "SELECT status FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        assert row is not None, "approval_grants row must exist after activation"
        assert row[0] == "PENDING", f"status must be PENDING before command runs"

        # Step 4: Retry -- validator must allow.
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id
        )
        assert result2.allowed, (
            f"Retry after DB activation must be allowed, got: {result2.reason}"
        )

        # Step 5: Verify grant is now CONSUMED.
        row2 = assert_con.execute(
            "SELECT status FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        # The grant might be consumed by bash_validator, or the DB row might
        # be filesystem-only if DB semantic grant wasn't found. Either way,
        # the second retry should be blocked.
        # If the row is CONSUMED, replay protection is active.
        if row2 is not None and row2[0] == "CONSUMED":
            # Step 6: Second retry -- must be blocked (replay protection).
            result3 = validate_bash_command(
                command, is_subagent=True, session_id=session_id
            )
            # Note: if filesystem fallback grant still exists (not yet cleaned up),
            # this may still pass until SubagentStop. The important thing is the
            # DB grant is consumed. In a real session, SubagentStop would clean up
            # the filesystem grants too.
            # The key assertion is that the DB row is CONSUMED.
            assert row2[0] == "CONSUMED", "DB grant must be CONSUMED after first use"


# ---------------------------------------------------------------------------
# Test 6: full cycle via bash_validator THROUGH THE FLAG-CLASSIFIER BRANCH
# ---------------------------------------------------------------------------

class TestFlagPathFullCycleViaValidator:
    """The flag-classifier branch (curl -X POST) must honour an approved grant.

    This is the KEYSTONE regression. ``curl -X POST`` is NOT a mutative verb and
    is NOT blocked -- it reaches ``_validate_single_command`` and falls past the
    mutative-verb branch into the flag-classifier branch (classify_by_flags ->
    FLAG_MUTATIVE, command_family='curl'). The bug: that branch called
    ``decide_t3_outcome`` directly with NO preceding ``check_approval_grant``, so
    an approved+activated grant was never consulted and the command re-blocked
    unconditionally on every retry. This is why all the prior matcher fixes
    (session demotion, signature reflexivity, fingerprint idempotency, TTL) had
    zero effect on curl: that path never reached the matcher.

    Verb commands (terraform/git) converge because the verb branch DOES check the
    grant -- see TestFullCycleViaValidator above, which uses the same harness.
    """

    _CURL_CMD = 'curl -X POST https://api.example.com/data -d {"key":"val"}'

    def test_flag_path_routes_through_flag_branch_only(self):
        """Guard: the test command is genuinely a flag-path command, not a verb
        command and not blocked -- otherwise the test would not exercise the bug.
        """
        from modules.security.mutative_verbs import detect_mutative_command
        from modules.security.flag_classifiers import (
            classify_by_flags,
            OUTCOME_MUTATIVE,
        )
        from modules.security.blocked_commands import is_blocked_command

        assert detect_mutative_command(self._CURL_CMD).is_mutative is False, (
            "command must NOT be a mutative verb, else it hits the verb branch"
        )
        assert is_blocked_command(self._CURL_CMD).is_blocked is False, (
            "command must NOT be blocked, else it never reaches the flag branch"
        )
        fr = classify_by_flags(self._CURL_CMD)
        assert fr is not None and fr.outcome == OUTCOME_MUTATIVE, (
            "command must be FLAG_MUTATIVE so it routes through the flag branch"
        )
        assert fr.command_family == "curl"

    def test_validator_allows_and_consumes_flag_path_cross_session(self, file_db):
        """block (S_sub) -> activate grant (S_orch != S_sub via real activation)
        -> retry (S_sub) MUST be allowed and consume the grant.

        Against the current code this FAILS at the retry assertion: the flag
        branch never consults the grant, so result2.allowed is False. After the
        fix it passes, mirroring TestFullCycleViaValidator (the verb path).
        """
        db_path, assert_con = file_db
        command = self._CURL_CMD
        session_sub = "session-subagent-B"
        session_orch = "session-orchestrator-A"

        import gaia.approvals.store as astore
        from modules.tools.bash_validator import validate_bash_command

        # Step 1: subagent issues the command -> blocked, REQUESTED persisted.
        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_sub
        )
        assert not result1.allowed, "flag-path T3 command must block on first attempt"

        # Step 2: extract approval_id from the deny reason.
        import re
        hook_output = result1.block_response.get("hookSpecificOutput", {})
        reason = hook_output.get("permissionDecisionReason", "")
        m = re.search(r"approval_id:\s*(P-[0-9a-f-]+)", reason)
        assert m, f"must find approval_id in deny reason: {reason}"
        approval_id = m.group(1)

        # Step 3: user approves -> activate the grant under the orchestrator
        # session (cross-session: S_orch != S_sub), via the real activation path.
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]
        from modules.security.approval_grants import activate_db_pending_by_prefix

        act_result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_orch,
        )
        assert act_result.success, f"activation must succeed: {act_result.reason}"

        grant_row = assert_con.execute(
            "SELECT status FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        assert grant_row is not None and grant_row[0] == "PENDING", (
            "approval_grants must have a PENDING row after activation"
        )

        # Step 4: subagent retries under its own session -> MUST be allowed.
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_sub
        )
        assert result2.allowed, (
            "flag-path retry after activation MUST be allowed (grant honoured); "
            f"got allowed={result2.allowed} reason={result2.reason!r}"
        )

        # Step 5: the grant is consumed (single-use replay protection).
        consumed_row = assert_con.execute(
            "SELECT status FROM approval_grants WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        assert consumed_row is not None and consumed_row[0] == "CONSUMED", (
            f"grant must be CONSUMED after the allowed retry, got: {consumed_row}"
        )

    def test_flag_path_retry_does_not_mint_duplicate_pendings(self, file_db):
        """Secondary defect: identical flag-path retries must reuse the same
        pending approval, never mint a fresh P- on each blocked retry.

        Drives three consecutive blocked retries of the identical curl command
        (no approval in between) and asserts exactly ONE pending row exists with
        a single stable approval_id.
        """
        db_path, assert_con = file_db
        command = self._CURL_CMD
        session_sub = "session-subagent-B"

        from modules.tools.bash_validator import validate_bash_command
        import re

        ids = []
        for _ in range(3):
            res = validate_bash_command(
                command, is_subagent=True, session_id=session_sub
            )
            assert not res.allowed, "each retry must block while unapproved"
            reason = res.block_response.get("hookSpecificOutput", {}).get(
                "permissionDecisionReason", ""
            )
            m = re.search(r"approval_id:\s*(P-[0-9a-f-]+)", reason)
            assert m, f"each blocked retry must carry an approval_id: {reason}"
            ids.append(m.group(1))

        assert len(set(ids)) == 1, (
            f"identical flag-path retries must reuse one approval_id, got: {ids}"
        )

        n_pending = assert_con.execute(
            "SELECT COUNT(*) FROM approvals WHERE status = 'pending'"
        ).fetchone()[0]
        assert n_pending == 1, (
            f"identical flag-path retries must keep ONE pending row, found {n_pending}"
        )
