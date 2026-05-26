"""Tests for T2.3 -- Cross-session pending visibility.

Verifies:
  1. Pending from session A is visible from session B via list_pending(all_sessions=True).
  2. Without all_sessions, each session only sees its own pending approvals.
  3. age_seconds and stale fields are present and semantically correct.
  4. approve() transitions pending -> approved via cross-session approver_session.
  5. reject() transitions pending -> rejected via cross-session approver_session.
  6. Approved / rejected approvals no longer appear in list_pending() output.

Satisfies: AC-6, AC-7 from brief
    approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Task: T2.3 (M2, Wave 2)

AC test command:
    cd gaia && python -m pytest tests/cli/test_approvals_pending_cross_session.py -q
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.approvals.store import (  # noqa: E402
    approve,
    get_pending,
    insert_requested,
    list_pending,
    record_event,
    reject,
    transition,
)


# ---------------------------------------------------------------------------
# Shared in-memory DB factory (mirrors _make_v12_db from test_approval_events)
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


_SAMPLE_PAYLOAD = {
    "operation": "Delete stale branch",
    "exact_content": "git branch -D feature/stale",
    "scope": "feature/stale",
    "risk_level": "medium",
    "rollback_hint": "git branch feature/stale <sha>",
    "rationale": "Branch is merged and stale",
    "commands": ["git branch -D feature/stale"],
}


# ---------------------------------------------------------------------------
# Cross-session visibility
# ---------------------------------------------------------------------------

class TestCrossSessionVisibility:
    """Pending approval created in session A is visible from session B."""

    def test_pending_from_session_a_visible_with_all_sessions_true(self):
        """list_pending(all_sessions=True) returns approvals from all sessions."""
        con = _make_v12_db()

        id_a = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-1", session_id="session-A", con=con
        )
        id_b = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-2", session_id="session-B", con=con
        )

        # Cross-session view returns both.
        all_pending = list_pending(all_sessions=True, con=con)
        all_ids = {r["id"] for r in all_pending}
        assert id_a in all_ids, "session-A approval must be visible cross-session"
        assert id_b in all_ids, "session-B approval must be visible cross-session"

    def test_session_scoped_view_excludes_other_sessions(self):
        """Without all_sessions, each session only sees its own pending approvals."""
        con = _make_v12_db()

        id_a = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-1", session_id="session-A", con=con
        )
        id_b = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-2", session_id="session-B", con=con
        )

        # Session-A scoped view: only id_a.
        pending_a = list_pending(all_sessions=False, session_id="session-A", con=con)
        ids_a = {r["id"] for r in pending_a}
        assert id_a in ids_a, "session-A approval must appear in session-A view"
        assert id_b not in ids_a, "session-B approval must NOT appear in session-A view"

        # Session-B scoped view: only id_b.
        pending_b = list_pending(all_sessions=False, session_id="session-B", con=con)
        ids_b = {r["id"] for r in pending_b}
        assert id_b in ids_b, "session-B approval must appear in session-B view"
        assert id_a not in ids_b, "session-A approval must NOT appear in session-B view"

    def test_empty_result_when_session_has_no_pending(self):
        """list_pending for a session with no pending returns empty list."""
        con = _make_v12_db()

        insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-1", session_id="session-A", con=con
        )

        pending = list_pending(all_sessions=False, session_id="session-C", con=con)
        assert pending == [], "unknown session must return empty pending list"

    def test_get_pending_alias_matches_list_pending_result_ids(self):
        """get_pending() and list_pending() return the same approval IDs."""
        con = _make_v12_db()

        insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-1", session_id="session-X", con=con
        )

        raw = get_pending(session_id="session-X", all_sessions=False, con=con)
        enriched = list_pending(all_sessions=False, session_id="session-X", con=con)

        raw_ids = {r["id"] for r in raw}
        enriched_ids = {r["id"] for r in enriched}
        assert raw_ids == enriched_ids, (
            "get_pending() and list_pending() must return the same approval IDs"
        )


# ---------------------------------------------------------------------------
# Age / staleness enrichment
# ---------------------------------------------------------------------------

class TestAgeEnrichment:
    """list_pending() adds age_seconds and stale fields to each row."""

    def test_age_seconds_field_present_and_non_negative(self):
        """Every row from list_pending() has a non-negative age_seconds field."""
        con = _make_v12_db()
        insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s1", con=con
        )

        rows = list_pending(all_sessions=True, con=con)
        assert len(rows) == 1
        row = rows[0]
        assert "age_seconds" in row, "age_seconds field must be present"
        assert row["age_seconds"] >= 0.0, "age_seconds must be non-negative"

    def test_stale_field_present(self):
        """Every row from list_pending() has a stale boolean field."""
        con = _make_v12_db()
        insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s1", con=con
        )

        rows = list_pending(all_sessions=True, con=con)
        assert len(rows) == 1
        assert "stale" in rows[0], "stale field must be present"
        assert isinstance(rows[0]["stale"], bool), "stale must be a bool"

    def test_fresh_approval_is_not_stale(self):
        """An approval just created has stale=False."""
        con = _make_v12_db()
        insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s1", con=con
        )

        rows = list_pending(all_sessions=True, con=con)
        assert rows[0]["stale"] is False, "brand-new approval must not be stale"

    def test_old_approval_is_stale(self):
        """An approval with created_at > 1 hour ago has stale=True."""
        con = _make_v12_db()

        # Insert an approval, then backdate its created_at to 2 hours ago.
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s1", con=con
        )
        two_hours_ago = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute(
            "UPDATE approvals SET created_at = ? WHERE id = ?",
            (two_hours_ago, approval_id),
        )
        con.commit()

        rows = list_pending(all_sessions=True, con=con)
        target = next(r for r in rows if r["id"] == approval_id)
        assert target["stale"] is True, "approval > 1 hour old must be stale"
        assert target["age_seconds"] > 3600.0, (
            "age_seconds must exceed 3600 for a 2-hour-old approval"
        )


# ---------------------------------------------------------------------------
# approve() and reject() convenience methods
# ---------------------------------------------------------------------------

class TestApproveReject:
    """approve() and reject() are cross-session convenience wrappers."""

    def test_approve_transitions_pending_to_approved(self):
        """approve() transitions a pending approval to approved status."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="subagent", session_id="session-S1", con=con
        )

        # Session S2 approves the pending created by S1.
        approve(approval_id, approver_session="session-S2", con=con)

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "approved", "status must be 'approved' after approve()"

    def test_approve_inserts_approved_event_in_chain(self):
        """approve() inserts an APPROVED event in the hash chain."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="subagent", session_id="session-S1", con=con
        )
        approve(approval_id, approver_session="session-S2", con=con)

        events = con.execute(
            "SELECT event_type FROM approval_events "
            "WHERE approval_id = ? ORDER BY id ASC",
            (approval_id,),
        ).fetchall()
        types = [e[0] for e in events]
        assert "APPROVED" in types, "APPROVED event must be in the chain"

    def test_approved_approval_not_in_list_pending(self):
        """An approved approval is no longer returned by list_pending()."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="subagent", session_id="session-S1", con=con
        )
        approve(approval_id, approver_session="session-S2", con=con)

        pending = list_pending(all_sessions=True, con=con)
        ids = {r["id"] for r in pending}
        assert approval_id not in ids, "approved approval must not appear in pending list"

    def test_reject_transitions_pending_to_rejected(self):
        """reject() transitions a pending approval to rejected status."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="subagent", session_id="session-S1", con=con
        )

        reject(approval_id, approver_session="session-S2", con=con)

        row = con.execute(
            "SELECT status FROM approvals WHERE id = ?", (approval_id,)
        ).fetchone()
        assert row[0] == "rejected", "status must be 'rejected' after reject()"

    def test_reject_inserts_rejected_event_in_chain(self):
        """reject() inserts a REJECTED event in the hash chain."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="subagent", session_id="session-S1", con=con
        )
        reject(approval_id, approver_session="session-S2", con=con)

        events = con.execute(
            "SELECT event_type FROM approval_events "
            "WHERE approval_id = ? ORDER BY id ASC",
            (approval_id,),
        ).fetchall()
        types = [e[0] for e in events]
        assert "REJECTED" in types, "REJECTED event must be in the chain"

    def test_rejected_approval_not_in_list_pending(self):
        """A rejected approval is no longer returned by list_pending()."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="subagent", session_id="session-S1", con=con
        )
        reject(approval_id, approver_session="session-S2", con=con)

        pending = list_pending(all_sessions=True, con=con)
        ids = {r["id"] for r in pending}
        assert approval_id not in ids, "rejected approval must not appear in pending list"

    def test_approve_raises_when_already_approved(self):
        """approve() raises ValueError when called on an already-approved approval."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s", con=con
        )
        approve(approval_id, approver_session="s2", con=con)

        with pytest.raises(ValueError, match="expected status"):
            approve(approval_id, approver_session="s3", con=con)

    def test_reject_raises_when_already_rejected(self):
        """reject() raises ValueError when called on an already-rejected approval."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s", con=con
        )
        reject(approval_id, approver_session="s2", con=con)

        with pytest.raises(ValueError, match="expected status"):
            reject(approval_id, approver_session="s3", con=con)

    def test_cross_session_approval_stores_approver_session_id(self):
        """The APPROVED event stores the approver_session as its session_id."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="session-requester", con=con
        )
        approve(approval_id, approver_session="session-approver", con=con)

        row = con.execute(
            "SELECT session_id FROM approval_events "
            "WHERE approval_id = ? AND event_type = 'APPROVED'",
            (approval_id,),
        ).fetchone()
        assert row is not None, "APPROVED event must exist"
        assert row[0] == "session-approver", (
            "APPROVED event session_id must be the approver session, not the requester"
        )


