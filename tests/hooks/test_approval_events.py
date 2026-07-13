"""Tests for approval_events T3 intercept chain insertion and chain-walk.

These tests verify:
  1. A REQUESTED event is inserted with the correct fingerprint and chain linkage
     via store.insert_requested() -- the canonical API introduced in T2.1.
  2. The chain-walk validator passes for a clean multi-event chain.
  3. store.insert_requested() generates a P-prefixed approval_id.
  4. Cross-session pending visibility works via store.get_pending().
  5. Replay via store.replay_for_approval() returns events in insertion order.
  6. State transition via store.transition() enforces from_status guard.

Satisfies: AC-2, AC-6 from brief approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Tasks:     T1.1, T1.3 (M1, Wave 1), T2.1 (M2, Wave 2)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from gaia.approvals.chain import (  # noqa: E402
    ChainTamperError,
    validate_chain,
    insert_event,
    fingerprint_payload,
    canonical_payload,
)
from gaia.approvals.store import (  # noqa: E402
    insert_requested,
    record_event,
    get_pending,
    transition,
    replay_for_approval,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_db() -> sqlite3.Connection:
    """In-memory DB with v12 approval tables and gaia_sha256 registered."""
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")

    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)

    con.executescript("""
        CREATE TABLE approvals (
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

        CREATE TABLE approval_events (
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

        CREATE TRIGGER ai_approval_events_hash
        AFTER INSERT ON approval_events
        BEGIN
            SELECT 1;
        END;

        CREATE TRIGGER bu_approval_events_immutable
        BEFORE UPDATE ON approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END;

        CREATE TRIGGER bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN
            SELECT RAISE(ABORT, 'approval_events is append-only');
        END;
    """)
    return con


# ---------------------------------------------------------------------------
# Tests -- T2.1 AC: test_t3_intercept_inserts_requested_with_chain
# ---------------------------------------------------------------------------

class TestT3InterceptInsertsRequestedWithChain:
    """T3 intercept inserts a REQUESTED event with correct fingerprint and chain.

    All tests in this class use store.insert_requested() as the canonical API.
    The former _simulate_t3_intercept helper is replaced by the store module
    introduced in T2.1.
    """

    def test_t3_intercept_inserts_requested_with_chain(self):
        """REQUESTED event inserted with correct fingerprint, prev_hash, this_hash.

        This is the primary AC test for T2.1.
        """
        con = _make_v12_db()

        sealed_payload = {
            "operation": "Delete branch feature/old",
            "exact_content": "git branch -D feature/old",
            "scope": "feature/old",
            "risk_level": "medium",
            "rollback_hint": "git branch feature/old <sha>",
            "rationale": "Branch is stale and merged",
            "commands": ["git branch -D feature/old"],
        }

        approval_id = insert_requested(
            sealed_payload,
            agent_id="agent-test",
            session_id="session-test",
            con=con,
        )
        con.commit()

        # approval_id must have the P- prefix.
        assert approval_id.startswith("P-"), (
            f"approval_id must start with 'P-', got: {approval_id!r}"
        )

        # Verify approvals row.
        ap_row = con.execute(
            "SELECT id, status, fingerprint FROM approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        assert ap_row is not None, "approvals row must be inserted"
        assert ap_row[1] == "pending"
        expected_fp = fingerprint_payload(sealed_payload)
        assert ap_row[2] == expected_fp

        # Verify approval_events row.
        ev_row = con.execute(
            "SELECT event_type, fingerprint, prev_hash, this_hash "
            "FROM approval_events WHERE approval_id = ? ORDER BY id ASC LIMIT 1",
            (approval_id,),
        ).fetchone()
        assert ev_row is not None, "approval_events REQUESTED row must be inserted"
        assert ev_row[0] == "REQUESTED"
        assert ev_row[1] == expected_fp
        # Genesis row: prev_hash is NULL.
        assert ev_row[2] is None
        # this_hash = SHA-256('' || fingerprint)
        expected_this_hash = _sha256("" + expected_fp)
        assert ev_row[3] == expected_this_hash, (
            f"this_hash mismatch: stored={ev_row[3]!r}, expected={expected_this_hash!r}"
        )

    def test_t3_intercept_fingerprint_is_canonical(self):
        """Fingerprint is based on canonical JSON (sorted keys, no whitespace)."""
        # Two dicts with same content but different key order.
        payload_a = {"b": 2, "a": 1}
        payload_b = {"a": 1, "b": 2}

        fp_a = fingerprint_payload(payload_a)
        fp_b = fingerprint_payload(payload_b)

        # Canonical serialization sorts keys -> same fingerprint.
        assert fp_a == fp_b, "Fingerprint must be independent of key insertion order"

    def test_chain_walk_validates_clean_chain(self):
        """A multi-event approval chain passes validate_chain after T3 intercept."""
        con = _make_v12_db()

        sealed_payload = {
            "operation": "Create file /tmp/test.txt",
            "exact_content": "touch /tmp/test.txt",
            "scope": "/tmp/test.txt",
            "risk_level": "low",
            "rollback_hint": "rm /tmp/test.txt",
            "rationale": "Required for test",
            "commands": ["touch /tmp/test.txt"],
        }

        approval_id = insert_requested(
            sealed_payload,
            agent_id="agent-chain",
            session_id="session-chain",
            con=con,
        )
        con.commit()

        # Add a second event (APPROVED) via record_event().
        record_event(
            approval_id,
            "APPROVED",
            agent_id="user",
            session_id="session-chain",
            con=con,
        )
        con.commit()

        # Chain walk must pass.
        result = validate_chain(approval_id, con)
        assert result is True

    def test_fingerprint_deterministic_across_calls(self):
        """Same payload always produces the same fingerprint."""
        payload = {
            "operation": "Deploy",
            "exact_content": "kubectl apply -f manifest.yaml",
            "scope": "production",
            "risk_level": "high",
            "rollback_hint": None,
            "rationale": "Scheduled deploy",
            "commands": ["kubectl apply -f manifest.yaml"],
        }
        fp1 = fingerprint_payload(payload)
        fp2 = fingerprint_payload(payload)
        assert fp1 == fp2

    def test_genesis_approval_id_has_p_prefix(self):
        """insert_requested() returns a P-prefixed id and is fingerprint-idempotent.

        Brief 71, Change 2b: an identical sealed_payload while a pending approval
        already exists must REUSE that approval id (fingerprint idempotency), so a
        cross-session retry of the same blocked command does not mint a fresh P-
        on every pass. A genuinely different payload still mints a new id.
        """
        con = _make_v12_db()
        payload = {"operation": "test", "commands": ["echo hi"]}
        approval_id = insert_requested(
            payload, agent_id="a1", session_id="s1", con=con
        )
        assert approval_id.startswith("P-")

        # Same payload again -> SAME id (idempotent), no fresh mint.
        approval_id_same = insert_requested(
            payload, agent_id="a1", session_id="s1", con=con
        )
        assert approval_id_same == approval_id

        # A different payload -> a new id.
        other_payload = {"operation": "test", "commands": ["echo bye"]}
        approval_id_other = insert_requested(
            other_payload, agent_id="a1", session_id="s1", con=con
        )
        assert approval_id_other.startswith("P-")
        assert approval_id_other != approval_id

    def test_cross_session_pending_visibility(self):
        """get_pending(all_sessions=True) returns approvals from different sessions.

        Each session creates a DISTINCT approval (distinct payloads) -- with
        fingerprint idempotency (Change 2b), two IDENTICAL payloads would instead
        collapse to one id, so distinct payloads are required to exercise the
        cross-session visibility mechanic.
        """
        con = _make_v12_db()
        payload_a = {"operation": "op", "commands": ["rm /tmp/a"]}
        payload_b = {"operation": "op", "commands": ["rm /tmp/b"]}

        # Session A creates an approval.
        id_a = insert_requested(payload_a, agent_id="ag", session_id="session-A", con=con)
        # Session B creates another (distinct) approval.
        id_b = insert_requested(payload_b, agent_id="ag", session_id="session-B", con=con)
        assert id_a != id_b

        # Single-session view: each session only sees its own.
        pending_a = get_pending(session_id="session-A", all_sessions=False, con=con)
        pending_b = get_pending(session_id="session-B", all_sessions=False, con=con)
        assert len(pending_a) == 1 and pending_a[0]["id"] == id_a
        assert len(pending_b) == 1 and pending_b[0]["id"] == id_b

        # Cross-session view: both approvals visible.
        pending_all = get_pending(all_sessions=True, con=con)
        ids_all = {r["id"] for r in pending_all}
        assert id_a in ids_all
        assert id_b in ids_all

    def test_replay_for_approval_is_deterministic(self):
        """replay_for_approval() returns events in insertion order."""
        con = _make_v12_db()
        payload = {"operation": "deploy", "commands": ["kubectl apply -f app.yaml"]}

        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        record_event(approval_id, "SHOWN", agent_id="orch", session_id="s", con=con)
        record_event(approval_id, "APPROVED", agent_id="user", session_id="s", con=con)
        record_event(approval_id, "EXECUTED", agent_id="ag", session_id="s", con=con)

        events = replay_for_approval(approval_id, con=con)
        types = [e["event_type"] for e in events]
        assert types == ["REQUESTED", "SHOWN", "APPROVED", "EXECUTED"]

    def test_transition_guard_raises_on_wrong_status(self):
        """transition() raises ValueError when from_status does not match actual status."""
        con = _make_v12_db()
        payload = {"operation": "cleanup", "commands": ["rm /tmp/log"]}

        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        # approval is 'pending'. Trying to transition from 'approved' must raise.
        with pytest.raises(ValueError, match="expected status"):
            transition(
                approval_id, "approved", "rejected",
                agent_id="user", session_id="s", con=con
            )

    def test_transition_valid_pending_to_approved(self):
        """transition() from 'pending' to 'approved' updates status and inserts event."""
        con = _make_v12_db()
        payload = {"operation": "cleanup", "commands": ["rm /tmp/log"]}

        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        transition(
            approval_id, "pending", "approved",
            agent_id="user", session_id="s", con=con
        )

        # Status updated.
        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "approved"

        # APPROVED event appended to chain.
        events = replay_for_approval(approval_id, con=con)
        types = [e["event_type"] for e in events]
        assert "APPROVED" in types

        # Chain must still be valid after the transition event.
        assert validate_chain(approval_id, con) is True


class TestExecutedFailedAuditCycle:
    """EXECUTED / FAILED audit events appended after an approved T3 command runs.

    Mirrors the PostToolUse adapter path (_record_t3_outcome_event): once a T3
    command runs under a consumed grant, the adapter appends EXECUTED (clean
    exit) or FAILED (non-zero exit) for that approval via store.record_event(),
    continuing the hash chain. These tests exercise the store-level contract the
    adapter relies on.

    Satisfies: AC1, AC2, AC3 (close the audit-log cycle, Tier 1).
    """

    @staticmethod
    def _payload_json(command: str, exit_code: int) -> str:
        return canonical_payload(
            {
                "command": command,
                "exit_code": exit_code,
                "outcome": "success" if exit_code == 0 else "failure",
            }
        )

    def test_executed_event_links_chain(self):
        """AC1: a successful approved T3 command appends EXECUTED with valid linkage."""
        con = _make_v12_db()
        payload = {"operation": "deploy", "commands": ["kubectl apply -f app.yaml"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)

        # Adapter would call record_event(EXECUTED) on a clean exit.
        record_event(
            approval_id,
            "EXECUTED",
            session_id="s",
            payload_json=self._payload_json("kubectl apply -f app.yaml", 0),
            metadata_json=json.dumps({"source": "post_tool_use"}),
            con=con,
        )
        con.commit()

        events = replay_for_approval(approval_id, con=con)
        assert events[-1]["event_type"] == "EXECUTED"
        # prev_hash of EXECUTED must equal this_hash of the prior (APPROVED) row.
        assert events[-1]["prev_hash"] == events[-2]["this_hash"]
        # Chain walk passes end to end.
        assert validate_chain(approval_id, con) is True

    def test_failed_event_links_chain(self):
        """AC2: a failed approved T3 command appends FAILED with valid linkage."""
        con = _make_v12_db()
        payload = {"operation": "deploy", "commands": ["kubectl apply -f bad.yaml"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)

        # Adapter would call record_event(FAILED) on a non-zero exit.
        record_event(
            approval_id,
            "FAILED",
            session_id="s",
            payload_json=self._payload_json("kubectl apply -f bad.yaml", 1),
            metadata_json=json.dumps({"source": "post_tool_use"}),
            con=con,
        )
        con.commit()

        events = replay_for_approval(approval_id, con=con)
        assert events[-1]["event_type"] == "FAILED"
        assert events[-1]["prev_hash"] == events[-2]["this_hash"]
        assert validate_chain(approval_id, con) is True

    def test_full_requested_to_executed_chain_validates(self):
        """AC3: REQUESTED -> SHOWN -> APPROVED -> EXECUTED validates end to end."""
        con = _make_v12_db()
        payload = {"operation": "push", "commands": ["git push origin main"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        record_event(approval_id, "SHOWN", agent_id="orch", session_id="s", con=con)
        record_event(approval_id, "APPROVED", agent_id="user", session_id="s", con=con)
        record_event(
            approval_id,
            "EXECUTED",
            session_id="s",
            payload_json=self._payload_json("git push origin main", 0),
            con=con,
        )
        con.commit()

        events = replay_for_approval(approval_id, con=con)
        assert [e["event_type"] for e in events] == [
            "REQUESTED", "SHOWN", "APPROVED", "EXECUTED",
        ]
        assert validate_chain(approval_id, con) is True

    def test_executed_payload_recoverable_by_replay(self):
        """AC5 corollary: replay reads the EXECUTED payload that the adapter stored."""
        con = _make_v12_db()
        payload = {"operation": "deploy", "commands": ["helm upgrade app ."]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)
        record_event(
            approval_id,
            "EXECUTED",
            session_id="s",
            payload_json=self._payload_json("helm upgrade app .", 0),
            con=con,
        )
        con.commit()

        executed = [
            e for e in replay_for_approval(approval_id, con=con)
            if e["event_type"] == "EXECUTED"
        ]
        assert len(executed) == 1
        stored = json.loads(executed[0]["payload_json"])
        assert stored["command"] == "helm upgrade app ."
        assert stored["exit_code"] == 0
        assert stored["outcome"] == "success"


def _apply_v12_schema_to_file(db_path) -> None:
    """Apply the v12 approval schema to a file-backed SQLite DB.

    A file DB (not :memory:) is required because store._open_db() opens a fresh
    connection per call and commits+closes owned connections -- an in-memory DB
    would be discarded between the seed insert and the adapter's own connection.
    Mirrors _make_v12_db() above, minus the in-memory return.
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
        CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
        BEFORE UPDATE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
        BEFORE DELETE ON approval_events
        BEGIN SELECT RAISE(ABORT, 'approval_events is append-only'); END;
    """)
    con.commit()
    con.close()


class TestPostToolUseDiscriminatorRecordsFailed:
    """Drive the real discriminator: parse_post_tool_use -> _record_t3_outcome_event.

    AC2 above (test_failed_event_links_chain) calls store.record_event('FAILED')
    DIRECTLY, which bypasses the adapter code that actually decides EXECUTED vs
    FAILED from the Claude Code payload. That bypass is why the production bug
    survived: the Bash tool_response carries no 'exit_code' and no 'output', and
    a runtime FAILURE arrives as a bare STRING -- so the old parser defaulted
    success=True and every failure was mis-recorded as EXECUTED (261/0 split).

    These tests exercise the genuine path an approved T3 command takes at
    PostToolUse -- adapter.parse_post_tool_use() to derive success, then
    adapter._record_t3_outcome_event() to write the event -- and assert that a
    realistic failed Bash payload lands a FAILED event, and a success payload
    lands EXECUTED.
    """

    @pytest.fixture()
    def store_on_file_db(self, tmp_path, monkeypatch):
        """File-backed v12 DB wired into gaia.approvals.store._open_db.

        Yields (approval_id, assert_con) for an already-approved T3 approval, so
        the FK to approvals(id) is satisfied when the adapter appends its event.
        """
        import gaia.approvals.store as astore

        db_path = tmp_path / "discriminator_test.db"
        _apply_v12_schema_to_file(db_path)

        def _open():
            con = sqlite3.connect(str(db_path))
            con.execute("PRAGMA foreign_keys = ON")
            con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
            return con

        monkeypatch.setattr("gaia.approvals.store._open_db", _open)

        # Seed an approved approval so record_event's FK is satisfied.
        seed = _open()
        payload = {"operation": "deploy", "commands": ["kubectl apply -f app.yaml"]}
        approval_id = astore.insert_requested(
            payload, agent_id="ag", session_id="s", con=seed
        )
        seed.commit()
        astore.transition(
            approval_id, "pending", "approved",
            agent_id="user", session_id="s", con=seed,
        )
        seed.commit()
        seed.close()

        assert_con = _open()
        yield approval_id, assert_con
        assert_con.close()

    @staticmethod
    def _adapter():
        from adapters.claude_code import ClaudeCodeAdapter
        return ClaudeCodeAdapter()

    def _drive(self, adapter, approval_id, payload):
        """Reproduce the adapter's own discriminator wiring (claude_code.py).

        parse_post_tool_use -> success = exit_code == 0 -> _record_t3_outcome_event,
        matching adapt_post_tool_use lines 1468 and 1468-1474.
        """
        tr = adapter.parse_post_tool_use(payload)
        success = tr.exit_code == 0
        adapter._record_t3_outcome_event(
            approval_id,
            command=tr.command,
            success=success,
            exit_code=tr.exit_code,
            session_id=tr.session_id,
        )
        return tr

    def test_failed_bash_string_response_records_failed(self, store_on_file_db):
        """A realistic failed Bash payload (bare-string tool_response) -> FAILED.

        This is the exact scenario the pre-fix code mis-recorded as EXECUTED.
        """
        approval_id, con = store_on_file_db
        adapter = self._adapter()

        failed_payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl apply -f app.yaml"},
            # Real harness failure form: tool_response is a bare error string.
            "tool_response": "Error from server (Invalid): error validating data: "
                             "ValidationError(Deployment.spec): unknown field",
            "session_id": "s",
        }
        tr = self._drive(adapter, approval_id, failed_payload)
        assert tr.exit_code == 1, "failed Bash must derive a non-zero exit_code"

        events = replay_for_approval(approval_id, con=con)
        assert events[-1]["event_type"] == "FAILED", (
            f"expected a FAILED event to close the cycle, got: "
            f"{[e['event_type'] for e in events]}"
        )
        stored = json.loads(events[-1]["payload_json"])
        assert stored["outcome"] == "failure"
        # Chain stays intact end to end.
        assert validate_chain(approval_id, con) is True

    def test_interrupted_bash_records_failed(self, store_on_file_db):
        """A dict tool_response with interrupted=True -> FAILED."""
        approval_id, con = store_on_file_db
        adapter = self._adapter()
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl apply -f app.yaml"},
            "tool_response": {"stdout": "", "stderr": "", "interrupted": True,
                              "isImage": False, "noOutputExpected": False},
            "session_id": "s",
        }
        self._drive(adapter, approval_id, payload)
        events = replay_for_approval(approval_id, con=con)
        assert events[-1]["event_type"] == "FAILED"

    def test_successful_bash_records_executed(self, store_on_file_db):
        """A realistic success dict (stdout, no failure flags) -> EXECUTED.

        Guards against the fix over-correcting: benign success must still be
        EXECUTED, not FAILED.
        """
        approval_id, con = store_on_file_db
        adapter = self._adapter()
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl apply -f app.yaml"},
            "tool_response": {"stdout": "deployment.apps/app configured",
                              "stderr": "", "interrupted": False,
                              "isImage": False, "noOutputExpected": False},
            "session_id": "s",
        }
        tr = self._drive(adapter, approval_id, payload)
        assert tr.exit_code == 0
        events = replay_for_approval(approval_id, con=con)
        assert events[-1]["event_type"] == "EXECUTED"
