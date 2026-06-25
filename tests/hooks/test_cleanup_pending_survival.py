#!/usr/bin/env python3
"""Integration tests for approval_cleanup.cleanup() against a real DB schema.

These tests lock the P-3d23 invariant restored by Fix A:

    A pending younger than its TTL (DEFAULT_PENDING_TTL_MINUTES = 1440 / 24h)
    MUST survive ANY subagent's SubagentStop cleanup(), regardless of that
    subagent's final plan_status. SubagentStop is the normal lifecycle of the
    block -> approve -> retry flow; it is the wrong trigger for revoking fresh
    pendings, because all subagents share the main session_id.

Before Fix A, cleanup() revoked EVERY pending in the session that was not in
preserve_nonces, with no age/TTL check -- so any subagent finishing as COMPLETE
(empty preserve_nonces) wiped out every other outstanding pending in the
session, breaking the documented block -> approve -> retry flow.

After Fix A, cleanup() only acts on GENUINELY-EXPIRED pendings (age past the
24h pending TTL), transitioning them to the schema 'expired' status with
non-null provenance (agent_id + reason metadata) via store.expire().

Unlike the unit tests in tests/hooks/modules/security/test_approval_cleanup.py
(which mock list_pending/revoke), these run cleanup() end-to-end against an
isolated SQLite file carrying the full approvals + approval_events schema, so
the TTL gate, the 'expired' transition, the status-has-event trigger, and the
provenance wiring are all exercised for real.
"""

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
for _p in (str(REPO_ROOT), str(HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.fixtures.db_helpers import apply_approvals_schema, seed_db_pending  # noqa: E402


def _sha256(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


# Full approvals + approval_events schema WITH the status-has-event trigger,
# so the 'expired'/'revoked' transition path is exercised against the real
# guard rather than a permissive minimal copy.
_FULL_APPROVALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT,
    session_id   TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected', 'revoked', 'expired')),
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

CREATE TRIGGER IF NOT EXISTS bu_approvals_status_has_event
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
"""


@pytest.fixture()
def writer_db(monkeypatch, tmp_path):
    """Isolate gaia.store.writer._connect to a temp file carrying the full
    approvals schema (incl. the status-has-event trigger and gaia_sha256).

    gaia.approvals.store._open_db delegates to writer._connect, so patching it
    here routes every store read/write -- insert_requested, list_pending,
    revoke, expire, get_by_id -- at the test-local DB.
    """
    import gaia.store.writer as gwriter

    db_path = tmp_path / "cleanup_writer.db"

    def _make_db():
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function("gaia_sha256", 1, _sha256, deterministic=True)
        con.executescript(_FULL_APPROVALS_SCHEMA)
        con.commit()
        return con

    monkeypatch.setattr(gwriter, "_connect", lambda db_path=None: _make_db())
    return db_path


def _backdate_created_at(db_path: Path, approval_id: str, iso_ts: str) -> None:
    """Force an approval's created_at so its age crosses (or stays under) TTL."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "UPDATE approvals SET created_at = ? WHERE id = ?", (iso_ts, approval_id)
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Test 1 + 2: the P-3d23 invariant -- fresh pending survives a COMPLETE Stop
# ---------------------------------------------------------------------------

class TestFreshPendingSurvivesSubagentStop:
    def test_fresh_pending_survives_complete_subagent_stop(self, writer_db):
        """A FRESH (< TTL) pending SURVIVES a SubagentStop cleanup() when the
        stopping subagent's contract is COMPLETE (empty preserve_nonces).

        This is the exact P-3d23 scenario. Before Fix A this pending was
        revoked; after Fix A it is still listed and still 'pending'.
        """
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import list_pending, get_by_id

        session_id = "shared-main-session"
        approval_id = seed_db_pending(
            command="terraform apply -auto-approve",
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
        )

        # A different subagent finishes as COMPLETE: preserve_nonces is empty.
        cleanup("developer", session_id=session_id, preserve_nonces=None)

        row = get_by_id(approval_id)
        assert row is not None
        assert row["status"] == "pending", (
            "Fresh pending MUST survive a COMPLETE SubagentStop (P-3d23 invariant)"
        )
        pending_ids = {p["id"] for p in list_pending(session_id=session_id)}
        assert approval_id in pending_ids, (
            "Fresh pending must still be listed after an unrelated subagent's Stop"
        )

    def test_fresh_pending_still_scannable_after_unrelated_stop(self, writer_db):
        """A pending emitted in the session is still returned by list_pending /
        scan_pending_db after an unrelated subagent's Stop (empty preserve)."""
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import list_pending

        session_id = "shared-main-session"
        kept = seed_db_pending(
            command="kubectl delete pod web-0",
            session_id=session_id,
            danger_verb="delete",
            danger_category="MUTATIVE",
        )

        # Unrelated subagent stops (COMPLETE, empty preserve_nonces).
        cleanup("cloud-troubleshooter", session_id=session_id, preserve_nonces=set())

        pending = list_pending(session_id=session_id)
        assert any(p["id"] == kept for p in pending), (
            "Pending must still be returned by list_pending after unrelated Stop"
        )

    def test_multiple_fresh_pendings_all_survive(self, writer_db):
        """Multiple fresh pendings in one session all survive a COMPLETE Stop."""
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import list_pending

        session_id = "shared-main-session"
        a = seed_db_pending(command="terraform apply", session_id=session_id,
                            danger_verb="apply")
        b = seed_db_pending(command="gcloud run deploy svc", session_id=session_id,
                            danger_verb="deploy")

        cleanup("developer", session_id=session_id, preserve_nonces=None)

        ids = {p["id"] for p in list_pending(session_id=session_id)}
        assert a in ids and b in ids, "All fresh pendings must survive"


# ---------------------------------------------------------------------------
# Test 3: a genuinely-expired pending IS cleaned up
# ---------------------------------------------------------------------------

class TestExpiredPendingIsCleaned:
    def test_expired_pending_is_transitioned_to_expired(self, writer_db):
        """A pending older than the 24h pending TTL IS cleaned up by cleanup()
        and lands in the schema 'expired' terminal status (not 'pending')."""
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import list_pending, get_by_id

        session_id = "shared-main-session"
        approval_id = seed_db_pending(
            command="terraform destroy",
            session_id=session_id,
            danger_verb="destroy",
            danger_category="MUTATIVE",
        )
        # Backdate well past 1440 min (25h ago).
        _backdate_created_at(writer_db, approval_id, "2026-06-24T00:00:00Z")
        # Anchor 'now' independent of wall clock not needed: 25h+ is past 24h
        # for any realistic test run date >= 2026-06-25. Use a far-back date to
        # be robust regardless of when the suite runs.
        _backdate_created_at(writer_db, approval_id, "2020-01-01T00:00:00Z")

        cleanup("developer", session_id=session_id, preserve_nonces=None)

        row = get_by_id(approval_id)
        assert row is not None
        assert row["status"] == "expired", (
            "Genuinely-expired pending must be transitioned to 'expired'"
        )
        pending_ids = {p["id"] for p in list_pending(session_id=session_id)}
        assert approval_id not in pending_ids, (
            "Expired pending must no longer appear in the pending list"
        )

    def test_expired_cleaned_but_fresh_in_same_session_kept(self, writer_db):
        """Mixed session: the expired pending is cleaned, the fresh one stays."""
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import get_by_id

        session_id = "shared-main-session"
        fresh = seed_db_pending(command="terraform apply", session_id=session_id,
                                danger_verb="apply")
        old = seed_db_pending(command="terraform destroy", session_id=session_id,
                              danger_verb="destroy")
        _backdate_created_at(writer_db, old, "2020-01-01T00:00:00Z")

        cleanup("developer", session_id=session_id, preserve_nonces=None)

        assert get_by_id(fresh)["status"] == "pending", "fresh must survive"
        assert get_by_id(old)["status"] == "expired", "old must be expired"


# ---------------------------------------------------------------------------
# Cross-session sweep: SubagentStop expires past-TTL pendings from ANY session,
# while fresh pendings in ANY session survive (global-scope invariant).
# ---------------------------------------------------------------------------

class TestCrossSessionSweep:
    def test_stale_pending_in_other_session_is_expired(self, writer_db):
        """A STALE (> TTL) pending created under session B IS expired when a
        subagent in session A stops.

        Before this fix the EXPIRE sweep was session-scoped
        (list_pending(all_sessions=False)), so a stale pending orphaned by a
        dead/other session never auto-expired -- it had to be drained by hand.
        With the global sweep (all_sessions=True), the session-A SubagentStop
        reaps it because the only gate is the age, not session membership.
        """
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import get_by_id

        session_b = "dead-session-B"
        session_a = "live-session-A"
        stale = seed_db_pending(
            command="terraform destroy -auto-approve",
            session_id=session_b,
            danger_verb="destroy",
            danger_category="MUTATIVE",
        )
        _backdate_created_at(writer_db, stale, "2020-01-01T00:00:00Z")

        # A subagent in a DIFFERENT session (A) stops.
        cleanup("developer", session_id=session_a, preserve_nonces=None)

        row = get_by_id(stale)
        assert row is not None
        assert row["status"] == "expired", (
            "Stale pending in another session MUST be expired at a SubagentStop "
            "from a different session (cross-session auto-expiry)"
        )

    def test_fresh_pending_in_other_session_survives(self, writer_db):
        """A FRESH (< TTL) pending created under session B SURVIVES when a
        subagent in session A stops.

        This is the critical guard against re-introducing the P-3d23 bug at
        global scope: widening the sweep to all_sessions must NOT touch a fresh
        pending in any session. The age gate is session-independent.
        """
        from modules.security.approval_cleanup import cleanup
        from gaia.approvals.store import get_by_id, list_pending

        session_b = "other-session-B"
        session_a = "live-session-A"
        fresh = seed_db_pending(
            command="kubectl delete pod web-0",
            session_id=session_b,
            danger_verb="delete",
            danger_category="MUTATIVE",
        )
        # No backdate: pending is fresh (< 24h TTL).

        # A subagent in a DIFFERENT session (A) stops.
        cleanup("cloud-troubleshooter", session_id=session_a, preserve_nonces=None)

        row = get_by_id(fresh)
        assert row is not None
        assert row["status"] == "pending", (
            "Fresh pending in another session MUST survive a cross-session "
            "SubagentStop (P-3d23 invariant holds at global scope)"
        )
        pending_ids = {p["id"] for p in list_pending(all_sessions=True)}
        assert fresh in pending_ids, (
            "Fresh cross-session pending must still be listed after the sweep"
        )

    def test_cross_session_expiry_event_has_provenance(self, writer_db):
        """A cross-session expiry still carries provenance: the auto-transition
        event records the sweeping agent_id and reason=expired_ttl."""
        import json
        from modules.security.approval_cleanup import cleanup

        session_b = "dead-session-B"
        session_a = "live-session-A"
        stale = seed_db_pending(
            command="terraform destroy",
            session_id=session_b,
            danger_verb="destroy",
        )
        _backdate_created_at(writer_db, stale, "2020-01-01T00:00:00Z")

        cleanup("platform-architect", session_id=session_a, preserve_nonces=None)

        con = sqlite3.connect(str(writer_db))
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT agent_id, metadata_json FROM approval_events "
            "WHERE approval_id = ? AND event_type = 'REVOKED'",
            (stale,),
        ).fetchall()
        con.close()

        assert rows, "A cross-session expiry must write an auto-transition event"
        for r in rows:
            assert r["agent_id"] == "platform-architect", (
                "cross-session expiry event must carry the sweeping agent_id"
            )
            assert r["metadata_json"], "event must carry metadata_json"
            meta = json.loads(r["metadata_json"])
            assert meta.get("reason") == "expired_ttl"


# ---------------------------------------------------------------------------
# Test 4: provenance guard -- every auto-transition has non-null agent_id + reason
# ---------------------------------------------------------------------------

class TestAutoTransitionProvenance:
    def test_expiry_event_has_non_null_provenance(self, writer_db):
        """Every auto-transition event written by cleanup() carries a non-null
        agent_id and a reason in metadata, closing the null-provenance gap."""
        import json
        from modules.security.approval_cleanup import cleanup

        session_id = "shared-main-session"
        approval_id = seed_db_pending(
            command="terraform destroy",
            session_id=session_id,
            danger_verb="destroy",
        )
        _backdate_created_at(writer_db, approval_id, "2020-01-01T00:00:00Z")

        cleanup("developer", session_id=session_id, preserve_nonces=None)

        con = sqlite3.connect(str(writer_db))
        con.row_factory = sqlite3.Row
        # The auto-transition event is the REVOKED event (the expire path emits
        # REVOKED with reason metadata, since there is no EXPIRED event_type).
        rows = con.execute(
            "SELECT event_type, agent_id, metadata_json FROM approval_events "
            "WHERE approval_id = ? AND event_type = 'REVOKED'",
            (approval_id,),
        ).fetchall()
        con.close()

        assert rows, "An auto-transition (REVOKED) event must have been written"
        for r in rows:
            assert r["agent_id"], "auto-transition event must have non-null agent_id"
            assert r["metadata_json"], "auto-transition event must carry metadata_json"
            meta = json.loads(r["metadata_json"])
            assert meta.get("reason"), "metadata must carry a reason"
            assert meta["reason"] == "expired_ttl"
            assert meta.get("source"), "metadata must carry a source"
