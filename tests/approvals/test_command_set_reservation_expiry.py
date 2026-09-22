"""A COMMAND_SET reservation the host never let execute must not freeze the grant.

The measured incident: Gaia reserved index 0 of an approved set, the HOST refused
the command downstream of that allow, so settle_plan_command never ran. The
reservation slot stayed taken forever, every legitimate retry was refused, and
the approval's event chain showed only REQUESTED/SHOWN/APPROVED -- indistinguish-
able from a set nobody ever tried to run.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from gaia.approvals.command_set import request_fingerprint, validate_request_set
from gaia.approvals.store import insert_requested
from gaia.store import writer

COMMANDS = ["git push origin main", "docker push registry/app:1", "gh release create v1"]

# Aging by a day rather than by the TTL constant keeps these assertions about
# BEHAVIOUR: they fail because a stale reservation is not released, not because a
# constant is missing. The constant's own value is pinned by its own test below.
STALE_MINUTES = 24 * 60

RESERVATION_ABANDONED_REASON_CODE = "reservation_abandoned_unexecuted"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    # GAIA_DATA_DIR (not GAIA_DB) so the event write, which resolves its own
    # connection, lands in the same substrate as the reservation write.
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


@pytest.fixture
def host_blocked_grant(isolated_db):
    """Reproduce the exact incident state: consented, reserved, never settled."""
    # The approval row and its REQUESTED/SHOWN/APPROVED chain are not decoration:
    # approval_events carries a real FK to approvals(id), so a grant without its
    # parent approval cannot be written about at all.
    approval_id = insert_requested(
        {"scope": "COMMAND_SET", "commands": COMMANDS},
        agent_id="a0000000000000000",
        session_id="ses-original",
        approval_id="P-plan",
    )
    assert approval_id == "P-plan"
    con = sqlite3.connect(isolated_db)
    try:
        from gaia.approvals.chain import insert_event

        for event in ("SHOWN", "APPROVED"):
            insert_event(con, "P-plan", event, session_id="ses-original")
        con.commit()
    finally:
        con.close()

    items = validate_request_set(COMMANDS)
    assert writer.insert_plan_command_set(
        "P-plan", items, request_fingerprint=request_fingerprint(COMMANDS),
        session_id="ses-original", db_path=isolated_db,
    )["status"] == "applied"
    assert writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-original", tool_use_id="call-blocked-by-host",
        db_path=isolated_db,
    ) == {"approval_id": "P-plan", "index": 0}
    return isolated_db


def _grant(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT * FROM approval_grants WHERE approval_id='P-plan'"
        ).fetchone())
    finally:
        con.close()


def _age_reservation(db_path, minutes):
    from datetime import datetime, timedelta, timezone

    stamp = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE approval_grants SET reservation_at=? WHERE approval_id='P-plan'",
            (stamp,),
        )
        con.commit()
    finally:
        con.close()


def _events(db_path, event_type=None):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM approval_events WHERE approval_id='P-plan' ORDER BY id"
        )]
    finally:
        con.close()
    if event_type is not None:
        rows = [r for r in rows if r["event_type"] == event_type]
    return rows


def test_the_reservation_window_sits_inside_the_grant_window():
    """A stuck index must cost part of the grant's authority, never all of it."""
    assert 0 < writer.PLAN_COMMAND_RESERVATION_TTL_MINUTES < writer.PLAN_COMMAND_SET_TTL_MINUTES


def test_incident_state_is_reproduced(host_blocked_grant):
    """Guard the premise: without the fix this is a grant that cannot advance."""
    grant = _grant(host_blocked_grant)
    assert grant["reservation_tool_use_id"] == "call-blocked-by-host"
    assert grant["next_index"] == 0
    assert grant["status"] == "PENDING"
    # Neither advanced nor recorded as failed -- the shape the incident left.
    assert grant["failed_index"] is None
    assert grant["failure_reason"] is None


def test_stale_reservation_releases_and_a_fresh_retry_executes(host_blocked_grant):
    _age_reservation(host_blocked_grant, STALE_MINUTES)

    retry = writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-retry", tool_use_id="call-fresh-retry",
        db_path=host_blocked_grant,
    )
    assert retry == {"approval_id": "P-plan", "index": 0}

    assert writer.settle_plan_command(
        "P-plan", session_id="ses-retry", tool_use_id="call-fresh-retry",
        success=True, db_path=host_blocked_grant,
    ) is True

    grant = _grant(host_blocked_grant)
    assert grant["next_index"] == 1
    assert grant["status"] == "PENDING"
    assert json.loads(grant["consumed_indexes_json"]) == [0]


def test_expiry_does_not_relax_retry_freshness(host_blocked_grant):
    """Consent is not spent by a host failure; the tool_use_id is still spent."""
    _age_reservation(host_blocked_grant, STALE_MINUTES)

    assert writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-original", tool_use_id="call-blocked-by-host",
        db_path=host_blocked_grant,
    ) is None
    assert _grant(host_blocked_grant)["reservation_tool_use_id"] == "call-blocked-by-host"


def test_a_running_command_keeps_its_reservation(host_blocked_grant):
    """Inside the window the slot is held: a command may still be executing."""
    assert writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-retry", tool_use_id="call-too-soon",
        db_path=host_blocked_grant,
    ) is None


def test_the_unexecuted_attempt_is_written_to_the_event_chain(host_blocked_grant):
    assert _events(host_blocked_grant, "NOOP") == []

    _age_reservation(host_blocked_grant, STALE_MINUTES)
    writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-retry", tool_use_id="call-fresh-retry",
        db_path=host_blocked_grant,
    )

    noops = _events(host_blocked_grant, "NOOP")
    assert len(noops) == 1
    metadata = json.loads(noops[0]["metadata_json"])
    # The row must say what actually happened -- consented, reserved, never run --
    # and must name the abandoned call rather than the retry that replaced it.
    assert metadata["reason_code"] == RESERVATION_ABANDONED_REASON_CODE
    assert metadata["call_id"] == "call-blocked-by-host"
    assert metadata["session_id"] == "ses-original"
    assert metadata["index"] == 0

    # No authorization state moved: the grant is still the user's live consent.
    grant = _grant(host_blocked_grant)
    assert grant["status"] == "PENDING"
    assert grant["failed_index"] is None
    assert grant["failure_reason"] is None


def test_reclaim_is_recorded_once_per_abandoned_call(host_blocked_grant):
    """A second stale reclaim of the same abandoned call appends no duplicate."""
    _age_reservation(host_blocked_grant, STALE_MINUTES)
    writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-retry", tool_use_id="call-fresh-retry",
        db_path=host_blocked_grant,
    )
    _age_reservation(host_blocked_grant, STALE_MINUTES)
    writer.reserve_plan_command(
        COMMANDS[0], session_id="ses-retry", tool_use_id="call-third",
        db_path=host_blocked_grant,
    )

    reasons = [
        json.loads(row["metadata_json"])["call_id"]
        for row in _events(host_blocked_grant, "NOOP")
    ]
    assert reasons == ["call-blocked-by-host", "call-fresh-retry"]
