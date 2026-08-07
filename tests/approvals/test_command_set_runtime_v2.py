"""Regression coverage for ordered plan-first COMMAND_SET execution."""

from __future__ import annotations

import sqlite3

import pytest

from gaia.approvals.command_set import (
    CommandSetValidationError,
    request_fingerprint,
    validate_request_set,
)
from gaia.store import writer


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript((writer._SCHEMA_PATH).read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _approved_set(db_path):
    commands = ["git push origin main", "docker push registry/app:1"]
    items = validate_request_set(commands)
    result = writer.insert_plan_command_set(
        "P-plan", items, request_fingerprint=request_fingerprint(commands),
        session_id="request-session", db_path=db_path,
    )
    assert result["status"] == "applied"
    return commands


@pytest.mark.parametrize(
    "commands, message",
    [
        (["echo safe", "git push origin main"], "not classified T3"),
        (["git push origin main && docker push x", "git push origin backup"], "atomic"),
        (["vim file", "git push origin main"], "interactive"),
        (["cp x .claude/settings.json", "git push origin main"], "protected"),
        (["rm -rf /", "git push origin main"], "permanently blocked"),
    ],
)
def test_request_set_rejects_ineligible_commands(commands, message):
    with pytest.raises(CommandSetValidationError, match=message):
        validate_request_set(commands)


def test_exact_order_and_post_success_commit(isolated_db):
    first, second = _approved_set(isolated_db)
    assert writer.reserve_plan_command(
        second, session_id="s", tool_use_id="later", db_path=isolated_db
    ) is None
    reservation = writer.reserve_plan_command(
        first, session_id="s", tool_use_id="call-1", db_path=isolated_db
    )
    assert reservation == {"approval_id": "P-plan", "index": 0}
    assert writer.settle_plan_command(
        "P-plan", session_id="wrong", tool_use_id="call-1", success=True,
        db_path=isolated_db,
    ) is False
    assert writer.settle_plan_command(
        "P-plan", session_id="s", tool_use_id="call-1", success=True,
        db_path=isolated_db,
    ) is True
    assert writer.reserve_plan_command(
        second, session_id="s", tool_use_id="call-2", db_path=isolated_db
    )["index"] == 1


def test_failure_freezes_grant_and_preserves_checkpoint(isolated_db):
    first, second = _approved_set(isolated_db)
    writer.reserve_plan_command(first, session_id="s", tool_use_id="call-1", db_path=isolated_db)
    assert writer.settle_plan_command(
        "P-plan", session_id="s", tool_use_id="call-1", success=False,
        failure_reason="exit 7", db_path=isolated_db,
    )
    assert writer.reserve_plan_command(second, session_id="s", tool_use_id="call-2", db_path=isolated_db) is None
    assert writer.reserve_plan_command(first, session_id="s", tool_use_id="call-3", db_path=isolated_db) is None
    con = sqlite3.connect(isolated_db)
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM approval_grants WHERE approval_id='P-plan'").fetchone())
    con.close()
    assert row["status"] == "FAILED"
    assert row["next_index"] == 0
    assert row["failed_index"] == 0
    assert row["consumed_indexes_json"] == "[]"
    assert row["failure_reason"] == "exit 7"


def test_completed_progress_survives_failure_and_new_set_can_continue(isolated_db):
    first, second = _approved_set(isolated_db)
    writer.reserve_plan_command(first, session_id="s", tool_use_id="call-1", db_path=isolated_db)
    writer.settle_plan_command(
        "P-plan", session_id="s", tool_use_id="call-1", success=True, db_path=isolated_db,
    )
    writer.reserve_plan_command(second, session_id="s", tool_use_id="call-2", db_path=isolated_db)
    writer.settle_plan_command(
        "P-plan", session_id="s", tool_use_id="call-2", success=False,
        failure_reason="exit 9", db_path=isolated_db,
    )
    con = sqlite3.connect(isolated_db)
    row = con.execute(
        "SELECT status, consumed_indexes_json, next_index, failed_index FROM approval_grants WHERE approval_id='P-plan'"
    ).fetchone()
    con.close()
    assert row == ("FAILED", "[0]", 1, 1)
    assert writer.reserve_plan_command(second, session_id="s", tool_use_id="old", db_path=isolated_db) is None

    items = validate_request_set([second, "npm publish --tag next"])
    assert writer.insert_plan_command_set(
        "P-continuation", items,
        request_fingerprint=request_fingerprint([second, "npm publish --tag next"]),
        db_path=isolated_db,
    )["status"] == "applied"
    assert writer.reserve_plan_command(
        second, session_id="s", tool_use_id="new", db_path=isolated_db,
    ) == {"approval_id": "P-continuation", "index": 0}


def test_fingerprint_is_order_sensitive_and_exact():
    first = ["git push origin main", "docker push registry/app:1"]
    assert request_fingerprint(first) != request_fingerprint(list(reversed(first)))
    assert request_fingerprint(first) != request_fingerprint([first[0] + " ", first[1]])


def test_duplicate_commands_consume_distinct_ordered_indexes(isolated_db):
    command = "git push origin main"
    items = validate_request_set([command, command])
    writer.insert_plan_command_set(
        "P-duplicates", items, request_fingerprint=request_fingerprint([command, command]),
        db_path=isolated_db,
    )
    for index in (0, 1):
        call_id = f"call-{index}"
        reservation = writer.reserve_plan_command(
            command, session_id="s", tool_use_id=call_id, db_path=isolated_db,
        )
        assert reservation["index"] == index
        assert writer.settle_plan_command(
            "P-duplicates", session_id="s", tool_use_id=call_id,
            success=True, db_path=isolated_db,
        )
    assert writer.reserve_plan_command(
        command, session_id="s", tool_use_id="call-2", db_path=isolated_db,
    ) is None


def test_adapter_without_correlation_fails_closed(isolated_db):
    from modules.tools.bash_validator import BashValidator

    first, _second = _approved_set(isolated_db)
    result = BashValidator()._validate_single_command(first)
    assert not result.allowed
    assert "lacks stable tool-call correlation" in result.reason


def test_reservation_persistence_error_fails_closed(isolated_db, monkeypatch):
    from modules.tools.bash_validator import BashValidator

    first, _second = _approved_set(isolated_db)

    def broken_reservation(*args, **kwargs):
        raise sqlite3.OperationalError("disk unavailable")

    monkeypatch.setattr(writer, "reserve_plan_command", broken_reservation)
    result = BashValidator()._validate_single_command(
        first, session_id="s", tool_use_id="call-1"
    )
    assert not result.allowed
    assert "persistence failed closed" in result.reason
