"""A grant outlives a failed run of its own command and nothing else.

Pins four behaviors of the grant lifecycle: a single-command grant returns to
PENDING when its command exits non-zero inside the window, and then serves only
those bytes in that directory; a grant sealed for one directory is refused from
another; a terminal grant is never moved by the writers that only advance a
PENDING one; and request-set refuses a directory its shell cannot enter.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (_ROOT, _ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.approvals import store  # noqa: E402
from gaia.approvals.command_set import request_fingerprint, validate_request_set  # noqa: E402
from gaia.approvals.core import close_call, grant_lookup_filter  # noqa: E402
from gaia.store import writer  # noqa: E402
from modules.security.approval_scopes import build_approval_signature  # noqa: E402
from modules.tools.bash_validator import _build_sealed_payload  # noqa: E402

SESSION = "ses-survival"
AGENT = "agent-survival"
COMMAND = "git push origin feat/survival"
OTHER_COMMAND = "git push origin main"
SEALED_DIR = "/work/sealed"
OTHER_DIR = "/work/other"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "gaia.db"
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_DB", str(db_path))
    con = sqlite3.connect(db_path)
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return db_path


def _grant_for(command: str = COMMAND, cwd: str = SEALED_DIR) -> str:
    """An approved single-command grant sealed to ``cwd`` for this requester."""
    payload = _build_sealed_payload(
        command, verb="push", category="MUTATIVE",
        agent_type=AGENT, session_id=SESSION, cwd=cwd,
    )
    approval_id = store.insert_requested(payload, agent_id=AGENT, session_id=SESSION)
    signature = build_approval_signature(command, danger_verb="push", danger_category="MUTATIVE")
    result = writer.insert_semantic_grant(
        approval_id, command, signature.to_dict(), agent_id=AGENT, session_id=SESSION,
    )
    assert result["status"] == "applied"
    return approval_id


def _lookup(command: str, cwd: str):
    requester = grant_lookup_filter(cwd=cwd, session_id=SESSION, agent_id=AGENT)
    return writer.check_db_semantic_grant(command, requester=requester)


def _status(db_path: Path, approval_id: str) -> str:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT status FROM approval_grants WHERE approval_id=?", (approval_id,)
        ).fetchone()[0]
    finally:
        con.close()


def _set_status(db_path: Path, approval_id: str, status: str) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("UPDATE approval_grants SET status=? WHERE approval_id=?", (status, approval_id))
        con.commit()
    finally:
        con.close()


def _run_and_close(approval_id: str, exit_code: int, command: str = COMMAND) -> str:
    assert writer.consume_db_semantic_grant(approval_id)
    return close_call(
        approval_id, command=command, session_id=SESSION, tool_use_id="call-1",
        exit_code=exit_code, reserved=False, terminal_event="PostToolUse",
    )


class TestSealedDirectory:
    """Regression rows: a directory-sealed grant already refuses another directory."""

    def test_grant_matches_only_from_the_directory_it_was_sealed_for(self, isolated_db):
        _grant_for()

        assert _lookup(COMMAND, SEALED_DIR) is not None
        assert _lookup(COMMAND, OTHER_DIR) is None

    def test_grant_matches_only_the_requester_it_was_sealed_for(self, isolated_db):
        _grant_for()

        stranger = grant_lookup_filter(cwd=SEALED_DIR, session_id=SESSION, agent_id="agent-other")
        assert writer.check_db_semantic_grant(COMMAND, requester=stranger) is None


class TestGrantSurvivesFailure:
    def test_failed_run_returns_the_grant_to_pending(self, isolated_db):
        approval_id = _grant_for()

        assert _run_and_close(approval_id, exit_code=1) == "failed"

        assert _status(isolated_db, approval_id) == "PENDING"
        assert _lookup(COMMAND, SEALED_DIR) is not None

    def test_restored_grant_serves_neither_another_command_nor_another_directory(self, isolated_db):
        approval_id = _grant_for()
        _run_and_close(approval_id, exit_code=1)

        assert _lookup(OTHER_COMMAND, SEALED_DIR) is None
        assert _lookup(COMMAND, OTHER_DIR) is None

    def test_successful_run_still_spends_the_grant(self, isolated_db):
        approval_id = _grant_for()

        assert _run_and_close(approval_id, exit_code=0) == "executed"

        assert _status(isolated_db, approval_id) == "CONSUMED"
        assert _lookup(COMMAND, SEALED_DIR) is None

    def test_failure_after_the_window_does_not_revive_the_grant(self, isolated_db):
        approval_id = _grant_for()
        assert writer.consume_db_semantic_grant(approval_id)
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        con = sqlite3.connect(isolated_db)
        con.execute("UPDATE approval_grants SET expires_at=? WHERE approval_id=?", (past, approval_id))
        con.commit()
        con.close()

        close_call(
            approval_id, command=COMMAND, session_id=SESSION, tool_use_id="call-1",
            exit_code=1, reserved=False, terminal_event="PostToolUse",
        )

        assert _status(isolated_db, approval_id) == "CONSUMED"


class TestTerminalGrantStaysTerminal:
    @pytest.fixture
    def plan_grant(self, isolated_db):
        commands = [COMMAND, OTHER_COMMAND]
        result = writer.insert_plan_command_set(
            "P-terminal", validate_request_set(commands),
            request_fingerprint=request_fingerprint(commands),
            session_id=SESSION, db_path=isolated_db,
        )
        assert result["status"] == "applied"
        return "P-terminal"

    @pytest.mark.parametrize("terminal", ["REVOKED", "EXPIRED"])
    def test_marking_an_item_consumed_leaves_a_terminal_grant_as_it_ended(
        self, isolated_db, plan_grant, terminal,
    ):
        _set_status(isolated_db, plan_grant, terminal)

        for index in (0, 1):
            result = writer.mark_command_set_item_consumed(plan_grant, index, db_path=isolated_db)
            assert result["status"] == "error"

        assert _status(isolated_db, plan_grant) == terminal

    @pytest.mark.parametrize("terminal", ["REVOKED", "CONSUMED"])
    def test_status_update_does_not_move_a_terminal_grant(self, isolated_db, plan_grant, terminal):
        _set_status(isolated_db, plan_grant, terminal)

        result = writer.update_approval_grant_status(plan_grant, "EXPIRED", db_path=isolated_db)

        assert result["status"] == "error"
        assert _status(isolated_db, plan_grant) == terminal

    def test_status_update_never_returns_a_grant_to_pending(self, isolated_db, plan_grant):
        _set_status(isolated_db, plan_grant, "REVOKED")

        result = writer.update_approval_grant_status(plan_grant, "PENDING", db_path=isolated_db)

        assert result["status"] == "error"
        assert _status(isolated_db, plan_grant) == "REVOKED"

    def test_status_update_still_expires_a_pending_grant(self, isolated_db, plan_grant):
        result = writer.update_approval_grant_status(plan_grant, "EXPIRED", db_path=isolated_db)

        assert result["status"] == "applied"
        assert _status(isolated_db, plan_grant) == "EXPIRED"


class TestRequestSetDirectory:
    @staticmethod
    def _items(cwd: str):
        from bin.cli.approvals import _request_set_items

        return _request_set_items(argparse.Namespace(
            command=[COMMAND], cwd=[cwd], expect_exit=None, does=None, impact=None,
        ))

    def test_refuses_a_directory_that_does_not_exist(self, tmp_path):
        with pytest.raises(ValueError, match="not an existing directory"):
            self._items(str(tmp_path / "missing"))

    @pytest.mark.skipif(os.geteuid() == 0, reason="root enters any directory")
    def test_refuses_a_directory_the_shell_cannot_enter(self, tmp_path):
        sealed = tmp_path / "closed"
        sealed.mkdir()
        sealed.chmod(0o600)
        try:
            with pytest.raises(ValueError, match="cannot be entered"):
                self._items(str(sealed))
        finally:
            sealed.chmod(0o700)

    def test_accepts_an_existing_directory(self, tmp_path):
        assert self._items(str(tmp_path))[0]["cwd"] == str(tmp_path)
