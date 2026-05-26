#!/usr/bin/env python3
"""Tests for the DB-bridge activation path (M2 cutover fix).

Gap being closed: M2 migrated REQUESTED writes to DB only (no filesystem
pending file), but the activation path in elicitation_result.py and
_handle_ask_user_question_result() still looked up filesystem pending files
first.  When the filesystem file didn't exist, the grant was never activated
and the subagent re-blocked eternally with the same approval_id.

These tests verify:
  1. activate_db_pending_by_prefix() creates a filesystem grant and writes
     SHOWN+APPROVED events to the DB when given a DB-only pending.
  2. The filesystem grant created by activate_db_pending_by_prefix() is
     findable by check_approval_grant() (the CHECK side).
  3. check/write alignment: what the validator reads is what activation writes.
  4. _handle_ask_user_question_result() falls through to DB bridge when
     load_pending_by_nonce_prefix() returns None.
  5. Negative path: activate_db_pending_by_prefix returns NOT_FOUND when
     no DB row matches the prefix.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Sys-path setup
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[5]
HOOKS_DIR = _REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_schema(con: sqlite3.Connection) -> None:
    """Apply v12 approval schema to an in-memory or file-backed connection."""
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
def db_and_store(tmp_path, monkeypatch):
    """File-backed DB + patched store._open_db for isolation."""
    db_path = tmp_path / "test_bridge.db"
    con = sqlite3.connect(str(db_path))
    _make_v12_schema(con)
    con.commit()

    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )

    import gaia.approvals.store as store
    orig_get_pending = store.get_pending

    def patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = sqlite3.connect(str(db_path))
        return orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

    monkeypatch.setattr("gaia.approvals.store.get_pending", patched_get_pending)

    yield db_path, con, store
    con.close()


@pytest.fixture(autouse=True)
def isolated_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for filesystem grants and an isolated DB for
    gaia.store.writer (check_db_semantic_grant / consume_db_semantic_grant).

    The db_and_store fixture patches gaia.approvals.store._open_db for the
    approvals chain.  This fixture additionally patches gaia.store.writer._connect
    so that the new DB-primary check path (check_db_semantic_grant) also reads
    from the test-local file DB rather than ~/.gaia/gaia.db.
    """
    import modules.security.approval_grants as ag

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-bridge-session")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False

    # Patch gaia.store.writer._connect so check_db_semantic_grant reads from an
    # isolated DB (not ~/.gaia/gaia.db).  The approval_grants table is created
    # on demand in this empty DB -- no rows, so the DB path returns None and
    # check_approval_grant() falls through to the filesystem path as before.
    writer_db_path = tmp_path / "writer_isolation.db"
    import sqlite3 as _sqlite3
    import hashlib as _hashlib

    def _make_writer_db() -> _sqlite3.Connection:
        con = _sqlite3.connect(str(writer_db_path))
        con.row_factory = _sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1,
            lambda v: _hashlib.sha256((v or "").encode()).hexdigest(),
            deterministic=True,
        )
        # Ensure all tables used by both gaia.approvals.store and
        # gaia.store.writer exist in the isolation DB.
        # gaia.approvals.store._open_db() delegates to gaia.store.writer._connect(),
        # so patching _connect affects both paths.
        con.executescript("""
            CREATE TABLE IF NOT EXISTS approvals (
                id           TEXT PRIMARY KEY,
                agent_id     TEXT,
                session_id   TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                fingerprint  TEXT,
                payload_json TEXT,
                created_at   TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                decided_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS approval_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id   TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                agent_id      TEXT,
                session_id    TEXT,
                payload_json  TEXT,
                fingerprint   TEXT,
                prev_hash     TEXT,
                this_hash     TEXT,
                metadata_json TEXT,
                created_at    TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (approval_id) REFERENCES approvals(id)
            );

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
                revoked_at           TEXT
            );
        """)
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", lambda db_path_arg=None: _make_writer_db())

    yield grants_dir


# ---------------------------------------------------------------------------
# Test 1: activate_db_pending_by_prefix creates filesystem grant + DB events
# ---------------------------------------------------------------------------

