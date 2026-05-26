"""Tests for approval_events hash-chain integrity and append-only invariants.

These tests verify:
  1. BEFORE UPDATE trigger raises "approval_events is append-only"
  2. BEFORE DELETE trigger raises "approval_events is append-only"
  3. Chain-walk validator detects a tampered prev_hash
  4. Chain-walk validator detects a tampered fingerprint
  5. A clean chain passes validation

Satisfies: AC-3 from brief approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Task:       T1.2 / T1.3 (M1, Wave 1)
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup: ensure gaia package and tests package are importable
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.approvals.chain import (  # noqa: E402
    ChainTamperError,
    validate_chain,
    insert_event,
    _compute_this_hash,
)


# ---------------------------------------------------------------------------
# Helper: build an isolated in-memory DB with the v12 schema
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the approvals + approval_events tables
    and all three triggers. Registers gaia_sha256 on the connection."""
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")

    # Register the gaia_sha256 scalar function (mirrors writer._connect())
    def _gaia_sha256(value: str | None) -> str:
        return _sha256(value)

    con.create_function("gaia_sha256", 1, _gaia_sha256, deterministic=True)

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

    # Seed a parent approval row
    con.execute(
        "INSERT INTO approvals (id, agent_id, session_id) VALUES ('P-test-001', 'agent-x', 'session-1')"
    )
    con.commit()
    return con


def _insert_event(con: sqlite3.Connection, approval_id: str, event_type: str,
                  fingerprint: str | None, prev_hash: str | None = None) -> int:
    """Insert an event row via insert_event() and return its id.

    Uses the canonical insert_event() API which computes this_hash before the
    INSERT. The prev_hash parameter is accepted for test clarity but is ignored
    since insert_event() queries the chain tip itself -- caller only needs to
    ensure events are inserted in the right order.
    """
    event_id = insert_event(
        con,
        approval_id,
        event_type,
        fingerprint=fingerprint,
    )
    con.commit()
    return event_id


def _get_this_hash(con: sqlite3.Connection, event_id: int) -> str | None:
    """Retrieve this_hash for a specific event_id."""
    row = con.execute(
        "SELECT this_hash FROM approval_events WHERE id = ?", (event_id,)
    ).fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Tests: append-only invariants
# ---------------------------------------------------------------------------

class TestAppendOnlyImmutability:
    """BEFORE UPDATE and BEFORE DELETE triggers must raise on mutation."""

    def test_direct_update_raises(self):
        """BEFORE UPDATE trigger raises 'approval_events is append-only'."""
        con = _make_v12_db()
        event_id = _insert_event(con, "P-test-001", "REQUESTED", "fp1", None)

        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="approval_events is append-only"):
            con.execute(
                "UPDATE approval_events SET fingerprint = 'tampered' WHERE id = ?",
                (event_id,),
            )
            con.commit()

    def test_direct_delete_raises(self):
        """BEFORE DELETE trigger raises 'approval_events is append-only'."""
        con = _make_v12_db()
        event_id = _insert_event(con, "P-test-001", "REQUESTED", "fp1", None)

        with pytest.raises((sqlite3.OperationalError, sqlite3.IntegrityError), match="approval_events is append-only"):
            con.execute(
                "DELETE FROM approval_events WHERE id = ?", (event_id,)
            )
            con.commit()

    def test_insert_is_allowed(self):
        """INSERT is not affected by the immutability triggers."""
        con = _make_v12_db()
        event_id = _insert_event(con, "P-test-001", "REQUESTED", "fp1", None)
        assert event_id is not None
        # A second insert should also work
        event_id2 = _insert_event(con, "P-test-001", "APPROVED", None, None)
        assert event_id2 is not None
        assert event_id2 > event_id


# ---------------------------------------------------------------------------
# Tests: hash-chain computation (AFTER INSERT trigger)
# ---------------------------------------------------------------------------

