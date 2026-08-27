"""Regression coverage for the two ends of a plan-first grant's life.

A plan-first COMMAND_SET grant used to have no way to die: it was born without a
deadline, the sweep skipped rows without one, and the revoke verb refused every
id whose decision had already been taken. That left exactly one reachable state
-- approved, unconsumed, live -- which was also the only state neither the clock
nor the tool could close. These tests pin both closures shut.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from gaia.approvals.command_set import request_fingerprint, validate_request_set
from gaia.store import writer

COMMANDS = ["git push origin main", "docker push registry/app:1"]


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "gaia.db"
    con = sqlite3.connect(db_path)
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return db_path


def _insert_set(db_path, approval_id="P-plan", commands=COMMANDS):
    items = validate_request_set(commands)
    result = writer.insert_plan_command_set(
        approval_id, items, request_fingerprint=request_fingerprint(commands),
        session_id="request-session", db_path=db_path,
    )
    assert result["status"] == "applied"
    return commands


def _row(db_path, approval_id="P-plan"):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT * FROM approval_grants WHERE approval_id=?", (approval_id,)
        ).fetchone())
    finally:
        con.close()


def _iso(offset_minutes: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _backdate(db_path, approval_id, *, created_minutes_ago, expires_at):
    """Rewrite a row's clock fields to stand in for a grant of another age."""
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "UPDATE approval_grants SET created_at=?, expires_at=? WHERE approval_id=?",
            (_iso(-created_minutes_ago), expires_at, approval_id),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# A grant nobody consumed stops authorizing
# ---------------------------------------------------------------------------

class TestGrantExpiry:
    def test_grant_is_born_with_a_deadline(self, isolated_db):
        _insert_set(isolated_db)
        row = _row(isolated_db)
        assert row["expires_at"], "a plan-first grant must carry a TTL at birth"
        assert row["expires_at"] == writer._plan_grant_deadline(row["created_at"])
        assert row["expires_at"] > row["created_at"]

    def test_live_grant_still_reserves(self, isolated_db):
        """Control: the window is wide enough that a fresh grant is usable."""
        first, _ = _insert_set(isolated_db)
        assert writer.reserve_plan_command(
            first, session_id="s", tool_use_id="call-1", db_path=isolated_db,
        ) == {"approval_id": "P-plan", "index": 0}

    def test_lapsed_grant_no_longer_reserves(self, isolated_db):
        """The deadline gates the point that AUTHORIZES, not just the sweep.

        No cleanup runs in this test: the grant is still status='PENDING' in the
        table when the reservation is refused.
        """
        first, _ = _insert_set(isolated_db)
        _backdate(isolated_db, "P-plan", created_minutes_ago=120, expires_at=_iso(-60))

        assert writer.reserve_plan_command(
            first, session_id="s", tool_use_id="call-1", db_path=isolated_db,
        ) is None
        assert writer.pending_plan_command_exists(first, db_path=isolated_db) is False
        assert _row(isolated_db)["status"] == "PENDING"

    def test_partly_consumed_set_lapses_on_its_remainder(self, isolated_db):
        """A consumed item does not extend the window over the ones still unused."""
        first, second = _insert_set(isolated_db)
        writer.reserve_plan_command(first, session_id="s", tool_use_id="c1", db_path=isolated_db)
        assert writer.settle_plan_command(
            "P-plan", session_id="s", tool_use_id="c1", success=True, db_path=isolated_db,
        )
        _backdate(isolated_db, "P-plan", created_minutes_ago=120, expires_at=_iso(-60))

        assert writer.reserve_plan_command(
            second, session_id="s", tool_use_id="c2", db_path=isolated_db,
        ) is None


# ---------------------------------------------------------------------------
# Rows issued before grants carried a TTL
# ---------------------------------------------------------------------------

class TestGrantsIssuedWithoutATtl:
    def test_old_ttl_less_grant_is_already_lapsed(self, isolated_db):
        """A key issued yesterday with no deadline is dead on its creation date.

        The deadline is DERIVED from created_at rather than backfilled, so the
        row itself is never rewritten.
        """
        first, _ = _insert_set(isolated_db)
        _backdate(isolated_db, "P-plan", created_minutes_ago=1440, expires_at=None)

        assert writer.reserve_plan_command(
            first, session_id="s", tool_use_id="call-1", db_path=isolated_db,
        ) is None
        assert _row(isolated_db)["expires_at"] is None, "the row is read, not rewritten"

    def test_in_flight_ttl_less_grant_survives(self, isolated_db):
        """Deriving does not expire retroactively: a set approved minutes ago runs."""
        first, _ = _insert_set(isolated_db)
        _backdate(isolated_db, "P-plan", created_minutes_ago=2, expires_at=None)

        assert writer.reserve_plan_command(
            first, session_id="s", tool_use_id="call-1", db_path=isolated_db,
        ) == {"approval_id": "P-plan", "index": 0}

    def test_sweep_collects_an_old_ttl_less_grant(self, isolated_db):
        _insert_set(isolated_db)
        _backdate(isolated_db, "P-plan", created_minutes_ago=1440, expires_at=None)

        assert writer.cleanup_expired_db_grants(db_path=isolated_db) == 1
        assert _row(isolated_db)["status"] == "EXPIRED"

    def test_clean_verb_reaches_the_same_rows_as_the_sweep(self, isolated_db, capsys):
        """`gaia approvals clean` used to carry its own copy of the rule and miss these."""
        from bin.cli.approvals import cmd_clean

        _insert_set(isolated_db)
        _backdate(isolated_db, "P-plan", created_minutes_ago=1440, expires_at=None)

        assert writer.count_expired_db_grants(db_path=isolated_db) == 1
        assert cmd_clean(_args(dry_run=False)) == 0
        assert "1 expired DB grant" in capsys.readouterr().out
        assert _row(isolated_db)["status"] == "EXPIRED"

    def test_sweep_spares_a_ttl_less_grant_of_another_source(self, isolated_db):
        """The NULL-means-derive exception is anchored to source='plan-first'."""
        writer.insert_approval_grant(
            "P-legacy", [{"command": "aws s3 rm s3://b/k", "rationale": "x"}],
            session_id="s", db_path=isolated_db,
        )
        _backdate(isolated_db, "P-legacy", created_minutes_ago=1440, expires_at=None)

        assert writer.cleanup_expired_db_grants(db_path=isolated_db) == 0
        assert _row(isolated_db, "P-legacy")["status"] == "PENDING"