class TestActivateDbPendingByPrefix:
    """Core unit tests for activate_db_pending_by_prefix()."""

    def test_activates_db_pending_creates_grant(self, db_and_store):
        """Given a DB REQUESTED row, activation creates a filesystem grant + DB events."""
        db_path, assert_con, store = db_and_store
        command = "terraform apply"
        session_id = "test-bridge-session"

        # Insert a REQUESTED approval into the DB (simulates what bash_validator does).
        payload = _build_sealed_payload(command)
        approval_id = store.insert_requested(
            payload,
            agent_id="test-agent",
            session_id=session_id,
        )
        assert approval_id.startswith("P-"), f"Expected P-prefix, got: {approval_id}"

        # Extract the nonce prefix (first 8 chars after "P-").
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            check_approval_grant,
            ACTIVATION_ACTIVATED,
        )

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )

        assert result.success, f"Activation should succeed, got: {result.reason}"
        assert result.status == ACTIVATION_ACTIVATED
        assert result.grant_path is not None
        assert result.grant_path.exists(), "Filesystem grant file should exist"

    def test_db_events_written(self, db_and_store):
        """SHOWN and APPROVED events are written to approval_events."""
        db_path, assert_con, store = db_and_store
        command = "git push origin main"
        session_id = "test-bridge-session"

        payload = _build_sealed_payload(command)
        approval_id = store.insert_requested(
            payload,
            agent_id="test-agent",
            session_id=session_id,
        )
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import activate_db_pending_by_prefix

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success

        # Verify DB events: REQUESTED + SHOWN + APPROVED.
        events = store.replay_for_approval(approval_id, con=assert_con)
        event_types = [e["event_type"] for e in events]
        assert "REQUESTED" in event_types, f"REQUESTED missing from {event_types}"
        assert "SHOWN" in event_types, f"SHOWN missing from {event_types}"
        assert "APPROVED" in event_types, f"APPROVED missing from {event_types}"

    def test_status_flipped_to_approved(self, db_and_store):
        """approvals.status is updated to 'approved' after activation."""
        db_path, assert_con, store = db_and_store
        command = "kubectl delete pod mypod"
        session_id = "test-bridge-session"

        payload = _build_sealed_payload(command)
        approval_id = store.insert_requested(
            payload,
            session_id=session_id,
        )
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import activate_db_pending_by_prefix

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success

        # Check DB status.
        row = assert_con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == "approved", f"Expected status='approved', got: {row[0]}"

    def test_not_found_returns_error(self):
        """NOT_FOUND when no DB row matches the prefix."""
        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            ACTIVATION_NOT_FOUND,
        )

        result = activate_db_pending_by_prefix(
            "deadbeef", current_session_id="test-bridge-session",
        )
        assert not result.success
        assert result.status == ACTIVATION_NOT_FOUND


# ---------------------------------------------------------------------------
# Test 2: check/write alignment
# ---------------------------------------------------------------------------

class TestCheckWriteAlignment:
    """The filesystem grant written by activation must be found by check_approval_grant()."""

    def test_grant_is_checkable_after_activation(self, db_and_store):
        """check_approval_grant() returns the grant created by activate_db_pending_by_prefix()."""
        db_path, assert_con, store = db_and_store
        command = "terraform apply"
        session_id = "test-bridge-session"

        payload = _build_sealed_payload(command)
        approval_id = store.insert_requested(
            payload,
            agent_id="test-agent",
            session_id=session_id,
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

        # The CHECK side should find the grant.
        grant = check_approval_grant(command, session_id=session_id)
        assert grant is not None, (
            "check_approval_grant() must find the grant written by activate_db_pending_by_prefix()"
        )
        assert grant.approved_scope == command
        assert grant.confirmed, "Grant must have confirmed=True (user already approved)"

    def test_full_cycle_deny_activate_retry(self, db_and_store, monkeypatch):
        """End-to-end cycle: DB REQUESTED -> activation bridge -> validator passthrough.

        This replicates the exact scenario from the E2E failure:
          1. bash_validator blocks a T3 command and calls insert_requested() -> DB.
          2. User approves via AskUserQuestion with [P-{prefix}] label.
          3. _handle_ask_user_question_result (or elicitation_result) calls
             activate_db_pending_by_prefix() because no filesystem pending exists.
          4. Filesystem grant is created.
          5. bash_validator retry finds the grant and allows the command.
        """
        import gaia.approvals.store as astore

        command = "terraform apply"
        session_id = "test-bridge-session"
        db_path, assert_con, store = db_and_store

        # Step 1: Subagent command is denied (writes DB REQUESTED row).
        from modules.tools.bash_validator import validate_bash_command

        result1 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed, "T3 command should be blocked"

        hook_output = result1.block_response["hookSpecificOutput"]
        reason = hook_output["permissionDecisionReason"]
        match = re.search(r"approval_id:\s*(P-[\w-]+)", reason)
        assert match, f"Could not extract approval_id from deny reason: {reason}"
        approval_id = match.group(1)
        assert approval_id.startswith("P-"), f"approval_id should start with P-: {approval_id}"

        # Confirm DB row exists (REQUESTED written).
        pending_rows = astore.get_pending(session_id=session_id, con=assert_con)
        assert len(pending_rows) >= 1, "DB pending row must exist after deny"
        assert pending_rows[0]["id"] == approval_id

        # Step 2: Simulate user approval via AskUserQuestion.
        # Extract nonce prefix from approval_id: P-{nonce_prefix=first_8_chars}...
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            check_approval_grant,
        )

        activation_result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert activation_result.success, (
            f"DB-bridge activation must succeed: {activation_result.reason}"
        )

        # Step 3: Retry the command -- should pass through.
        result2 = validate_bash_command(
            command, is_subagent=True, session_id=session_id,
        )
        assert result2.allowed, (
            f"Retry should be allowed after DB-bridge activation, got: {result2.reason}"
        )

        # Step 4: DB state -- status should be 'approved'.
        row = assert_con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row is not None
        assert row[0] == "approved", f"Expected status='approved', got: {row[0]}"

        # Step 5: approval_events should have REQUESTED + SHOWN + APPROVED.
        events = astore.replay_for_approval(approval_id, con=assert_con)
        event_types = [e["event_type"] for e in events]
        assert "REQUESTED" in event_types
        assert "SHOWN" in event_types
        assert "APPROVED" in event_types


