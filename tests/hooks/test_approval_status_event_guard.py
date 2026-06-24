"""Tests for Task B audit-immutability gap closure.

Verifies:
  1. A direct UPDATE approvals SET status = 'approved' WITHOUT a preceding
     APPROVED event is rejected by the bu_approvals_status_has_event trigger.
  2. The same direct UPDATE preceded by a manually inserted APPROVED event
     in the same transaction succeeds (the trigger checks the event exists).
  3. Normal approve / reject / revoke via store.transition() still works and
     leaves a chained event -- the trigger passes on the legitimate write path.
  4. The existing chain-integrity tests continue to pass (regression guard).
  5. A direct UPDATE to 'expired' (cleanup path) is allowed without an event
     (expired is excluded from the trigger's WHEN clause).

Satisfies Task B acceptance criteria from the approval redesign.
"""

from __future__ import annotations

import hashlib
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
    _compute_this_hash,
)
from gaia.approvals.store import (  # noqa: E402
    insert_requested,
    record_event,
    transition,
    approve,
    reject,
    revoke,
    replay_for_approval,
)


# ---------------------------------------------------------------------------
# Helper: build an isolated in-memory DB with the full v19 schema
# (approvals + approval_events + all triggers including the new guard)
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v19_db() -> sqlite3.Connection:
    """Create an in-memory DB with the v19 schema (including bu_approvals_status_has_event).

    Registers gaia_sha256 to satisfy the chain hash-computation path.
    """
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")

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

        -- approval_events immutability triggers (carry-forward from v12)
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

        -- Task B: guard trigger -- every status transition must have a preceding event
        CREATE TRIGGER bu_approvals_status_has_event
        BEFORE UPDATE OF status ON approvals
        WHEN NEW.status != OLD.status AND NEW.status IN ('approved', 'rejected', 'revoked')
        BEGIN
            SELECT CASE
                WHEN (
                    SELECT COUNT(*) FROM approval_events
                     WHERE approval_id = NEW.id
                       AND event_type = CASE NEW.status
                                            WHEN 'approved' THEN 'APPROVED'
                                            WHEN 'rejected' THEN 'REJECTED'
                                            WHEN 'revoked'  THEN 'REVOKED'
                                        END
                ) = 0
                THEN RAISE(ABORT, 'approvals: status change requires a preceding event in approval_events')
            END;
        END;
    """)
    return con


def _seed_approval(con: sqlite3.Connection, approval_id: str = "P-test-b001") -> str:
    """Seed a pending approval row and its REQUESTED event. Returns approval_id."""
    con.execute(
        "INSERT INTO approvals (id, agent_id, session_id) VALUES (?, 'agent-x', 'sess-1')",
        (approval_id,),
    )
    insert_event(con, approval_id, "REQUESTED", fingerprint="fp-initial")
    con.commit()
    return approval_id


# ---------------------------------------------------------------------------
# Tests: trigger blocks direct UPDATE without preceding event
# ---------------------------------------------------------------------------

class TestDirectUpdateBlockedWithoutEvent:
    """bu_approvals_status_has_event must reject direct status UPDATEs without events."""

    def test_direct_update_to_approved_without_event_raises(self):
        """UPDATE approvals SET status='approved' without APPROVED event is rejected."""
        con = _make_v19_db()
        _seed_approval(con)

        with pytest.raises(
            (sqlite3.OperationalError, sqlite3.IntegrityError),
            match="approvals: status change requires a preceding event in approval_events",
        ):
            con.execute(
                "UPDATE approvals SET status = 'approved' WHERE id = 'P-test-b001'"
            )
            con.commit()

    def test_direct_update_to_rejected_without_event_raises(self):
        """UPDATE approvals SET status='rejected' without REJECTED event is rejected."""
        con = _make_v19_db()
        _seed_approval(con)

        with pytest.raises(
            (sqlite3.OperationalError, sqlite3.IntegrityError),
            match="approvals: status change requires a preceding event in approval_events",
        ):
            con.execute(
                "UPDATE approvals SET status = 'rejected' WHERE id = 'P-test-b001'"
            )
            con.commit()

    def test_direct_update_to_revoked_without_event_raises(self):
        """UPDATE approvals SET status='revoked' without REVOKED event is rejected."""
        con = _make_v19_db()
        _seed_approval(con)

        with pytest.raises(
            (sqlite3.OperationalError, sqlite3.IntegrityError),
            match="approvals: status change requires a preceding event in approval_events",
        ):
            con.execute(
                "UPDATE approvals SET status = 'revoked' WHERE id = 'P-test-b001'"
            )
            con.commit()

    def test_direct_update_to_expired_without_event_is_allowed(self):
        """UPDATE to 'expired' (cleanup path) is allowed even without an EXPIRED event.

        'expired' is intentionally excluded from the trigger's WHEN clause
        because there is no EXPIRED event_type in the approval_events schema --
        expiry is a TTL-sweep side effect, not a user-visible decision.
        """
        con = _make_v19_db()
        _seed_approval(con)

        # Should not raise
        con.execute(
            "UPDATE approvals SET status = 'expired' WHERE id = 'P-test-b001'"
        )
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = 'P-test-b001'"
        ).fetchone()
        assert row[0] == "expired"


# ---------------------------------------------------------------------------
# Tests: direct UPDATE with a preceding event is allowed
# ---------------------------------------------------------------------------

class TestDirectUpdateAllowedWithPrecedingEvent:
    """A direct UPDATE is accepted when the matching event was inserted first."""

    def test_direct_update_to_approved_with_approved_event_succeeds(self):
        """Inserting APPROVED event first, then UPDATE status='approved' passes the trigger."""
        con = _make_v19_db()
        _seed_approval(con)

        # Insert APPROVED event first (mimicking what transition() does)
        insert_event(con, "P-test-b001", "APPROVED", fingerprint=None)

        # NOW the UPDATE should be allowed
        con.execute(
            "UPDATE approvals SET status = 'approved' WHERE id = 'P-test-b001'"
        )
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = 'P-test-b001'"
        ).fetchone()
        assert row[0] == "approved"

    def test_direct_update_to_rejected_with_rejected_event_succeeds(self):
        """Inserting REJECTED event first, then UPDATE status='rejected' passes the trigger."""
        con = _make_v19_db()
        _seed_approval(con)

        insert_event(con, "P-test-b001", "REJECTED", fingerprint=None)
        con.execute(
            "UPDATE approvals SET status = 'rejected' WHERE id = 'P-test-b001'"
        )
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = 'P-test-b001'"
        ).fetchone()
        assert row[0] == "rejected"


# ---------------------------------------------------------------------------
# Tests: legitimate write path via store.transition() still works
# ---------------------------------------------------------------------------

class TestTransitionLegitimateWritePath:
    """store.transition() (event-first order) must still work correctly with the trigger."""

    def test_transition_pending_to_approved_succeeds(self):
        """transition() from pending to approved writes status and event, chain valid."""
        con = _make_v19_db()
        payload = {"operation": "push", "commands": ["git push origin main"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        # Should not raise
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "approved"

        events = replay_for_approval(approval_id, con=con)
        types = [e["event_type"] for e in events]
        assert "APPROVED" in types
        assert validate_chain(approval_id, con) is True

    def test_approve_convenience_wrapper_succeeds(self):
        """approve() convenience wrapper works and the chain is intact."""
        con = _make_v19_db()
        payload = {"operation": "deploy", "commands": ["kubectl apply -f app.yaml"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        approve(approval_id, "s", agent_id="user", con=con)
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "approved"
        assert validate_chain(approval_id, con) is True

    def test_reject_convenience_wrapper_succeeds(self):
        """reject() convenience wrapper works and the chain is intact."""
        con = _make_v19_db()
        payload = {"operation": "deploy", "commands": ["kubectl apply -f app.yaml"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        reject(approval_id, "s", agent_id="user", con=con)
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "rejected"
        assert validate_chain(approval_id, con) is True

    def test_revoke_convenience_wrapper_succeeds(self):
        """revoke() convenience wrapper works and the chain is intact."""
        con = _make_v19_db()
        payload = {"operation": "cleanup", "commands": ["rm -rf /tmp/old"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        revoke(approval_id, "s", agent_id="user", con=con)
        con.commit()

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "revoked"
        assert validate_chain(approval_id, con) is True

    def test_full_lifecycle_approved_then_executed(self):
        """Full lifecycle: REQUESTED -> APPROVED -> EXECUTED validates end to end."""
        con = _make_v19_db()
        payload = {"operation": "push", "commands": ["git push origin main"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        approve(approval_id, "s", agent_id="user", con=con)
        record_event(
            approval_id,
            "EXECUTED",
            session_id="s",
            payload_json='{"command": "git push", "exit_code": 0}',
            con=con,
        )
        con.commit()

        events = replay_for_approval(approval_id, con=con)
        types = [e["event_type"] for e in events]
        assert types == ["REQUESTED", "APPROVED", "EXECUTED"]
        assert validate_chain(approval_id, con) is True

    def test_transition_order_event_before_status_update(self):
        """Verify transition() inserts event BEFORE updating status.

        We instrument this by checking that after the event is in the DB
        but before commit, the trigger allows the status UPDATE (the trigger
        fires at UPDATE time and sees the event row already in the transaction).
        The indirection is via transition() itself -- if the order were reversed
        (UPDATE first, event second), the trigger would fire before the event
        exists and RAISE, causing the whole transition to fail. That it succeeds
        is evidence that event-first ordering is in place.
        """
        con = _make_v19_db()
        payload = {"operation": "test", "commands": ["echo hi"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)

        # This would fail with OperationalError if event were inserted after the UPDATE
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "approved", (
            "transition() succeeded: event-first ordering is in place and "
            "the trigger allowed the status UPDATE."
        )


# ---------------------------------------------------------------------------
# Tests: regression guard -- existing chain-integrity invariants still hold
# ---------------------------------------------------------------------------

class TestChainIntegrityRegressionGuard:
    """Existing chain-integrity invariants must not be broken by Task B changes."""

    def test_append_only_update_still_raises(self):
        """BEFORE UPDATE trigger on approval_events still raises on any UPDATE."""
        con = _make_v19_db()
        _seed_approval(con)
        ev_id = con.execute(
            "SELECT id FROM approval_events WHERE approval_id = 'P-test-b001' LIMIT 1"
        ).fetchone()[0]

        with pytest.raises(
            (sqlite3.OperationalError, sqlite3.IntegrityError),
            match="approval_events is append-only",
        ):
            con.execute(
                "UPDATE approval_events SET fingerprint = 'tampered' WHERE id = ?",
                (ev_id,),
            )
            con.commit()

    def test_append_only_delete_still_raises(self):
        """BEFORE DELETE trigger on approval_events still raises on any DELETE."""
        con = _make_v19_db()
        _seed_approval(con)
        ev_id = con.execute(
            "SELECT id FROM approval_events WHERE approval_id = 'P-test-b001' LIMIT 1"
        ).fetchone()[0]

        with pytest.raises(
            (sqlite3.OperationalError, sqlite3.IntegrityError),
            match="approval_events is append-only",
        ):
            con.execute(
                "DELETE FROM approval_events WHERE id = ?", (ev_id,)
            )
            con.commit()

    def test_validate_chain_passes_after_approve(self):
        """validate_chain passes on a clean approval chain after transition."""
        con = _make_v19_db()
        payload = {"operation": "test", "commands": ["ls"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)

        assert validate_chain(approval_id, con) is True

    def test_validate_chain_detects_tamper_after_transition(self):
        """Tampered this_hash is still detected by validate_chain after Task B changes."""
        con = _make_v19_db()
        payload = {"operation": "test", "commands": ["ls"]}
        approval_id = insert_requested(payload, agent_id="ag", session_id="s", con=con)
        transition(approval_id, "pending", "approved",
                   agent_id="user", session_id="s", con=con)

        # Drop the immutability trigger temporarily to simulate external tampering
        con.execute("DROP TRIGGER IF EXISTS bu_approval_events_immutable")
        con.commit()
        # Tamper with the genesis event's this_hash
        con.execute(
            "UPDATE approval_events SET this_hash = 'fake-tampered-hash' "
            "WHERE approval_id = ? AND event_type = 'REQUESTED'",
            (approval_id,),
        )
        con.commit()

        with pytest.raises(ChainTamperError, match="tamper"):
            validate_chain(approval_id, con)