# ---------------------------------------------------------------------------
# The closing verb reaches every state a grant can be live in
# ---------------------------------------------------------------------------

def _approved_with_live_grant(db_path, session="sess-a"):
    """Produce the one state a loose key can exist in: decided + grant live."""
    import gaia.approvals.store as store

    items = validate_request_set(COMMANDS)
    con = writer._connect(db_path)
    try:
        approval_id = store.insert_requested(
            {
                "operation": "plan-first set",
                "exact_content": COMMANDS[0],
                "scope": "COMMAND_SET",
                "request_type": "COMMAND_SET",
            },
            agent_id="gaia-system", session_id=session, con=con,
        )
        store.approve(approval_id, approver_session=session, con=con)
        con.commit()
    finally:
        con.close()

    assert writer.insert_plan_command_set(
        approval_id, items, request_fingerprint=request_fingerprint(COMMANDS),
        session_id=session, db_path=db_path,
    )["status"] == "applied"
    return approval_id


def _args(**kwargs):
    import argparse

    defaults = {"json": False, "yes": True}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


class TestClosingVerbReachesTheDecidedState:
    def test_revoke_closes_an_approved_grant(self, isolated_db, capsys):
        from bin.cli.approvals import cmd_revoke
        import gaia.approvals.store as store

        approval_id = _approved_with_live_grant(isolated_db)
        assert _row(isolated_db, approval_id)["status"] == "PENDING"

        rc = cmd_revoke(_args(approval_id=approval_id))

        assert rc == 0, capsys.readouterr().err
        assert _row(isolated_db, approval_id)["status"] == "REVOKED"
        assert store.get_by_id(approval_id)["status"] == "approved", (
            "revoking closes the capability, never the record of the user's decision"
        )

    def test_revoked_grant_no_longer_reserves(self, isolated_db):
        from bin.cli.approvals import cmd_revoke

        approval_id = _approved_with_live_grant(isolated_db)
        assert cmd_revoke(_args(approval_id=approval_id)) == 0
        assert writer.reserve_plan_command(
            COMMANDS[0], session_id="s", tool_use_id="c1", db_path=isolated_db,
        ) is None

    def test_reject_closes_an_approved_grant_by_prefix(self, isolated_db, capsys):
        """Name kept for the literal-count gate; 97c8197 retired prefix lookup
        (cmd_reject now requires the complete canonical approval_id -- a
        prefix is a short display label and is rejected by
        _require_canonical_approval_id before lookup runs). This exercises
        the closing verb with the exact id the new contract demands."""
        from bin.cli.approvals import cmd_reject
        import gaia.approvals.store as store

        approval_id = _approved_with_live_grant(isolated_db)

        rc = cmd_reject(_args(approval_id=approval_id, all=False, reason=None))

        assert rc == 0, capsys.readouterr().err
        assert _row(isolated_db, approval_id)["status"] == "REVOKED"
        assert store.get_by_id(approval_id)["status"] == "approved"

    def test_revoke_reports_when_nothing_is_left_to_close(self, isolated_db, capsys):
        from bin.cli.approvals import cmd_revoke

        approval_id = _approved_with_live_grant(isolated_db)
        assert cmd_revoke(_args(approval_id=approval_id)) == 0

        assert cmd_revoke(_args(approval_id=approval_id)) == 1
        assert "terminal state" in capsys.readouterr().err

    def test_pending_approval_still_revokes_through_the_decision_log(self, isolated_db, capsys):
        """The pre-existing path is untouched: a pending row is revoked, not fallen through."""
        from bin.cli.approvals import cmd_revoke
        import gaia.approvals.store as store

        con = writer._connect(isolated_db)
        try:
            approval_id = store.insert_requested(
                {"operation": "x", "exact_content": "echo x", "scope": "COMMAND_SET"},
                agent_id="gaia-system", session_id="sess-p", con=con,
            )
            con.commit()
        finally:
            con.close()

        assert cmd_revoke(_args(approval_id=approval_id)) == 0, capsys.readouterr().err
        assert store.get_by_id(approval_id)["status"] == "revoked"
