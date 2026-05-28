"""Tests for orchestrator fingerprint validation before inserting a SHOWN event.

These tests verify:
  1. A sealed_payload relayed verbatim by the orchestrator passes
     verify_fingerprint() -- bytes match the REQUESTED row fingerprint.
  2. A tampered sealed_payload raises ChainTamperError before SHOWN is written.

The underlying function `verify_fingerprint(approval_id, payload_json, con)`
lives in gaia.approvals.chain (created in T1.3). This test module is the
acceptance test for T2.4, which proves that the check function exists with
the correct contract and that the orchestrator flow (subagent -> relay ->
validation -> SHOWN) is verified against the hash-chain invariant.

The orchestrator skill `orchestrator-present-approval` (M4 T4.2) will call
verify_fingerprint before emitting the SHOWN event. The invariant is:
    - If verify_fingerprint raises, do NOT insert SHOWN, do NOT present
      the approval to the user.
    - If verify_fingerprint returns True, insert SHOWN and proceed.

Satisfies: AC-5 from brief approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Task:       T2.4 (M2, Wave 2)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: ensure repo root and gaia package are importable.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.approvals.chain import (  # noqa: E402
    ChainTamperError,
    verify_fingerprint,
    canonical_payload,
    fingerprint_payload,
)
from gaia.approvals.store import (  # noqa: E402
    insert_requested,
    record_event,
)


# ---------------------------------------------------------------------------
# Helper: isolated in-memory DB with the v10 schema (same pattern as T1/T2
# tests -- self-contained, no external DB dependency).
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v10_db() -> sqlite3.Connection:
    """Create an isolated in-memory SQLite DB with the approvals + approval_events
    schema and all three triggers. Registers gaia_sha256 scalar function."""
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
            this_hash     TEXT NOT NULL,
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
# Full 7-field sealed_payload fixture (D13 canonical shape).
# ---------------------------------------------------------------------------

_SEALED_PAYLOAD: dict = {
    "operation": "Delete stale branch feature/legacy-auth",
    "exact_content": "git branch -D feature/legacy-auth",
    "scope": "feature/legacy-auth",
    "risk_level": "medium",
    "rollback_hint": "git branch feature/legacy-auth <sha>",
    "rationale": "Branch was merged in PR #441 and is no longer needed",
    "commands": ["git branch -D feature/legacy-auth"],
}


# ---------------------------------------------------------------------------
# T2.4 test 1: verbatim relay matches fingerprint
# ---------------------------------------------------------------------------

class TestVerbatimRelayMatchesFingerprint:
    """Orchestrator relay scenario: subagent emits -> orchestrator relays unchanged.

    The orchestrator receives the sealed_payload from the agent-protocol
    contract, serializes it to canonical JSON, and calls verify_fingerprint
    before emitting the SHOWN event. This test proves that a verbatim relay
    (no field mutations) produces a fingerprint that matches the REQUESTED row.
    """

    def test_payload_bytes_match_between_subagent_and_orchestrator(self):
        """Verbatim relay passes verify_fingerprint -- fingerprints are equal.

        Simulates the end-to-end relay:
          1. Subagent: insert_requested(sealed_payload) -> REQUESTED event
          2. Orchestrator: re-serializes the payload to canonical JSON
          3. Orchestrator: calls verify_fingerprint(approval_id, canonical_json)
          4. Assert: verify_fingerprint returns True without raising
        """
        con = _make_v10_db()

        # Step 1: Subagent emits via insert_requested (T2.1 API).
        approval_id = insert_requested(
            _SEALED_PAYLOAD,
            agent_id="agent-subagent-a1b2",
            session_id="session-subagent-001",
            con=con,
        )
        assert approval_id.startswith("P-"), (
            f"approval_id must have P- prefix, got {approval_id!r}"
        )

        # Step 2: Orchestrator receives the payload object (via agent-protocol
        # agent_contract_handoff APPROVAL_REQUEST field) and re-canonicalizes it.
        # Using canonical_payload() mirrors what the orchestrator-present-approval
        # skill will do: json.dumps(payload, sort_keys=True, separators=(',', ':'))
        relayed_canonical_json = canonical_payload(_SEALED_PAYLOAD)

        # Step 3: Orchestrator calls verify_fingerprint before emitting SHOWN.
        result = verify_fingerprint(approval_id, relayed_canonical_json, con)

        # Step 4: Must return True -- fingerprints match.
        assert result is True, (
            "verify_fingerprint must return True when relayed payload matches "
            "the REQUESTED fingerprint"
        )

    def test_key_order_independence_in_relay(self):
        """Canonical fingerprint is stable regardless of dict key insertion order.

        The orchestrator may receive the payload as a Python dict with keys in
        any order (from JSON deserialization). verify_fingerprint must succeed
        regardless of the key order in the relayed payload_json, because both
        subagent and orchestrator use sort_keys=True in json.dumps.
        """
        con = _make_v10_db()

        # Subagent inserts with standard key order.
        approval_id = insert_requested(
            _SEALED_PAYLOAD,
            agent_id="agent-order-test",
            session_id="session-order-test",
            con=con,
        )

        # Orchestrator receives with reversed key order (simulating different
        # deserialization order from JSON).
        reversed_payload = dict(reversed(list(_SEALED_PAYLOAD.items())))
        relayed_canonical_json = canonical_payload(reversed_payload)

        # Must still pass: canonical serialization normalizes key order.
        result = verify_fingerprint(approval_id, relayed_canonical_json, con)
        assert result is True, (
            "verify_fingerprint must pass even when relayed payload has "
            "different key insertion order than the original"
        )

    def test_shown_event_written_after_successful_verification(self):
        """After verify_fingerprint passes, inserting a SHOWN event is valid.

        This test verifies the complete orchestrator flow: validate -> insert SHOWN.
        After SHOWN is inserted, the chain must still be intact.
        """
        from gaia.approvals.chain import validate_chain

        con = _make_v10_db()

        approval_id = insert_requested(
            _SEALED_PAYLOAD,
            agent_id="agent-flow",
            session_id="session-flow",
            con=con,
        )

        relayed_canonical_json = canonical_payload(_SEALED_PAYLOAD)

        # Verification passes.
        verify_fingerprint(approval_id, relayed_canonical_json, con)

        # Orchestrator writes SHOWN event with the relayed payload and fingerprint.
        fp = fingerprint_payload(_SEALED_PAYLOAD)
        record_event(
            approval_id,
            "SHOWN",
            agent_id="orchestrator",
            session_id="session-flow",
            payload_json=relayed_canonical_json,
            fingerprint=fp,
            con=con,
        )

        # Chain must be intact after SHOWN.
        assert validate_chain(approval_id, con) is True

        # Events must be in order: REQUESTED -> SHOWN.
        from gaia.approvals.store import replay_for_approval
        events = replay_for_approval(approval_id, con=con)
        types = [e["event_type"] for e in events]
        assert types == ["REQUESTED", "SHOWN"], (
            f"Expected [REQUESTED, SHOWN], got {types!r}"
        )


# ---------------------------------------------------------------------------
# T2.4 test 2: tampered payload raises before SHOWN
# ---------------------------------------------------------------------------

class TestTamperedPayloadRaisesBeforeShown:
    """Tamper detection: any mutation to the payload between REQUESTED and SHOWN
    must be caught by verify_fingerprint before the SHOWN event is written.

    This test class exercises the invariant from plan D13:
        'If fingerprint does not match the REQUESTED row, the orchestrator
         raises and does not present the approval to the user.'
    """

    def test_tampered_payload_raises_before_shown(self):
        """Mutated payload raises ChainTamperError -- SHOWN is never written.

        Simulates a Man-in-the-Middle scenario where the sealed_payload is
        modified between subagent emission and orchestrator presentation.
        """
        con = _make_v10_db()

        # Step 1: Subagent inserts REQUESTED with original payload.
        approval_id = insert_requested(
            _SEALED_PAYLOAD,
            agent_id="agent-mitm-test",
            session_id="session-mitm-001",
            con=con,
        )

        # Step 2: Tamper -- attacker changes 'scope' to a different target.
        tampered_payload = dict(_SEALED_PAYLOAD)
        tampered_payload["scope"] = "main"  # escalated from feature branch to main

        tampered_canonical_json = canonical_payload(tampered_payload)

        # Step 3: verify_fingerprint must raise ChainTamperError.
        with pytest.raises(ChainTamperError, match="Fingerprint mismatch"):
            verify_fingerprint(approval_id, tampered_canonical_json, con)

        # Step 4: No SHOWN event must exist (orchestrator stopped before writing it).
        events = con.execute(
            "SELECT event_type FROM approval_events WHERE approval_id = ?",
            (approval_id,),
        ).fetchall()
        event_types = [row[0] for row in events]
        assert "SHOWN" not in event_types, (
            "SHOWN must NOT be inserted when fingerprint validation fails; "
            f"found events: {event_types!r}"
        )

    def test_tampered_exact_content_raises(self):
        """Mutation of exact_content (the command string) raises ChainTamperError.

        exact_content is the highest-sensitivity field: it is the verbatim
        command string the system will execute. Any alteration must be caught.
        """
        con = _make_v10_db()

        approval_id = insert_requested(
            _SEALED_PAYLOAD,
            agent_id="agent-content-tamper",
            session_id="session-content-001",
            con=con,
        )

        # Tamper: swap the command to target a different branch.
        tampered_payload = dict(_SEALED_PAYLOAD)
        tampered_payload["exact_content"] = "git branch -D main"
        tampered_payload["commands"] = ["git branch -D main"]

        with pytest.raises(ChainTamperError, match="Fingerprint mismatch"):
            verify_fingerprint(
                approval_id, canonical_payload(tampered_payload), con
            )

    def test_tampered_risk_level_raises(self):
        """Downgrading risk_level raises ChainTamperError.

        An attacker who downgrades risk_level from 'high' to 'low' in transit
        could suppress user warnings. The fingerprint check must catch this.
        """
        high_risk_payload = dict(_SEALED_PAYLOAD)
        high_risk_payload["risk_level"] = "high"

        con = _make_v10_db()
        approval_id = insert_requested(
            high_risk_payload,
            agent_id="agent-risk-tamper",
            session_id="session-risk-001",
            con=con,
        )

        # Tamper: attacker downgrades risk_level to 'low'.
        tampered_payload = dict(high_risk_payload)
        tampered_payload["risk_level"] = "low"

        with pytest.raises(ChainTamperError, match="Fingerprint mismatch"):
            verify_fingerprint(
                approval_id, canonical_payload(tampered_payload), con
            )

    def test_invalid_json_raises_value_error(self):
        """Passing non-JSON bytes to verify_fingerprint raises ValueError."""
        con = _make_v10_db()
        approval_id = insert_requested(
            _SEALED_PAYLOAD,
            agent_id="agent-bad-json",
            session_id="session-bad-json",
            con=con,
        )

        with pytest.raises(ValueError, match="not valid JSON"):
            verify_fingerprint(approval_id, "this is not json {{", con)

    def test_missing_requested_event_raises_value_error(self):
        """verify_fingerprint raises ValueError when no REQUESTED event exists.

        This guards against calling verify_fingerprint with a non-existent
        or wrong approval_id -- prevents silent pass on an empty baseline.
        """
        con = _make_v10_db()

        # Insert a minimal approval row but no events.
        con.execute(
            "INSERT INTO approvals (id, agent_id, status) "
            "VALUES ('P-no-events-999', 'agent-x', 'pending')"
        )
        con.commit()

        with pytest.raises(ValueError, match="No REQUESTED event"):
            verify_fingerprint(
                "P-no-events-999",
                canonical_payload(_SEALED_PAYLOAD),
                con,
            )