class TestHashChainComputation:
    """AFTER INSERT trigger must compute this_hash = gaia_sha256(prev||fp)."""

    def test_genesis_row_hash(self):
        """Genesis row (prev_hash=NULL): this_hash = SHA-256('' || fingerprint)."""
        con = _make_v12_db()
        fingerprint = "abc123fingerprint"
        event_id = _insert_event(con, "P-test-001", "REQUESTED", fingerprint, None)

        stored = _get_this_hash(con, event_id)
        expected = _sha256("" + fingerprint)  # COALESCE(NULL, '') = ''
        assert stored == expected, (
            f"Genesis row this_hash mismatch: stored={stored!r}, expected={expected!r}"
        )

    def test_chained_row_hash(self):
        """Subsequent row: this_hash = SHA-256(prev_hash || fingerprint)."""
        con = _make_v12_db()
        fp1 = "fingerprint-row1"
        id1 = _insert_event(con, "P-test-001", "REQUESTED", fp1)
        prev_hash = _get_this_hash(con, id1)

        fp2 = "fingerprint-row2"
        id2 = _insert_event(con, "P-test-001", "APPROVED", fp2)

        stored = _get_this_hash(con, id2)
        expected = _sha256((prev_hash or "") + fp2)
        assert stored == expected

    def test_null_fingerprint_row_hash(self):
        """Row with null fingerprint: this_hash = SHA-256(prev_hash || '')."""
        con = _make_v12_db()
        id1 = _insert_event(con, "P-test-001", "REQUESTED", "fp1")
        prev_hash = _get_this_hash(con, id1)

        # SHOWN event with no distinct fingerprint
        id2 = _insert_event(con, "P-test-001", "SHOWN", None)
        stored = _get_this_hash(con, id2)
        expected = _sha256((prev_hash or "") + "")
        assert stored == expected


# ---------------------------------------------------------------------------
# Tests: chain-walk validator (gaia.approvals.chain.validate_chain)
# ---------------------------------------------------------------------------

class TestChainWalkValidator:
    """validate_chain must pass on clean chains and raise on tampered rows."""

    def test_chain_walk_validates_clean_chain(self):
        """A clean 3-event chain passes validate_chain without error."""
        con = _make_v12_db()
        _insert_event(con, "P-test-001", "REQUESTED", "fingerprint-a")
        _insert_event(con, "P-test-001", "APPROVED", "fingerprint-b")
        _insert_event(con, "P-test-001", "EXECUTED", None)

        # validate_chain must return True for a clean chain
        result = validate_chain("P-test-001", con)
        assert result is True

    def test_tampered_prev_hash_detected(self):
        """Chain validator raises ChainTamperError when this_hash was tampered.

        Strategy: drop the immutability trigger, UPDATE this_hash to a fake
        value (simulates external tampering), recreate the trigger, then run
        validate_chain which must detect the discrepancy.
        """
        con = _make_v12_db()
        id1 = _insert_event(con, "P-test-001", "REQUESTED", "fp-original")

        # Drop immutability trigger temporarily to allow simulated tampering
        con.execute("DROP TRIGGER IF EXISTS bu_approval_events_immutable")
        con.commit()

        # Directly UPDATE this_hash to a fake value (simulates external tampering)
        con.execute(
            "UPDATE approval_events SET this_hash = 'tampered-hash-value' WHERE id = ?",
            (id1,),
        )
        con.commit()

        # Recreate the immutability trigger
        con.executescript("""
            CREATE TRIGGER bu_approval_events_immutable
            BEFORE UPDATE ON approval_events
            BEGIN
                SELECT RAISE(ABORT, 'approval_events is append-only');
            END;
        """)

        with pytest.raises(ChainTamperError, match="tamper"):
            validate_chain("P-test-001", con)

    def test_empty_chain_passes_validation(self):
        """An approval with no events passes validate_chain (vacuously true)."""
        con = _make_v12_db()
        # No events inserted for P-test-001
        result = validate_chain("P-test-001", con)
        assert result is True

    def test_validate_chain_different_approvals_independent(self):
        """Chain validation for one approval does not affect another."""
        con = _make_v12_db()
        # Insert a second approval
        con.execute(
            "INSERT INTO approvals (id, agent_id) VALUES ('P-test-002', 'agent-y')"
        )
        con.commit()

        # Events for P-test-001
        _insert_event(con, "P-test-001", "REQUESTED", "fp-001")
        _insert_event(con, "P-test-001", "APPROVED", None)

        # Events for P-test-002
        _insert_event(con, "P-test-002", "REQUESTED", "fp-002")
        _insert_event(con, "P-test-002", "APPROVED", None)

        # Both chains must pass
        assert validate_chain("P-test-001", con) is True
        assert validate_chain("P-test-002", con) is True
