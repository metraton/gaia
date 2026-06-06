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


def _build_command_set_payload(command_set: list[dict]) -> dict:
    """Build a multi-command (COMMAND_SET) sealed_payload.

    Mirrors what bash_validator._build_sealed_payload() emits when a
    ``command_set`` of more than one {command, rationale} item is supplied:
    ``commands`` lists every command string and a verbatim ``command_set`` key
    carries the full set. ``exact_content`` is the first command (the singular
    stand-in), which the activation path must NOT degrade to.
    """
    first = command_set[0]["command"]
    return {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": first,
        "scope": first.split()[0],
        "risk_level": "medium",
        "rollback_hint": None,
        "rationale": "Batch under one consent",
        "commands": [it["command"] for it in command_set],
        "command_set": command_set,
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


# ---------------------------------------------------------------------------
# Test 4: COMMAND_SET create-side wiring (multi-command under one consent)
#
# Closes the orphaned-create gap: activate_db_pending_by_prefix must branch
# into create_command_set_grant when the approved payload carries a set of
# more than one command, and the resulting grant must be consumable by the
# existing bash_validator consume path (match_command_set_grant +
# mark_command_set_item_consumed) without breaking replay protection.
# ---------------------------------------------------------------------------

class TestActivateDbPendingCommandSet:
    """activate_db_pending_by_prefix wires the COMMAND_SET create side."""

    def test_multi_command_payload_creates_command_set_grant(self, db_and_store):
        """A payload with >1 command activates into ONE COMMAND_SET grant,
        not a degraded single-command semantic grant."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "git add -A", "rationale": "stage"},
            {"command": "git commit -m 'fix'", "rationale": "record"},
            {"command": "git push origin main", "rationale": "publish"},
        ]

        payload = _build_command_set_payload(command_set)
        approval_id = store.insert_requested(
            payload, agent_id="test-agent", session_id=session_id,
        )
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            ACTIVATION_ACTIVATED,
        )

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )

        assert result.success, f"Activation should succeed: {result.reason}"
        assert result.status == ACTIVATION_ACTIVATED
        # COMMAND_SET grants are pure DB rows -- no filesystem grant file.
        assert result.grant_path is None
        assert "COMMAND_SET" in result.reason

        # The DB grant must be a COMMAND_SET with all 3 commands and nothing
        # consumed yet -- i.e. NOT degraded to a single command.
        from gaia.store.writer import list_approval_grants
        grants = list_approval_grants(
            session_id=session_id, status="PENDING",
        )
        cs_grants = [g for g in grants if g.get("scope") == "COMMAND_SET"]
        assert len(cs_grants) == 1, f"Expected 1 COMMAND_SET grant, got {len(cs_grants)}"
        row = cs_grants[0]
        assert row["approval_id"] == approval_id
        stored_set = json.loads(row["command_set_json"])
        assert [it["command"] for it in stored_set] == [
            c["command"] for c in command_set
        ]
        assert json.loads(row["consumed_indexes_json"] or "[]") == []

    def test_command_set_grant_ttl_is_60_minutes(self, db_and_store):
        """The COMMAND_SET grant created on activation carries a 60-minute TTL."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "terraform apply", "rationale": "provision"},
            {"command": "terraform output", "rationale": "read"},
        ]
        payload = _build_command_set_payload(command_set)
        approval_id = store.insert_requested(payload, session_id=session_id)
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            DEFAULT_COMMAND_SET_TTL_MINUTES,
        )
        from datetime import datetime, timezone

        assert DEFAULT_COMMAND_SET_TTL_MINUTES == 60

        before = datetime.now(timezone.utc)
        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success

        from gaia.store.writer import list_approval_grants
        grants = list_approval_grants(session_id=session_id, status="PENDING")
        row = next(g for g in grants if g.get("scope") == "COMMAND_SET")
        expires_at = datetime.strptime(
            row["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        ttl_minutes = (expires_at - before).total_seconds() / 60
        # Allow a small execution-time window; the window is centred on 60.
        assert 59 <= ttl_minutes <= 61, (
            f"COMMAND_SET TTL must be ~60 min, got {ttl_minutes:.2f}"
        )

    def test_command_set_consumable_by_bash_validator(self, db_and_store):
        """End-to-end: the COMMAND_SET grant created on activation is matched
        and consumed by the existing bash_validator consume path, item by item,
        with replay protection intact."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "git push origin main", "rationale": "publish"},
            {"command": "git push origin tags", "rationale": "tags"},
        ]
        payload = _build_command_set_payload(command_set)
        approval_id = store.insert_requested(payload, session_id=session_id)
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            match_command_set_grant,
        )
        from gaia.store.writer import mark_command_set_item_consumed

        assert activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        ).success

        # First command: matches at index 0, consume it.
        m0 = match_command_set_grant("git push origin main")
        assert m0 is not None
        aid0, idx0 = m0
        assert aid0 == approval_id and idx0 == 0
        mark_command_set_item_consumed(aid0, idx0)

        # Replay protection: the same command no longer matches (index consumed).
        assert match_command_set_grant(
            "git push origin main",
        ) is None

        # Second command still matches at its own index (single consent, multi item).
        m1 = match_command_set_grant("git push origin tags")
        assert m1 is not None
        aid1, idx1 = m1
        assert aid1 == approval_id and idx1 == 1
        mark_command_set_item_consumed(aid1, idx1)

        # Whole set consumed -> grant flips to CONSUMED, nothing matches.
        assert match_command_set_grant(
            "git push origin tags",
        ) is None

    def test_command_set_consumed_cross_and_empty_session(self, db_and_store):
        """REGRESSION (live bug, grant P-651b5e55): the COMMAND_SET consume path
        MUST be session-agnostic, exactly like the singular semantic path
        (check_db_semantic_grant).

        The live failure: a COMMAND_SET grant was created and activated under
        session A. The approved T3 commands were then retried from the bash
        subprocess where CLAUDE_SESSION_ID is NOT exported, so
        get_session_id() resolved to the literal "default". The OLD
        match_command_set_grant queried list_approval_grants(session_id=...),
        which appended `AND session_id = 'default'` to the WHERE clause and
        therefore never returned the grant created under session A. The grant
        stayed PENDING and the commands ran WITHOUT being consumed -- a
        consumption bypass that defeats single-use replay protection.

        Why the previous unit/harness tests did not catch it: EVERY existing
        COMMAND_SET test (here and in tests/hooks/test_approval_grants.py)
        creates the grant and matches it under the SAME controlled session_id
        (sess-1 .. sess-8, "test-bridge-session"). Aligning the two sessions
        silently encoded the broken session-scoped assumption -- the filter was
        always satisfied, so the bug was invisible. This test deliberately
        DIVERGES the sessions: it activates under session A and consumes under a
        DIFFERENT session B and under an EMPTY session (the NOT_SET surrogate).

        Run against the OLD code (list_approval_grants with a session_id
        filter), the first cross-session match below returns None and this test
        is RED. With the fix (list_command_set_grants_agnostic, no session
        filter) it is GREEN, while replay protection and per-index matching stay
        intact.
        """
        db_path, assert_con, store = db_and_store
        session_a = "test-bridge-session"   # the activation session (env-set)
        session_b = "a-totally-different-session-id"  # the retry session
        command_set = [
            {"command": "git push origin main", "rationale": "publish"},
            {"command": "git push origin tags", "rationale": "tags"},
            {"command": "git push origin release", "rationale": "release"},
        ]
        payload = _build_command_set_payload(command_set)
        approval_id = store.insert_requested(payload, session_id=session_a)
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            match_command_set_grant,
        )
        from gaia.store.writer import (
            list_approval_grants,
            mark_command_set_item_consumed,
        )

        # Activate the grant under session A.
        assert activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_a,
        ).success

        # Demonstrate the ROOT CAUSE: the old session-scoped query would have
        # found NOTHING under session B (or under "default"). This documents the
        # exact line the fix removes -- the grant lives under session A only.
        assert list_approval_grants(
            session_id=session_b, status="PENDING",
        ) == [], "grant must be invisible to a session-scoped lookup under B"
        assert list_approval_grants(
            session_id="default", status="PENDING",
        ) == [], "grant must be invisible to a session-scoped lookup under 'default'"

        # Index 0: consume under a DIFFERENT session (B). Session-agnostic match
        # MUST find it. On the old code this returns None (RED).
        m0 = match_command_set_grant("git push origin main")
        assert m0 is not None, (
            "cross-session consume must succeed -- this is the live bug"
        )
        aid0, idx0 = m0
        assert aid0 == approval_id and idx0 == 0
        res0 = mark_command_set_item_consumed(aid0, idx0)
        assert res0["status"] == "applied" and res0["all_consumed"] is False

        # Replay protection survives cross-session: index 0 no longer matches.
        assert match_command_set_grant(
            "git push origin main",
        ) is None

        # Index 1: consume under an EMPTY session (the NOT_SET surrogate -- what
        # the bash subprocess actually passes). Must still match.
        m1 = match_command_set_grant("git push origin tags")
        assert m1 is not None, "empty-session consume must succeed"
        aid1, idx1 = m1
        assert aid1 == approval_id and idx1 == 1
        mark_command_set_item_consumed(aid1, idx1)

        # Index 2: consume under yet another session. Last item -> grant CONSUMED.
        m2 = match_command_set_grant(
            "git push origin release",
        )
        assert m2 is not None
        aid2, idx2 = m2
        assert aid2 == approval_id and idx2 == 2
        res2 = mark_command_set_item_consumed(aid2, idx2)
        assert res2["all_consumed"] is True

        # Final state: all three indexes consumed, status CONSUMED, consumed_at
        # stamped -- regardless of which sessions did the consuming.
        rows = list_approval_grants(status="CONSUMED")
        consumed = [r for r in rows if r["approval_id"] == approval_id]
        assert len(consumed) == 1
        row = consumed[0]
        assert json.loads(row["consumed_indexes_json"]) == [0, 1, 2]
        assert row["status"] == "CONSUMED"
        assert row["consumed_at"] is not None

        # And a fully-consumed grant matches nothing further, in ANY session.
        assert match_command_set_grant(
            "git push origin release",
        ) is None

    def test_single_command_payload_does_not_create_command_set(self, db_and_store):
        """A payload with exactly one command_set item is NOT a batch: it stays
        the singular semantic-signature path, no COMMAND_SET grant is minted."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        # command_set with a single item -> must behave as singular.
        payload = _build_command_set_payload(
            [{"command": "terraform apply", "rationale": "one"}]
        )
        approval_id = store.insert_requested(payload, session_id=session_id)
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            check_approval_grant,
        )

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success
        # Singular path -> a filesystem grant exists, no COMMAND_SET grant.
        assert result.grant_path is not None

        from gaia.store.writer import list_approval_grants
        grants = list_approval_grants(session_id=session_id, status="PENDING")
        cs_grants = [g for g in grants if g.get("scope") == "COMMAND_SET"]
        assert cs_grants == [], "A single-command payload must not mint a COMMAND_SET grant"

        # And the singular semantic grant is checkable as before.
        assert check_approval_grant("terraform apply", session_id=session_id) is not None


# ---------------------------------------------------------------------------
# Test 5: _build_sealed_payload (bash_validator) carries the command_set
# ---------------------------------------------------------------------------

class TestSealedPayloadCommandSet:
    """The envelope builder must carry a multi-command set verbatim."""

    def test_multi_command_payload_has_command_set_key(self):
        from modules.tools.bash_validator import _build_sealed_payload

        cset = [
            {"command": "git add -A", "rationale": "stage"},
            {"command": "git push origin main", "rationale": "publish"},
        ]
        payload = _build_sealed_payload(
            command="git add -A",
            verb="push",
            category="MUTATIVE",
            agent_type="developer",
            command_set=cset,
        )
        assert payload["command_set"] == cset
        assert payload["commands"] == ["git add -A", "git push origin main"]

    def test_single_command_payload_omits_command_set_key(self):
        from modules.tools.bash_validator import _build_sealed_payload

        payload = _build_sealed_payload(
            command="git push origin main",
            verb="push",
            category="MUTATIVE",
            agent_type="developer",
        )
        assert "command_set" not in payload
        assert payload["commands"] == ["git push origin main"]

    def test_single_item_command_set_is_not_multi(self):
        from modules.tools.bash_validator import _build_sealed_payload

        payload = _build_sealed_payload(
            command="terraform apply",
            verb="apply",
            category="MUTATIVE",
            command_set=[{"command": "terraform apply", "rationale": "one"}],
        )
        # A set of length 1 is not a batch -- no command_set key.
        assert "command_set" not in payload
        assert payload["commands"] == ["terraform apply"]


# ---------------------------------------------------------------------------
# Test 6: INTAKE side -- plan-first COMMAND_SET envelope -> ONE pending row
#
# Closes the orphaned-intake gap. The CHECK side (bash_validator) and the
# ACTIVATION side (activate_db_pending_by_prefix Step 3b) were already wired,
# but NO production caller minted a pending COMMAND_SET from a subagent's
# contract. handoff_persister.persist_handoff is that caller: when a subagent
# emits an APPROVAL_REQUEST whose approval_request carries a command_set of
# >= 2 {command, rationale} items and NO approval_id (plan-first), persist_handoff
# writes exactly ONE pending approval whose payload_json contains command_set.
#
# These tests drive the REAL processor (persist_handoff), NOT a hand-rolled
# insert_requested call -- that was the prior demo's mistake.
# ---------------------------------------------------------------------------

class TestIntakeCommandSetPending:
    """persist_handoff is the production INTAKE caller for plan-first COMMAND_SET."""

    @staticmethod
    def _plan_first_contract(command_set: list[dict]) -> dict:
        """A subagent agent_contract_handoff envelope declaring a batch up-front.

        Plan-first: plan_status APPROVAL_REQUEST, approval_request carries the
        command_set and NO approval_id (nothing was attempted/blocked yet).
        """
        return {
            "agent_status": {
                "plan_status": "APPROVAL_REQUEST",
                "agent_id": "developer",
                "pending_steps": [],
                "next_action": "await batch consent",
            },
            "evidence_report": {
                "patterns_checked": [], "files_checked": [], "commands_run": [],
                "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
                "open_gaps": [],
            },
            "consolidation_report": None,
            "approval_request": {
                "operation": "Release batch: stage, commit, push",
                "risk_level": "medium",
                "rationale": "Three related git mutations under one consent.",
                "verification": "git log origin/main shows the release commit",
                "command_set": command_set,
                # NOTE: no approval_id -- plan-first.
            },
        }

    def test_plan_first_command_set_creates_one_pending_command_set(self, db_and_store):
        """A plan-first envelope with N>=2 *mutative* command_set items, run through
        the REAL processor (persist_handoff), produces EXACTLY ONE pending approval
        whose payload_json carries the full command_set.

        All commands here are genuinely mutative/T3 (every one will reach the
        bash_validator COMMAND_SET matcher and consume its index), so none are
        dropped by the Thread-a mutative filter and the batch is minted intact.
        """
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "git push origin main", "rationale": "publish the branch"},
            {"command": "git push origin v1.2.0", "rationale": "publish the tag"},
            {"command": "terraform apply -auto-approve", "rationale": "apply infra"},
        ]
        contract = self._plan_first_contract(command_set)

        # Drive the REAL production processor -- NOT a hand-rolled insert_requested.
        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        # EXACTLY ONE pending row, scope COMMAND_SET implied by payload command_set.
        pending = store.get_pending(all_sessions=True, con=assert_con)
        assert len(pending) == 1, f"Expected exactly 1 pending, got {len(pending)}"
        row = pending[0]
        assert row["id"].startswith("P-"), f"approval_id must be P-prefixed: {row['id']}"
        assert row["status"] == "pending"

        payload = json.loads(row["payload_json"])
        assert "command_set" in payload, "payload must carry command_set (the activation signal)"
        assert [it["command"] for it in payload["command_set"]] == [
            c["command"] for c in command_set
        ]
        assert payload["commands"] == [c["command"] for c in command_set]

    def test_plan_first_command_set_drops_non_mutative_items(self, db_and_store):
        """THREAD (a): a plan-first batch mixing mutative + non-mutative commands
        mints a COMMAND_SET containing ONLY the mutative commands.

        This is the live bug: a non-mutative command (e.g. ``touch``) never reaches
        the bash_validator COMMAND_SET matcher (the match path is gated on
        ``detect_mutative_command(...).is_mutative``), so its index can never be
        consumed and the grant is pinned at PENDING forever. The intake now filters
        with the exact same predicate, so only the mutative commands enter the grant
        -- and the grant can actually reach CONSUMED.
        """
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "rm -rf /tmp/stale-build", "rationale": "clean (mutative)"},
            {"command": "touch /tmp/build/marker", "rationale": "marker (NON-mutative)"},
            {"command": "terraform apply -auto-approve", "rationale": "apply (mutative)"},
        ]
        contract = self._plan_first_contract(command_set)

        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        pending = store.get_pending(all_sessions=True, con=assert_con)
        assert len(pending) == 1, f"Expected exactly 1 pending, got {len(pending)}"
        payload = json.loads(pending[0]["payload_json"])
        minted = [it["command"] for it in payload["command_set"]]
        # touch is dropped; only the two mutative commands remain.
        assert minted == ["rm -rf /tmp/stale-build", "terraform apply -auto-approve"], (
            f"non-mutative 'touch' must be filtered out, got {minted}"
        )
        assert payload["commands"] == minted, "commands listing must match the filtered set"

    def test_plan_first_command_set_with_one_mutative_does_not_mint(self, db_and_store):
        """THREAD (a): if only ONE command survives the mutative filter, it is not a
        batch -- no COMMAND_SET is minted (the lone mutative command falls to the
        singular hook-block path; non-mutative commands need no approval at all)."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "ls -la /tmp", "rationale": "list (NON-mutative)"},
            {"command": "cat /etc/hosts", "rationale": "read (NON-mutative)"},
            {"command": "terraform apply -auto-approve", "rationale": "apply (the only mutative)"},
        ]
        contract = self._plan_first_contract(command_set)

        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        pending = store.get_pending(all_sessions=True, con=assert_con)
        cs_pending = [
            p for p in pending
            if "command_set" in json.loads(p["payload_json"] or "{}")
        ]
        assert cs_pending == [], (
            "a single surviving mutative command must not mint a COMMAND_SET"
        )

    def test_plan_first_command_set_with_zero_mutative_does_not_mint(self, db_and_store):
        """THREAD (a): if NO command survives the mutative filter, there is nothing
        to approve -- no pending is created at all."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "ls -la /tmp", "rationale": "list"},
            {"command": "cat /etc/hosts", "rationale": "read"},
            {"command": "touch /tmp/marker", "rationale": "marker"},
        ]
        contract = self._plan_first_contract(command_set)

        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        pending = store.get_pending(all_sessions=True, con=assert_con)
        cs_pending = [
            p for p in pending
            if "command_set" in json.loads(p["payload_json"] or "{}")
        ]
        assert cs_pending == [], "an all-non-mutative batch must not mint any pending"

    def test_filtered_command_set_is_fully_consumable_to_consumed(self, db_and_store):
        """THREAD (a) end-to-end: the COMMAND_SET minted from a mixed batch (after the
        non-mutative item is dropped) activates into a grant whose every remaining
        index is consumable by bash_validator's matcher -- so the grant actually
        REACHES status=CONSUMED instead of being pinned at PENDING forever.

        This is the regression the live bug never let happen: with the unfiltered
        set, the non-mutative index was never consumed and CONSUMED was unreachable.
        """
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "git push origin main", "rationale": "publish (mutative)"},
            {"command": "touch /tmp/release-marker", "rationale": "marker (NON-mutative, dropped)"},
            {"command": "git push origin v1.2.0", "rationale": "publish tag (mutative)"},
        ]
        contract = self._plan_first_contract(command_set)

        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        pending = store.get_pending(all_sessions=True, con=assert_con)
        assert len(pending) == 1
        approval_id = pending[0]["id"]
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            match_command_set_grant,
            ACTIVATION_ACTIVATED,
        )
        from gaia.store.writer import (
            list_approval_grants,
            mark_command_set_item_consumed,
        )

        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success, f"activation must succeed: {result.reason}"
        assert result.status == ACTIVATION_ACTIVATED

        # The grant holds exactly the two mutative commands (touch was dropped).
        grants = list_approval_grants(session_id=session_id, status="PENDING")
        cs = [g for g in grants if g.get("scope") == "COMMAND_SET"]
        assert len(cs) == 1
        assert json.loads(cs[0]["command_set_json"]) and [
            it["command"] for it in json.loads(cs[0]["command_set_json"])
        ] == ["git push origin main", "git push origin v1.2.0"]

        # Consume BOTH items -> the grant reaches CONSUMED (not stuck PENDING).
        m0 = match_command_set_grant("git push origin main")
        assert m0 == (approval_id, 0)
        r0 = mark_command_set_item_consumed(*m0)
        assert r0["all_consumed"] is False
        m1 = match_command_set_grant("git push origin v1.2.0")
        assert m1 == (approval_id, 1)
        r1 = mark_command_set_item_consumed(*m1)
        assert r1["all_consumed"] is True, "every mutative index consumed -> CONSUMED"

        consumed_grants = list_approval_grants(session_id=session_id, status="CONSUMED")
        assert any(g["approval_id"] == approval_id for g in consumed_grants), (
            "the grant must reach status=CONSUMED once its mutative indexes are consumed"
        )

    def test_intake_pending_activates_into_command_set_grant(self, db_and_store):
        """The pending minted by the INTAKE processor, when approved (activation),
        drives Step 3b -> create_command_set_grant (TTL 60), and the grant is
        consumed item-by-item with replay protection until CONSUMED. This proves
        the intake's payload shape matches what activation/consume expect."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        command_set = [
            {"command": "git push origin main", "rationale": "publish"},
            {"command": "git push origin tags", "rationale": "tags"},
        ]
        contract = self._plan_first_contract(command_set)

        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        pending = store.get_pending(all_sessions=True, con=assert_con)
        assert len(pending) == 1
        approval_id = pending[0]["id"]
        nonce_prefix = approval_id[len("P-"):len("P-") + 8]

        from modules.security.approval_grants import (
            activate_db_pending_by_prefix,
            match_command_set_grant,
            ACTIVATION_ACTIVATED,
            DEFAULT_COMMAND_SET_TTL_MINUTES,
        )
        from gaia.store.writer import (
            list_approval_grants,
            mark_command_set_item_consumed,
        )
        from datetime import datetime, timezone

        # Activation: Step 3b must fire (COMMAND_SET grant, not singular).
        before = datetime.now(timezone.utc)
        result = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert result.success, f"activation must succeed: {result.reason}"
        assert result.status == ACTIVATION_ACTIVATED
        assert result.grant_path is None  # COMMAND_SET is a pure DB grant
        assert "COMMAND_SET" in result.reason

        grants = list_approval_grants(session_id=session_id, status="PENDING")
        cs = [g for g in grants if g.get("scope") == "COMMAND_SET"]
        assert len(cs) == 1, f"Expected 1 COMMAND_SET grant, got {len(cs)}"
        assert cs[0]["approval_id"] == approval_id
        assert json.loads(cs[0]["command_set_json"])
        # TTL 60 minutes.
        assert DEFAULT_COMMAND_SET_TTL_MINUTES == 60
        expires_at = datetime.strptime(
            cs[0]["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        ttl = (expires_at - before).total_seconds() / 60
        assert 59 <= ttl <= 61, f"TTL must be ~60 min, got {ttl:.2f}"

        # Consume item-by-item with replay protection -> CONSUMED.
        m0 = match_command_set_grant("git push origin main")
        assert m0 == (approval_id, 0)
        mark_command_set_item_consumed(*m0)
        assert match_command_set_grant("git push origin main") is None
        m1 = match_command_set_grant("git push origin tags")
        assert m1 == (approval_id, 1)
        mark_command_set_item_consumed(*m1)
        assert match_command_set_grant("git push origin tags") is None

    def test_single_item_command_set_does_not_intake_command_set(self, db_and_store):
        """A plan-first envelope whose command_set has exactly 1 item is NOT a
        batch: persist_handoff must NOT mint a COMMAND_SET pending (no degrade
        in reverse). With no approval_id and a single item, no pending is created."""
        db_path, assert_con, store = db_and_store
        session_id = "test-bridge-session"
        contract = self._plan_first_contract(
            [{"command": "terraform apply", "rationale": "single"}]
        )

        from modules.agents.handoff_persister import persist_handoff

        persist_handoff(
            parsed_contract=contract,
            agent_output="",
            task_info={"agent_id": "developer", "db_path": str(db_path)},
            session_id=session_id,
        )

        pending = store.get_pending(all_sessions=True, con=assert_con)
        # No approval_id and a single-item set -> the intake bridge declines; the
        # singular approval_id path has nothing to act on either, so no pending.
        cs_pending = [
            p for p in pending
            if "command_set" in json.loads(p["payload_json"] or "{}")
        ]
        assert cs_pending == [], "A single-item command_set must not mint a COMMAND_SET pending"
