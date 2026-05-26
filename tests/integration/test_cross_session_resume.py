"""Integration test for T3.3 -- cross-session approval grant.

Verifies the end-to-end flow:
  1. Session S1 inserts a REQUESTED approval (pending).
  2. gaia approvals list (pending cmd) shows it from S2 via all_sessions=True.
  3. gaia approvals approve <id> from S2 (mocked CLI) transitions to approved.
  4. The APPROVED event is stored with S2's session_id.
  5. The approval is no longer in list_pending() output.
  6. store.get_by_id() returns the approval with status='approved'.

Satisfies: AC-7 from brief
    approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Task: T3.3 (M3, Wave 3)

AC test command:
    cd gaia && python -m pytest tests/integration/test_cross_session_resume.py -q
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.approvals.store import (  # noqa: E402
    approve,
    get_by_id,
    insert_requested,
    list_pending,
    revoke,
)


# ---------------------------------------------------------------------------
# In-memory DB factory
# ---------------------------------------------------------------------------

def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _make_v12_db() -> sqlite3.Connection:
    """In-memory DB with v12 approval tables."""
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
    "operation": "Deploy service v2",
    "exact_content": "kubectl apply -f deploy.yaml",
    "scope": "k8s/production/service",
    "risk_level": "high",
    "rollback_hint": "kubectl rollout undo deployment/service",
    "rationale": "Deploy new version",
    "commands": ["kubectl apply -f deploy.yaml"],
}

SESSION_S1 = "session-requester-001"
SESSION_S2 = "session-approver-002"


# ---------------------------------------------------------------------------
# Cross-session approval flow
# ---------------------------------------------------------------------------

class TestCrossSessionResumeFlow:
    """Full insert -> list -> approve cross-session flow."""

    def test_s1_pending_visible_from_s2(self):
        """Approval created in S1 appears in list_pending(all_sessions=True) for S2."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )

        # S2 queries all sessions.
        pending = list_pending(all_sessions=True, con=con)
        ids = {r["id"] for r in pending}
        assert approval_id in ids, "S1 approval must be visible cross-session"

    def test_s1_pending_not_in_s2_session_scoped_view(self):
        """S1 approval does not appear in S2's session-scoped view."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )

        pending_s2 = list_pending(all_sessions=False, session_id=SESSION_S2, con=con)
        ids = {r["id"] for r in pending_s2}
        assert approval_id not in ids, "S1 approval must NOT appear in S2's session view"

    def test_s2_approve_transitions_s1_pending(self):
        """S2 can approve a pending approval created by S1."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )

        # S2 approves.
        approve(approval_id, approver_session=SESSION_S2, con=con)

        row = get_by_id(approval_id, con=con)
        assert row is not None, "Approval must still exist after approve()"
        assert row["status"] == "approved", "Status must be 'approved' after S2 approve"

    def test_approved_stores_s2_session_id(self):
        """The APPROVED event records S2's session_id, not S1's."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )
        approve(approval_id, approver_session=SESSION_S2, con=con)

        row = con.execute(
            "SELECT session_id FROM approval_events "
            "WHERE approval_id = ? AND event_type = 'APPROVED'",
            (approval_id,),
        ).fetchone()
        assert row is not None
        assert row[0] == SESSION_S2, (
            f"APPROVED event must record S2 session_id={SESSION_S2!r}, got {row[0]!r}"
        )

    def test_approved_no_longer_in_list_pending(self):
        """After S2 approves, the approval no longer appears in list_pending."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )
        approve(approval_id, approver_session=SESSION_S2, con=con)

        pending = list_pending(all_sessions=True, con=con)
        ids = {r["id"] for r in pending}
        assert approval_id not in ids, "Approved approval must not appear in pending list"

    def test_get_by_id_returns_approved_row(self):
        """get_by_id() returns the approval with status='approved'."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )
        approve(approval_id, approver_session=SESSION_S2, con=con)

        row = get_by_id(approval_id, con=con)
        assert row is not None
        assert row["status"] == "approved"
        assert row["id"] == approval_id

    def test_get_by_id_returns_none_for_unknown_id(self):
        """get_by_id() returns None when approval_id does not exist."""
        con = _make_v12_db()
        result = get_by_id("P-nonexistent-abc", con=con)
        assert result is None

    def test_approve_twice_raises_value_error(self):
        """Calling approve() on an already-approved approval raises ValueError."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )
        approve(approval_id, approver_session=SESSION_S2, con=con)

        with pytest.raises(ValueError, match="expected status"):
            approve(approval_id, approver_session="session-s3", con=con)

    def test_revoke_pending_from_s2(self):
        """S2 can revoke a pending approval created by S1."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )
        revoke(approval_id, revoker_session=SESSION_S2, con=con)

        row = get_by_id(approval_id, con=con)
        assert row["status"] == "revoked", "Status must be 'revoked' after revoke()"

    def test_revoke_inserts_revoked_event(self):
        """revoke() inserts a REVOKED event in the approval_events chain."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="agent-s1", session_id=SESSION_S1, con=con
        )
        revoke(approval_id, revoker_session=SESSION_S2, con=con)

        events = con.execute(
            "SELECT event_type FROM approval_events "
            "WHERE approval_id = ? ORDER BY id ASC",
            (approval_id,),
        ).fetchall()
        types = [e[0] for e in events]
        assert "REVOKED" in types, "REVOKED event must be in the chain"

    def test_multiple_sessions_pending_visible_from_third(self):
        """list_pending(all_sessions=True) shows approvals from multiple sessions."""
        con = _make_v12_db()
        id_s1 = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id=SESSION_S1, con=con
        )
        id_s2 = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id=SESSION_S2, con=con
        )
        # S3 queries all.
        pending = list_pending(all_sessions=True, con=con)
        ids = {r["id"] for r in pending}
        assert id_s1 in ids
        assert id_s2 in ids


