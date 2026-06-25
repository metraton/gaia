#!/usr/bin/env python3
"""Tests for pending approval TTL semantics.

Context: two constants exist in approval_grants.py:
- DEFAULT_GRANT_TTL_MINUTES = 5    (active grant after user approval)
- DEFAULT_PENDING_TTL_MINUTES = 1440 (pending approval waiting for user response)

These must stay separate. The pending TTL (1440 = 24h) is the design:
user has a full day to come back and approve. Reducing it would break
legitimate workflows.

Since the FS-scanner retirement (Task E), the canonical pending store is the
DB (gaia.db).  scan_pending_db() is the sole query surface; it returns all
pending rows regardless of session.  Tests in this class verify that the DB
scanner honours TTL semantics: rejected rows are excluded, and within-TTL
rows are returned.  Expired-row TTL enforcement is covered by
tests/cli/test_approvals.py::TestCmdClean.
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
GAIA_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(GAIA_ROOT))

from modules.security.approval_grants import (
    DEFAULT_GRANT_TTL_MINUTES,
    DEFAULT_PENDING_TTL_MINUTES,
)
from modules.session.pending_scanner import scan_pending_db


# ---------------------------------------------------------------------------
# DB fixture helpers
# ---------------------------------------------------------------------------

def _make_v12_schema(con: sqlite3.Connection) -> None:
    """Apply the minimal approvals schema needed by scan_pending_db."""
    import hashlib

    def _sha256(v):
        return hashlib.sha256((v or "").encode("utf-8")).hexdigest()

    con.create_function("gaia_sha256", 1, _sha256, deterministic=True)
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
    """)


def _insert_pending_row(con: sqlite3.Connection, approval_id: str,
                        session_id: str = "test-session",
                        command: str = "git push origin main",
                        status: str = "pending") -> None:
    """Insert a minimal approvals row directly (bypasses chain for test speed)."""
    payload = json.dumps({
        "operation": "GIT_PUSH command intercepted: push",
        "exact_content": command,
        "scope": "semantic_signature",
        "risk_level": "medium",
        "rollback_hint": "git revert HEAD",
        "rationale": "push requires approval",
        "commands": [command],
    })
    con.execute(
        "INSERT INTO approvals (id, agent_id, session_id, status, payload_json) "
        "VALUES (?, 'test-agent', ?, ?, ?)",
        (approval_id, session_id, status, payload),
    )
    con.commit()


class TestTTLConstants:
    """Regression guards: the two TTL constants must not drift."""

    def test_default_pending_ttl_is_1440(self):
        """Pending TTL is 24h by design — user may come back next day."""
        assert DEFAULT_PENDING_TTL_MINUTES == 1440, (
            f"DEFAULT_PENDING_TTL_MINUTES must be 1440 (24h). "
            f"Got {DEFAULT_PENDING_TTL_MINUTES}. Reducing this would break "
            f"legitimate cross-session approval workflows."
        )

    def test_default_grant_ttl_is_60(self):
        """Grant TTL is 60 min by design (Brief 71, Change 3a).

        The active-grant retry window was widened 5 -> 60 so a cross-session
        human-in-the-loop approval (block under subagent, approve under
        orchestrator, retry under subagent) does not silently expire before it
        can be consumed. It remains SHORT relative to the 24h pending TTL -- the
        two stay distinct (see test_pending_and_grant_ttls_are_distinct).
        """
        assert DEFAULT_GRANT_TTL_MINUTES == 60, (
            f"DEFAULT_GRANT_TTL_MINUTES must be 60 minutes (Change 3a). "
            f"Got {DEFAULT_GRANT_TTL_MINUTES}."
        )

    def test_pending_and_grant_ttls_are_distinct(self):
        """Pending and grant TTLs must remain separate concepts."""
        assert DEFAULT_PENDING_TTL_MINUTES != DEFAULT_GRANT_TTL_MINUTES, (
            "Pending TTL (approval wait time) and grant TTL (active grant "
            "duration) must be different constants. Conflating them breaks "
            "either the approval window or the grant window."
        )


class TestScannerRespectsStoredTTL:
    """DB scanner honours the pending-row TTL contract.

    Note: Expired-row cleanup (rows older than 24h are transitioned to
    'revoked') is exercised by tests/cli/test_approvals.py::TestCmdClean --
    the behaviour lives in cmd_clean(), not in scan_pending_db() itself.
    These tests focus on what scan_pending_db() *returns*: active pending
    rows are surfaced; rejected/revoked rows are excluded.
    """

    def test_pending_row_within_ttl_is_returned_by_db_scanner(self, tmp_path, monkeypatch):
        """A fresh pending row is returned by scan_pending_db().

        This is the DB equivalent of the former 'at_20h_is_preserved' test:
        a row that has not yet been cleaned/expired/rejected must appear in
        the DB scanner results so the user can act on it.
        """
        db_path = tmp_path / "approvals.db"
        con = sqlite3.connect(str(db_path))
        _make_v12_schema(con)
        _insert_pending_row(con, "P-2222222222222222bbbbbbbbbbbbbbbb",
                            command="git push origin main")
        con.close()

        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        results = scan_pending_db()
        assert len(results) == 1, (
            "A fresh pending DB row must be returned by scan_pending_db(); "
            "the scanner must not suppress rows that have not expired."
        )
        assert results[0]["command"] == "git push origin main"

    def test_rejected_pending_is_excluded_from_db_scanner(self, tmp_path, monkeypatch):
        """A row with status='rejected' is excluded from scan_pending_db().

        scan_pending_db() calls list_pending() which queries only status='pending'
        rows, so a rejected row must not appear regardless of its age.
        """
        db_path = tmp_path / "approvals.db"
        con = sqlite3.connect(str(db_path))
        _make_v12_schema(con)
        _insert_pending_row(con, "P-3333333333333333cccccccccccccccc",
                            status="rejected",
                            command="git push origin main")
        con.close()

        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        results = scan_pending_db()
        assert len(results) == 0, (
            "Rejected rows must be excluded from scan_pending_db(); "
            "list_pending() queries status='pending' only."
        )


class TestNoCrossSessionDelete:
    """DB scanner must return pendings regardless of which session created them.

    scan_pending_db() uses all_sessions=True and applies no session filter.
    A pending from 'session-A' must appear when called from 'session-B'.
    """

    def test_cross_session_pending_is_returned_by_db_scanner(self, tmp_path, monkeypatch):
        """Pending from a different session is returned by scan_pending_db().

        This ensures no cross-session filtering silently drops live pendings
        from parallel Claude Code sessions running in the same workspace.
        """
        db_path = tmp_path / "approvals.db"
        con = sqlite3.connect(str(db_path))
        _make_v12_schema(con)
        _insert_pending_row(con, "P-4444444444444444dddddddddddddddd",
                            session_id="other-live-session",
                            command="kubectl delete pod x")
        con.close()

        monkeypatch.setattr(
            "gaia.approvals.store._open_db",
            lambda: sqlite3.connect(str(db_path)),
        )

        results = scan_pending_db()
        assert len(results) == 1, (
            "scan_pending_db() must return pendings from ALL sessions "
            "(all_sessions=True). A pending from 'other-live-session' must "
            "be visible regardless of the current session."
        )
        assert results[0]["pending_session_id"] == "other-live-session"
