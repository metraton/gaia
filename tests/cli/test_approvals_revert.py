"""Tests for T3.4 -- gaia approvals revert command.

Covers:
  1. derive_inverse() for gaia brief set-status transitions
  2. derive_inverse() for git branch create -> delete
  3. derive_inverse() for NOT REVERSIBLE commands
  4. derive_inverses_for_approval() -- empty when no EXECUTED events
  5. derive_inverses_for_approval() -- one InverseCommand per EXECUTED event
  6. cmd_revert -- no EXECUTED events, exits 0 with message
  7. cmd_revert --dry-run -- shows inverse commands without prompting
  8. cmd_revert --yes -- skips confirmation, executes (mocked subprocess)
  9. cmd_revert -- invalid approval_id returns 1
 10. display module: format_age covers all time ranges

Satisfies: AC-8 from brief
    approval-model-redesign-user-in-loop-fingerprint-bound-hash-chained
Task: T3.4 (M3, Wave 3)

AC test command:
    cd gaia && python -m pytest tests/cli/test_approvals_revert.py -q
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.approvals.revert import (  # noqa: E402
    InverseCommand,
    derive_inverse,
    derive_inverses_for_approval,
)
from gaia.approvals.display import format_age  # noqa: E402
from gaia.approvals.store import insert_requested, record_event  # noqa: E402


# ---------------------------------------------------------------------------
# In-memory DB factory (mirrors test_approvals_pending_cross_session.py)
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
    "rollback_hint": None,
    "rationale": "Branch is merged and stale",
    "commands": ["git branch feature/cleanup"],
}

_BRIEF_STATUS_PAYLOAD = {
    "operation": "Close brief",
    "exact_content": "gaia brief set-status 42 done",
    "scope": "brief/42",
    "risk_level": "low",
    "rollback_hint": "gaia brief set-status 42 pending",
    "rationale": "Brief completed",
    "commands": ["gaia brief set-status 42 done"],
}


# ---------------------------------------------------------------------------
# derive_inverse() unit tests
# ---------------------------------------------------------------------------

class TestDeriveInverse:
    """Unit tests for the derive_inverse() function."""

    def test_brief_set_status_done_inverts_to_pending(self):
        """gaia brief set-status <id> done -> gaia brief set-status <id> pending"""
        event = {
            "id": 1,
            "payload_json": json.dumps({
                "commands": ["gaia brief set-status 42 done"],
                "scope": "brief/42",
                "operation": "Close brief",
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is True
        assert ic.inverse_command == "gaia brief set-status 42 pending"

    def test_brief_set_status_active_inverts_to_draft(self):
        """gaia brief set-status <id> active -> gaia brief set-status <id> draft"""
        event = {
            "id": 2,
            "payload_json": json.dumps({
                "commands": ["gaia brief set-status 5 active"],
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is True
        assert ic.inverse_command == "gaia brief set-status 5 draft"

    def test_git_branch_create_inverts_to_delete(self):
        """git branch <name> -> git branch -D <name>"""
        event = {
            "id": 3,
            "payload_json": json.dumps({
                "commands": ["git branch feature/new-thing"],
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is True
        assert ic.inverse_command == "git branch -D feature/new-thing"

    def test_rm_command_is_not_reversible(self):
        """rm commands have no safe inverse."""
        event = {
            "id": 4,
            "payload_json": json.dumps({
                "commands": ["rm /some/file.txt"],
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is False
        assert ic.inverse_command is None
        assert "NOT REVERSIBLE" in ic.notes

    def test_unknown_command_is_not_reversible(self):
        """Commands with no matching rule are NOT REVERSIBLE."""
        event = {
            "id": 5,
            "payload_json": json.dumps({
                "commands": ["kubectl apply -f manifests/"],
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is False
        assert ic.inverse_command is None

    def test_no_commands_field_uses_exact_content(self):
        """When 'commands' is absent, exact_content is split by newlines."""
        event = {
            "id": 6,
            "payload_json": json.dumps({
                "exact_content": "gaia brief set-status 10 done",
                "operation": "Close brief 10",
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is True
        assert ic.inverse_command == "gaia brief set-status 10 pending"

    def test_empty_payload_returns_not_reversible(self):
        """Events with no command data yield NOT REVERSIBLE."""
        event = {
            "id": 7,
            "payload_json": json.dumps({}),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is False

    def test_null_payload_returns_not_reversible(self):
        """Events with null payload_json yield NOT REVERSIBLE."""
        event = {
            "id": 8,
            "payload_json": None,
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.reversible is False

    def test_event_id_is_preserved(self):
        """The returned InverseCommand.event_id matches the input event id."""
        event = {
            "id": 99,
            "payload_json": json.dumps({
                "commands": ["gaia brief set-status 1 done"],
            }),
            "metadata_json": None,
        }
        ic = derive_inverse(event)
        assert ic.event_id == 99


# ---------------------------------------------------------------------------
# derive_inverses_for_approval() integration tests
# ---------------------------------------------------------------------------

class TestDeriveInversesForApproval:
    """Tests using in-memory DB to verify derive_inverses_for_approval()."""

    def test_empty_when_no_executed_events(self):
        """Returns empty list when approval has only REQUESTED events."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _SAMPLE_PAYLOAD, agent_id="ag", session_id="s", con=con
        )
        inverses = derive_inverses_for_approval(approval_id, con)
        assert inverses == [], "No EXECUTED events means empty inverses list"

    def test_one_inverse_per_executed_event(self):
        """Each EXECUTED event produces one InverseCommand."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _BRIEF_STATUS_PAYLOAD, agent_id="ag", session_id="s", con=con
        )
        # Insert an EXECUTED event manually.
        record_event(
            approval_id,
            "EXECUTED",
            session_id="s",
            payload_json=json.dumps(_BRIEF_STATUS_PAYLOAD),
            con=con,
        )
        inverses = derive_inverses_for_approval(approval_id, con)
        assert len(inverses) == 1
        assert inverses[0].reversible is True
        assert "pending" in inverses[0].inverse_command

    def test_two_executed_events_give_two_inverses(self):
        """Two EXECUTED events produce two InverseCommand entries."""
        con = _make_v12_db()
        approval_id = insert_requested(
            _BRIEF_STATUS_PAYLOAD, agent_id="ag", session_id="s", con=con
        )
        for _ in range(2):
            record_event(
                approval_id,
                "EXECUTED",
                session_id="s",
                payload_json=json.dumps(_BRIEF_STATUS_PAYLOAD),
                con=con,
            )
        inverses = derive_inverses_for_approval(approval_id, con)
        assert len(inverses) == 2


# ---------------------------------------------------------------------------
# format_age() unit tests
# ---------------------------------------------------------------------------

class TestFormatAge:
    """Tests for display.format_age()."""

    def test_seconds_range(self):
        assert format_age(30) == "30s"

    def test_minutes_range(self):
        assert format_age(90) == "1m"

    def test_hours_range(self):
        assert format_age(7200) == "2h"

    def test_days_range(self):
        assert format_age(86400 * 3) == "3d"

    def test_zero(self):
        assert format_age(0) == "0s"


# ---------------------------------------------------------------------------
# cmd_revert CLI tests
# ---------------------------------------------------------------------------

class TestCmdRevert:
    """Tests for the cmd_revert CLI handler."""

    def _make_args(self, approval_id, **kwargs):
        ns = argparse.Namespace(
            approval_id=approval_id,
            yes=kwargs.get("yes", False),
            dry_run=kwargs.get("dry_run", False),
            file=kwargs.get("file", None),
            json=kwargs.get("json", False),
        )
        return ns

    def test_revert_unknown_approval_returns_1(self, tmp_path):
        """cmd_revert returns 1 when approval_id does not exist."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_revert

        args = self._make_args("P-nonexistent")
        # Monkeypatch store to return None.
        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = None
            mock_store.return_value = store_mock
            rc = cmd_revert(args)
        assert rc == 1

    def test_revert_no_executed_events_exits_0(self, tmp_path):
        """cmd_revert exits 0 with message when no EXECUTED events found."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_revert

        args = self._make_args("P-abc123", dry_run=True)

        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = {
                "id": "P-abc123",
                "status": "approved",
                "payload_json": json.dumps(_SAMPLE_PAYLOAD),
            }
            store_mock._open_db.return_value = MagicMock()
            mock_store.return_value = store_mock

            # Mock _import_approval_revert to return a module with derive_inverses_for_approval.
            with patch("cli.approvals._import_approval_revert") as mock_revert:
                revert_mod_mock = MagicMock()
                revert_mod_mock.derive_inverses_for_approval.return_value = []
                mock_revert.return_value = revert_mod_mock
                rc = cmd_revert(args)
        assert rc == 0

    def test_revert_dry_run_prints_commands_without_exec(self, tmp_path, capsys):
        """cmd_revert --dry-run prints inverse commands without executing."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_revert

        args = self._make_args("P-abc123", dry_run=True)
        fake_inverse = InverseCommand(
            event_id=1,
            original_command="gaia brief set-status 5 done",
            inverse_command="gaia brief set-status 5 pending",
            reversible=True,
            notes="Derived from pattern",
        )

        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = {
                "id": "P-abc123",
                "status": "executed",
                "payload_json": json.dumps(_BRIEF_STATUS_PAYLOAD),
            }
            store_mock._open_db.return_value = MagicMock()
            mock_store.return_value = store_mock

            with patch("cli.approvals._import_approval_revert") as mock_revert:
                revert_mod_mock = MagicMock()
                revert_mod_mock.derive_inverses_for_approval.return_value = [fake_inverse]
                mock_revert.return_value = revert_mod_mock
                rc = cmd_revert(args)

        captured = capsys.readouterr()
        assert rc == 0
        assert "[dry-run]" in captured.out
        assert "gaia brief set-status 5 pending" in captured.out

    def test_revert_yes_skips_confirm_and_executes(self, tmp_path, capsys):
        """cmd_revert --yes skips confirmation and executes inverse commands."""
        sys.path.insert(0, str(_REPO_ROOT / "bin"))
        from cli.approvals import cmd_revert

        args = self._make_args("P-abc123", yes=True)
        fake_inverse = InverseCommand(
            event_id=1,
            original_command="gaia brief set-status 5 done",
            inverse_command="echo 'inverse'",
            reversible=True,
            notes="Test",
        )

        with patch("cli.approvals._import_approval_store") as mock_store:
            store_mock = MagicMock()
            store_mock.get_by_id.return_value = {
                "id": "P-abc123",
                "status": "executed",
                "payload_json": json.dumps(_BRIEF_STATUS_PAYLOAD),
            }
            store_mock._open_db.return_value = MagicMock()
            mock_store.return_value = store_mock

            with patch("cli.approvals._import_approval_revert") as mock_revert:
                revert_mod_mock = MagicMock()
                revert_mod_mock.derive_inverses_for_approval.return_value = [fake_inverse]
                mock_revert.return_value = revert_mod_mock

                import subprocess
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.stdout = ""
                mock_proc.stderr = ""
                with patch("subprocess.run", return_value=mock_proc):
                    rc = cmd_revert(args)

        assert rc == 0