# ---------------------------------------------------------------------------
# cmd_approve CLI handler tests
# ---------------------------------------------------------------------------

class TestCmdApprove:
    """Tests for the cmd_approve CLI handler using monkeypatching."""

    def _make_args(self, approval_id, **kwargs):
        return argparse.Namespace(
            approval_id=approval_id,
            yes=kwargs.get("yes", False),
            json=kwargs.get("json", False),
        )

    def test_approve_unknown_id_returns_1(self):
        """cmd_approve returns 1 when approval_id is not found."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_approve

        args = self._make_args("P-nonexistent")
        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = None
            mock_store.return_value = store_mock
            rc = cmd_approve(args)
        assert rc == 1

    def test_approve_non_pending_returns_1(self):
        """cmd_approve returns 1 when approval is not in pending status."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_approve

        args = self._make_args("P-abc123", yes=True)
        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = {
                "id": "P-abc123",
                "status": "approved",
                "payload_json": json.dumps(_SAMPLE_PAYLOAD),
            }
            mock_store.return_value = store_mock
            rc = cmd_approve(args)
        assert rc == 1

    def test_approve_with_yes_calls_store_approve(self):
        """cmd_approve --yes calls store.approve() without prompting."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_approve

        args = self._make_args("P-abc123", yes=True)
        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = {
                "id": "P-abc123",
                "status": "pending",
                "payload_json": json.dumps(_SAMPLE_PAYLOAD),
            }
            mock_store.return_value = store_mock
            rc = cmd_approve(args)
        assert rc == 0
        store_mock.approve.assert_called_once()
        call_kwargs = store_mock.approve.call_args
        assert call_kwargs[0][0] == "P-abc123"

    def test_approve_json_output(self, capsys):
        """cmd_approve --json outputs JSON with status and approval_id."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_approve

        args = self._make_args("P-abc123", yes=True, json=True)
        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = {
                "id": "P-abc123",
                "status": "pending",
                "payload_json": json.dumps(_SAMPLE_PAYLOAD),
            }
            mock_store.return_value = store_mock
            rc = cmd_approve(args)

        assert rc == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "approved"
        assert result["approval_id"] == "P-abc123"
