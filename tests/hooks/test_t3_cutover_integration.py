"""Integration smoke test: T3 intercept -> DB REQUESTED row -> chain valid.

Locks the D16 invariant at the integration level: when validate_bash_command
intercepts a T3 subagent command it calls store.insert_requested(), which
inserts into approvals + approval_events with a correct hash chain.

This test bridges the gap between:
  - tests/hooks/test_approval_events.py  (unit-tests for the store layer)
  - tests/hooks/modules/security/test_bash_validator.py  (unit-tests for the
    validator, which stubs out the store calls)

By calling validate_bash_command with a patched store._open_db it exercises
both layers end-to-end inside a single test: the validator produces a deny
with a P-{hex} approval_id AND the store row for that id is present with a
valid chain.

Plan T-2.1 / Brief 71 / M2 Wave 2 cutover smoke.
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
# Path setup
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Local DB helper (mirrors _make_v12_db in test_approval_events.py)
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_file_db(db_path: Path) -> None:
    """Apply v12 approval schema to a file-backed SQLite DB.

    A file DB (not :memory:) is required because store._open_db() calls
    commit+close on owned connections, destroying in-memory databases.
    Writing to a file lets subsequent connections see committed rows.
    """
    con = sqlite3.connect(str(db_path))
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
    con.commit()
    con.close()


@pytest.fixture()
def v12_file_db(tmp_path):
    """File-backed v12 approval DB.

    Yields (db_path, assert_connection) where assert_connection is a persistent
    connection for assertion queries (not closed by store internals).
    """
    db_path = tmp_path / "t3_integration_test.db"
    _make_v12_file_db(db_path)
    assert_con = sqlite3.connect(str(db_path))
    assert_con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    yield db_path, assert_con
    assert_con.close()


# ---------------------------------------------------------------------------
# Integration smoke test
# ---------------------------------------------------------------------------

class TestT3InterceptWritesToDB:
    """Locks the D16 invariant end-to-end: validate_bash_command -> store -> chain."""

    def test_t3_intercept_writes_to_db(self, v12_file_db, monkeypatch):
        """T3 subagent intercept inserts a REQUESTED row with a valid hash chain.

        Given: in-memory v12 DB patched into store._open_db
        When:  validate_bash_command("git push origin main", is_subagent=True, ...)
        Then:
          - response is deny (allowed=False, permissionDecision=="deny")
          - denial message contains "Load Skill('subagent-request-approval')"
            OR contains the standard T3_BLOCKED approval_id pattern
          - denial message contains a P-<hex> approval_id
          - approvals table has a 'pending' row with that approval_id
          - approval_events has a REQUESTED row with that approval_id
          - validate_chain(approval_id, con) == True  (D16 invariant)
        """
        import gaia.approvals.store as astore
        from gaia.approvals.chain import validate_chain
        from modules.tools.bash_validator import validate_bash_command

        db_path, assert_con = v12_file_db

        # Patch store._open_db: each call opens a fresh connection to the same
        # file.  Commits from insert_requested() are durable to the file.
        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        # Also patch get_pending used by _find_pending_in_db (retry check).
        _orig_get_pending = astore.get_pending

        def _patched_get_pending(session_id=None, all_sessions=False, con=None):
            if con is None:
                con = sqlite3.connect(str(db_path))
            return _orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

        monkeypatch.setattr("gaia.approvals.store.get_pending", _patched_get_pending)

        session_id = "integration-test-session"
        command = "git push origin main"

        # --- WHEN ---
        result = validate_bash_command(command, is_subagent=True, session_id=session_id)

        # --- THEN: response is deny ---
        assert not result.allowed, "T3 subagent command must be blocked"
        assert result.block_response is not None, "Must have structured block_response"

        hook_output = result.block_response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "deny", (
            f"Expected deny, got: {hook_output.get('permissionDecision')}"
        )

        # --- THEN: denial message contains P-<hex> approval_id ---
        reason = hook_output.get("permissionDecisionReason", "")
        p_match = re.search(r"approval_id:\s*(P-[0-9a-f]+)", reason)
        assert p_match, (
            f"Denial reason must contain 'approval_id: P-<hex>', got:\n{reason}"
        )
        approval_id = p_match.group(1)
        assert approval_id.startswith("P-"), f"approval_id must start with P-, got: {approval_id}"

        # --- THEN: denial message contains the APPROVAL_REQUEST skill instruction ---
        assert "APPROVAL_REQUEST" in reason or "subagent-request-approval" in reason, (
            f"Denial reason must reference APPROVAL_REQUEST or subagent-request-approval, got:\n{reason}"
        )

        # --- THEN: approvals row is 'pending' in the DB ---
        ap_row = assert_con.execute(
            "SELECT id, status, session_id FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        assert ap_row is not None, (
            f"approvals row must exist for approval_id={approval_id}"
        )
        assert ap_row[1] == "pending", f"Expected status='pending', got: {ap_row[1]}"
        assert ap_row[2] == session_id, (
            f"Expected session_id={session_id!r}, got: {ap_row[2]!r}"
        )

        # --- THEN: approval_events has a REQUESTED row ---
        ev_row = assert_con.execute(
            "SELECT event_type, fingerprint, prev_hash, this_hash "
            "FROM approval_events WHERE approval_id = ? ORDER BY id ASC LIMIT 1",
            (approval_id,),
        ).fetchone()
        assert ev_row is not None, (
            f"approval_events REQUESTED row must exist for approval_id={approval_id}"
        )
        assert ev_row[0] == "REQUESTED", f"Expected REQUESTED event, got: {ev_row[0]}"

        # Genesis row: prev_hash must be NULL.
        assert ev_row[2] is None, f"Genesis prev_hash must be NULL, got: {ev_row[2]}"

        # this_hash must be SHA-256('' + fingerprint).
        expected_this_hash = _sha256("" + (ev_row[1] or ""))
        assert ev_row[3] == expected_this_hash, (
            f"this_hash mismatch: stored={ev_row[3]!r}, expected={expected_this_hash!r}"
        )

        # --- THEN: validate_chain passes (D16 invariant locked) ---
        chain_valid = validate_chain(approval_id, assert_con)
        assert chain_valid is True, (
            f"validate_chain must return True for approval_id={approval_id}"
        )

        # --- THEN: payload_json captures the correct command ---
        payload_row = assert_con.execute(
            "SELECT payload_json FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        assert payload_row is not None
        payload = json.loads(payload_row[0])
        assert payload.get("exact_content") == command, (
            f"sealed_payload.exact_content must equal the blocked command. "
            f"got: {payload.get('exact_content')!r}"
        )