# ---------------------------------------------------------------------------
# Test 3: _handle_ask_user_question_result falls through to DB bridge
# ---------------------------------------------------------------------------

class TestHandleAskUserQuestionDbBridge:
    """_handle_ask_user_question_result uses DB bridge when filesystem pending is absent."""

    def test_adapter_db_bridge_on_approve(self, db_and_store):
        """When an AskUserQuestion answer has a [P-xxx] nonce but no filesystem
        pending exists, the adapter activates via DB bridge."""
        db_path, assert_con, store = db_and_store
        command = "terraform apply"
        session_id = "test-bridge-session"

        payload = _build_sealed_payload(command)
        approval_id = store.insert_requested(
            payload,
            agent_id="test-agent",
            session_id=session_id,
        )
        # No filesystem pending file is written (M2 state: DB only).

        # Build an AskUserQuestion hook_data with a nonce-labeled approve answer.
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]
        approve_label = f"Approve -- terraform apply [P-{nonce_prefix}]"

        hook_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {},
            "tool_response": {"answers": {"Proceed?": approve_label}},
        }

        ADAPTERS_DIR = HOOKS_DIR / "adapters"
        sys.path.insert(0, str(ADAPTERS_DIR))
        from adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        adapter._handle_ask_user_question_result(hook_data)

        # Verify the filesystem grant was created.
        from modules.security.approval_grants import check_approval_grant

        grant = check_approval_grant(command, session_id=session_id)
        assert grant is not None, (
            "Filesystem grant must exist after _handle_ask_user_question_result "
            "activates via DB bridge"
        )
        assert grant.confirmed

        # Verify DB events.
        events = store.replay_for_approval(approval_id, con=assert_con)
        event_types = [e["event_type"] for e in events]
        assert "SHOWN" in event_types, f"SHOWN missing: {event_types}"
        assert "APPROVED" in event_types, f"APPROVED missing: {event_types}"

        # Verify status flipped.
        row = assert_con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "approved"

    def test_adapter_no_activation_on_reject(self, db_and_store):
        """When the user rejects, no grant is created and DB status stays pending."""
        db_path, assert_con, store = db_and_store
        command = "git push origin main"
        session_id = "test-bridge-session"

        payload = _build_sealed_payload(command)
        approval_id = store.insert_requested(
            payload,
            session_id=session_id,
        )

        hook_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": "AskUserQuestion",
            "session_id": session_id,
            "tool_input": {},
            "tool_response": {"answers": {"Proceed?": "Reject"}},
        }

        ADAPTERS_DIR = HOOKS_DIR / "adapters"
        sys.path.insert(0, str(ADAPTERS_DIR))
        from adapters.claude_code import ClaudeCodeAdapter

        adapter = ClaudeCodeAdapter()
        adapter._handle_ask_user_question_result(hook_data)

        from modules.security.approval_grants import check_approval_grant

        grant = check_approval_grant(command, session_id=session_id)
        assert grant is None, "No grant should be created on rejection"

        row = assert_con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "pending", f"Status should stay pending on rejection, got: {row[0]}"
