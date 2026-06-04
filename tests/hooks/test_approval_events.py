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
