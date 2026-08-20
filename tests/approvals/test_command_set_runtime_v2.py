"""Regression coverage for ordered plan-first COMMAND_SET execution."""

from __future__ import annotations

import json
import sqlite3

import pytest

from gaia.approvals.command_set import (
    CommandSetValidationError,
    command_fingerprint,
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


def test_single_command_request_set_full_lifecycle(isolated_db):
    """A set of one behaves through the whole plan-first lifecycle -- entrance,
    fingerprinting, reservation, and consumption -- exactly like a longer set,
    not merely passing the entrance check."""
    command = "git push origin main"
    items = validate_request_set([command])
    assert len(items) == 1
    assert items[0]["command"] == command

    result = writer.insert_plan_command_set(
        "P-single", items, request_fingerprint=request_fingerprint([command]),
        session_id="request-session", db_path=isolated_db,
    )
    assert result["status"] == "applied"

    reservation = writer.reserve_plan_command(
        command, session_id="s", tool_use_id="call-1", db_path=isolated_db,
    )
    assert reservation == {"approval_id": "P-single", "index": 0}
    assert writer.settle_plan_command(
        "P-single", session_id="s", tool_use_id="call-1", success=True,
        db_path=isolated_db,
    ) is True

    con = sqlite3.connect(isolated_db)
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT * FROM approval_grants WHERE approval_id='P-single'").fetchone())
    con.close()
    assert row["status"] == "CONSUMED"
    assert row["next_index"] == 1
    assert json.loads(row["consumed_indexes_json"]) == [0]

    # Replay protection: the same command no longer reserves once consumed.
    assert writer.reserve_plan_command(
        command, session_id="s", tool_use_id="call-2", db_path=isolated_db,
    ) is None


def test_request_set_rejects_empty_list():
    with pytest.raises(CommandSetValidationError, match="at least one command"):
        validate_request_set([])


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


def _grant_row(db_path, approval_id="P-plan"):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(
            con.execute(
                "SELECT * FROM approval_grants WHERE approval_id=?", (approval_id,)
            ).fetchone()
        )
    finally:
        con.close()


def _set_stored_fingerprint(db_path, index, fingerprint, approval_id="P-plan"):
    """Rewrite one stored per-item fingerprint, leaving the command bytes intact."""
    row = _grant_row(db_path, approval_id)
    items = json.loads(row["command_set_json"])
    items[index]["fingerprint"] = fingerprint
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE approval_grants SET command_set_json=? WHERE approval_id=?",
            (json.dumps(items), approval_id),
        )
        con.commit()
    finally:
        con.close()


def test_reservation_refuses_altered_bytes_and_an_altered_stored_fingerprint(isolated_db):
    """Byte-identity is fail-closed at RESERVATION, not only in the fingerprint function.

    test_fingerprint_is_order_sensitive_and_exact exercises request_fingerprint as a
    pure function and never reaches a grant. The property under test here is the
    refusal, so every assertion goes through reserve_plan_command against a real
    stored set, and each refusal is paired with the positive control that isolates
    what caused it.
    """
    first, second = _approved_set(isolated_db)

    off_by_one = (
        first + " ",              # one trailing byte
        first.replace("main", "maim"),  # one substituted letter
        first[:-1],               # one byte dropped
    )
    for altered in off_by_one:
        assert altered != first
        assert writer.reserve_plan_command(
            altered, session_id="s", tool_use_id="call-off", db_path=isolated_db,
        ) is None

    # A refused reservation leaves no stamp behind, so the control below starts
    # from the same state the refusals did.
    refused = _grant_row(isolated_db)
    assert refused["reservation_tool_use_id"] is None
    assert refused["next_index"] == 0
    assert refused["status"] == "PENDING"

    # An altered STORED fingerprint is refused even though the caller's bytes are
    # exact: the fingerprint is a second, independent gate on the same item.
    true_fingerprint = command_fingerprint(first)
    _set_stored_fingerprint(isolated_db, 0, command_fingerprint(second))
    assert writer.reserve_plan_command(
        first, session_id="s", tool_use_id="call-tampered", db_path=isolated_db,
    ) is None

    # Restoring only that one field makes the identical call succeed, which is
    # what proves the refusal above came from the fingerprint and from nothing
    # else about the grant.
    _set_stored_fingerprint(isolated_db, 0, true_fingerprint)
    assert writer.reserve_plan_command(
        first, session_id="s", tool_use_id="call-exact", db_path=isolated_db,
    ) == {"approval_id": "P-plan", "index": 0}


def test_an_outstanding_reservation_refuses_a_second_attempt_on_the_same_grant(isolated_db):
    """Mutual exclusion, proven by the outstanding-reservation guard specifically.

    A refusal that came from an index mismatch would satisfy the letter of the
    claim and miss it entirely, so the branch is isolated twice: the grant state
    is shown to make the item-match filter pass, and clearing ONLY the
    reservation stamp -- next_index and command bytes untouched -- turns the same
    refused call into a success while an index mismatch keeps refusing.
    """
    first, second = _approved_set(isolated_db)
    assert writer.reserve_plan_command(
        first, session_id="s", tool_use_id="call-1", db_path=isolated_db,
    ) == {"approval_id": "P-plan", "index": 0}

    # The state that rules the item-mismatch branch out for the attempts below:
    # next_index still addresses index 0, and the item stored at that index is
    # byte- and fingerprint-identical to what the second attempt passes.
    held = _grant_row(isolated_db)
    assert held["reservation_index"] == 0
    assert held["reservation_session_id"] == "s"
    assert held["reservation_tool_use_id"] == "call-1"
    assert held["next_index"] == 0
    assert held["status"] == "PENDING"
    item = json.loads(held["command_set_json"])[0]
    assert item["command"] == first
    assert item["fingerprint"] == command_fingerprint(first)

    for session_id, tool_use_id in (("s", "call-2"), ("other", "call-3")):
        assert writer.reserve_plan_command(
            first, session_id=session_id, tool_use_id=tool_use_id, db_path=isolated_db,
        ) is None

    # The guard rolls back rather than half-writing: the first holder still owns
    # the reservation and nothing advanced.
    assert _grant_row(isolated_db) == held

    con = sqlite3.connect(isolated_db)
    try:
        con.execute(
            "UPDATE approval_grants SET reservation_index=NULL, "
            "reservation_session_id=NULL, reservation_tool_use_id=NULL, "
            "reservation_at=NULL WHERE approval_id='P-plan'"
        )
        con.commit()
    finally:
        con.close()

    released = _grant_row(isolated_db)
    assert released["next_index"] == held["next_index"]
    assert released["command_set_json"] == held["command_set_json"]

    # Same call, same grant, one field of difference: refused while the stamp was
    # set, accepted once it is not. Meanwhile the index-mismatch branch is
    # unaffected by the stamp, so the two refusals are not the same refusal.
    assert writer.reserve_plan_command(
        second, session_id="s", tool_use_id="call-2", db_path=isolated_db,
    ) is None
    assert writer.reserve_plan_command(
        first, session_id="s", tool_use_id="call-2", db_path=isolated_db,
    ) == {"approval_id": "P-plan", "index": 0}