# ---------------------------------------------------------------------------
# Multiple pending approvals ordering
# ---------------------------------------------------------------------------

class TestOrderingAndMultiplePending:
    """list_pending() returns rows in created_at ASC order."""

    def test_list_pending_ordered_by_created_at_asc(self):
        """list_pending(all_sessions=True) returns rows oldest-first."""
        con = _make_v12_db()

        id_first = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s1", con=con
        )
        id_second = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s2", con=con
        )
        id_third = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s3", con=con
        )

        rows = list_pending(all_sessions=True, con=con)
        ids = [r["id"] for r in rows]
        assert ids.index(id_first) < ids.index(id_second), (
            "earlier approval must appear before later approval"
        )
        assert ids.index(id_second) < ids.index(id_third)

    def test_approving_one_does_not_affect_others(self):
        """Approving one pending approval does not remove others from list_pending."""
        con = _make_v12_db()

        id_a = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s1", con=con
        )
        id_b = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s2", con=con
        )

        approve(id_a, approver_session="s-approver", con=con)

        remaining = list_pending(all_sessions=True, con=con)
        remaining_ids = {r["id"] for r in remaining}
        assert id_a not in remaining_ids, "approved approval must be gone"
        assert id_b in remaining_ids, "unapproved approval must still be pending"
