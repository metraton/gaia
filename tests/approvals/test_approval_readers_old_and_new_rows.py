"""Every approvals reader shows the derived states on old and new rows alike (plan 76, task 6, AC-9).

One database holds rows written the old way -- bound by a label, closed by the
Stop sweep, with no window, directory or requester sealed, pending under the
``default``/``unattributed`` placeholders, rejected through the CLI as REVOKED --
next to rows written by the neutral core. Each reader must read every row
without raising, name an orphaned request orphaned, an expired one expired and a
replaced one replaced (never revoked, rejected or failed), and never count a
call with no result, or an old Stop-sweep failure, as a failure.
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import pytest

from gaia.store import writer

LIVE_SESSION = "ses-live"
DEAD_SESSION = "ses-dead"
AGENT = "developer"
CWD = "/home/jorge/ws/me/gaia"


def _iso(ago: timedelta) -> str:
    return (datetime.now(timezone.utc) - ago).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pid(n: int) -> str:
    return f"P-{n:08x}{'0' * 24}"


OLD_CLI_REJECT = _pid(1)
OLD_ORPHAN = _pid(2)
OLD_STOP_FAILED = _pid(3)
OLD_SWEPT_EXPIRED = _pid(4)
OLD_PAST_TTL = _pid(5)
NEW_PENDING = _pid(11)
NEW_REPLACED = _pid(12)
NEW_NO_RESULT = _pid(13)
NEW_EXECUTED = _pid(14)
NEW_REJECTED = _pid(15)
NEW_ORPHAN = _pid(16)


def _old_payload(command: str) -> dict:
    return {
        "commands": [command], "exact_content": command, "impact": None,
        "operation": "MUTATIVE command intercepted: push", "rationale": "old reactive block",
        "risk_level": "medium", "rollback_hint": None, "scope": command.split()[0],
        "verification": None,
    }


def _new_payload(command: str, session: str) -> dict:
    return {
        "what": "Publicar la rama.", "question": "¿Publico la rama?",
        "window_minutes": 30, "window_starts": "decision",
        "requested_by": {"session_id": session, "agent_id": AGENT},
        "items": [{
            "command": command, "does": "Sube la rama.", "impact": "Queda visible.",
            "cwd": CWD, "expect_exit": [], "position": 0, "fingerprint": "f", "key": "0:f",
        }],
        "commands": [command], "exact_content": command,
        "operation": "Execute an ordered T3 command set", "scope": "COMMAND_SET",
    }


def _row(con, approval_id, *, status, session, agent, payload, created, events=(), decided=None):
    from gaia.approvals.chain import _compute_this_hash

    con.execute(
        "INSERT INTO approvals (id, agent_id, session_id, status, fingerprint, payload_json, "
        "created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (approval_id, agent, session, status, "fp", json.dumps(payload), created, decided),
    )
    prev = None
    for event_type, ev_session, metadata, ev_payload, at in events:
        this = _compute_this_hash(prev, None)
        con.execute(
            "INSERT INTO approval_events (approval_id, event_type, agent_id, session_id, "
            "payload_json, prev_hash, this_hash, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (approval_id, event_type, agent, ev_session,
             json.dumps(ev_payload) if ev_payload else None, prev, this,
             json.dumps(metadata) if metadata else None, at),
        )
        prev = this


def _grant(con, approval_id, *, session, status, created, expires, consumed="[]"):
    con.execute(
        "INSERT INTO approval_grants (approval_id, agent_id, session_id, command_set_json, "
        "scope, created_at, expires_at, status, consumed_indexes_json, source) "
        "VALUES (?, ?, ?, ?, 'COMMAND_SET', ?, ?, ?, ?, 'plan-first')",
        (approval_id, AGENT, session, json.dumps([{"command": "git push"}]),
         created, expires, status, consumed),
    )


@pytest.fixture
def rows_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    from modules.session import session_registry

    monkeypatch.setattr(session_registry, "get_live_sessions", lambda include_headless=True: {LIVE_SESSION})
    db = tmp_path / "gaia.db"
    con = sqlite3.connect(db)
    con.executescript(writer._SCHEMA_PATH.read_text())
    two_hours, day_and_more = timedelta(hours=2), timedelta(hours=30)
    old = _old_payload("git push origin main")

    _row(con, OLD_CLI_REJECT, status="revoked", session="ses_old", agent=AGENT, payload=old,
         created=_iso(two_hours), decided=_iso(two_hours), events=[
             ("REQUESTED", "ses_old", None, old, _iso(two_hours)),
             ("SHOWN", "ses_old", {"host": "opencode", "call_id": "c1"}, None, _iso(two_hours)),
             ("SHOWN", "ses_old", {"host": "opencode", "call_id": "c2"}, None, _iso(two_hours)),
             ("SHOWN", "ses_old", {"host": "opencode", "call_id": "c3"}, None, _iso(two_hours)),
             ("REVOKED", "cli-reject", None, None, _iso(two_hours)),
         ])
    _row(con, OLD_ORPHAN, status="pending", session="default", agent="unattributed", payload=old,
         created=_iso(two_hours), events=[("REQUESTED", "default", None, old, _iso(two_hours))])
    _row(con, OLD_STOP_FAILED, status="approved", session="ses_old", agent=AGENT, payload=old,
         created=_iso(two_hours), decided=_iso(two_hours), events=[
             ("REQUESTED", "ses_old", None, old, _iso(two_hours)),
             ("APPROVED", "ses_old", {"label": f"[{OLD_STOP_FAILED}] Approve"}, None, _iso(two_hours)),
             ("FAILED", "ses_old", {"source": "post_tool_use"},
              {"exit_code": 1, "error": "command failed; no PostToolUse fired (reconciled at Stop)"},
              _iso(two_hours)),
         ])
    _grant(con, OLD_STOP_FAILED, session="ses_old", status="FAILED",
           created=_iso(two_hours), expires=None)
    _row(con, OLD_SWEPT_EXPIRED, status="expired", session="ses_old", agent=AGENT, payload=old,
         created=_iso(day_and_more), decided=_iso(two_hours), events=[
             ("REQUESTED", "ses_old", None, old, _iso(day_and_more)),
             ("REVOKED", "ses_old", {"reason": "expired_ttl", "source": "approval_cleanup.cleanup"},
              None, _iso(two_hours)),
         ])
    _row(con, OLD_PAST_TTL, status="pending", session="ses_old", agent=AGENT, payload=old,
         created=_iso(day_and_more), events=[("REQUESTED", "ses_old", None, old, _iso(day_and_more))])

    fresh = _new_payload("git push origin feat/new", LIVE_SESSION)
    _row(con, NEW_PENDING, status="pending", session=LIVE_SESSION, agent=AGENT, payload=fresh,
         created=_iso(timedelta(minutes=1)),
         events=[("REQUESTED", LIVE_SESSION, None, fresh, _iso(timedelta(minutes=1)))])
    _row(con, NEW_REPLACED, status="revoked", session=LIVE_SESSION, agent=AGENT,
         payload=_old_payload("git push origin feat/new"), created=_iso(timedelta(minutes=2)),
         decided=_iso(timedelta(minutes=1)), events=[
             ("REQUESTED", LIVE_SESSION, None, None, _iso(timedelta(minutes=2))),
             ("REVOKED", LIVE_SESSION, {"reason": "reemplazada", "replaced_by": NEW_PENDING,
                                        "source": "gaia.approvals.core"}, None, _iso(timedelta(minutes=1))),
         ])
    _row(con, NEW_NO_RESULT, status="approved", session=LIVE_SESSION, agent=AGENT, payload=fresh,
         created=_iso(two_hours), decided=_iso(two_hours), events=[
             ("REQUESTED", LIVE_SESSION, None, fresh, _iso(two_hours)),
             ("APPROVED", LIVE_SESSION, {"tool_use_id": "toolu_q"}, None, _iso(two_hours)),
         ])
    _grant(con, NEW_NO_RESULT, session=LIVE_SESSION, status="PENDING",
           created=_iso(two_hours), expires=_iso(timedelta(minutes=90)), consumed="[0]")
    _row(con, NEW_EXECUTED, status="approved", session=LIVE_SESSION, agent=AGENT, payload=fresh,
         created=_iso(two_hours), decided=_iso(two_hours), events=[
             ("REQUESTED", LIVE_SESSION, None, fresh, _iso(two_hours)),
             ("APPROVED", LIVE_SESSION, {"tool_use_id": "toolu_q"}, None, _iso(two_hours)),
             ("EXECUTED", LIVE_SESSION, {"source": "PostToolUse", "tool_use_id": "toolu_b"},
              {"exit_code": 0}, _iso(two_hours)),
         ])
    _grant(con, NEW_EXECUTED, session=LIVE_SESSION, status="CONSUMED",
           created=_iso(two_hours), expires=_iso(timedelta(minutes=90)), consumed="[0]")
    _row(con, NEW_REJECTED, status="rejected", session=LIVE_SESSION, agent=AGENT, payload=fresh,
         created=_iso(two_hours), decided=_iso(two_hours), events=[
             ("REQUESTED", LIVE_SESSION, None, fresh, _iso(two_hours)),
             ("REJECTED", LIVE_SESSION, None, None, _iso(two_hours)),
         ])
    dead = _new_payload("git push origin feat/dead", DEAD_SESSION)
    _row(con, NEW_ORPHAN, status="pending", session=DEAD_SESSION, agent=AGENT, payload=dead,
         created=_iso(two_hours), events=[("REQUESTED", DEAD_SESSION, None, dead, _iso(two_hours))])
    con.commit()
    con.close()
    return db


def _run(func, **values) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        assert func(argparse.Namespace(**values)) == 0
    return out.getvalue()


EXPECTED_STATES = {
    OLD_CLI_REJECT: "revoked",
    OLD_ORPHAN: "orphaned",
    OLD_STOP_FAILED: "approved",
    OLD_SWEPT_EXPIRED: "expired",
    OLD_PAST_TTL: "expired",
    NEW_PENDING: "pending",
    NEW_REPLACED: "replaced",
    NEW_NO_RESULT: "approved",
    NEW_EXECUTED: "approved",
    NEW_REJECTED: "rejected",
    NEW_ORPHAN: "orphaned",
}
EXPECTED_OUTCOMES = {
    OLD_STOP_FAILED: "legacy_failed",
    NEW_NO_RESULT: "no_result",
    NEW_EXECUTED: "executed",
}


@pytest.mark.parametrize("approval_id", sorted(EXPECTED_STATES))
def test_show_reads_every_row_with_its_derived_state(rows_db, approval_id):
    from bin.cli.approvals import cmd_show_v2

    shown = json.loads(_run(cmd_show_v2, approval_id=approval_id, json=True, consent_surface=False))
    assert shown["reading"]["state"] == EXPECTED_STATES[approval_id]
    assert shown["reading"]["outcome"] == EXPECTED_OUTCOMES.get(approval_id)

    text = _run(cmd_show_v2, approval_id=approval_id, json=False, consent_surface=False)
    assert f"State       : {EXPECTED_STATES[approval_id]}" in text


def test_show_names_what_a_new_request_sealed_and_an_old_one_did_not(rows_db):
    from bin.cli.approvals import cmd_show_v2

    new = json.loads(_run(cmd_show_v2, approval_id=NEW_PENDING, json=True, consent_surface=False))
    assert new["reading"]["requester"] == {"session_id": LIVE_SESSION, "agent_id": AGENT}
    assert new["reading"]["window_minutes"] == 30
    assert new["reading"]["cwd"] == [CWD]
    old = json.loads(_run(cmd_show_v2, approval_id=OLD_ORPHAN, json=True, consent_surface=False))
    assert old["reading"]["requester"] is None and old["reading"]["bound"] is False
    text = _run(cmd_show_v2, approval_id=OLD_ORPHAN, json=False, consent_surface=False)
    assert "Requester   : not sealed" in text


def test_list_and_pending_name_orphaned_and_expired_requests(rows_db):
    from bin.cli.approvals import cmd_list, cmd_pending

    listed = json.loads(_run(cmd_list, json=True, session=None, orphans_only=False))
    states = {item["approval_id"]: item["state"] for item in listed["pending"]}
    assert states == {
        OLD_ORPHAN: "orphaned", OLD_PAST_TTL: "expired",
        NEW_PENDING: "pending", NEW_ORPHAN: "orphaned",
    }
    outcomes = {item["approval_id"]: item["outcome"] for item in listed["grants"]}
    assert outcomes[NEW_NO_RESULT] == "no_result"
    assert outcomes[OLD_STOP_FAILED] == "legacy_failed"
    assert "orphaned" in _run(cmd_list, json=False, session=None, orphans_only=False)

    pending = json.loads(_run(cmd_pending, json=True, all_sessions=True, session=None))
    assert {row["id"]: row["state"] for row in pending}[OLD_ORPHAN] == "orphaned"
    assert "orphaned" in _run(cmd_pending, json=False, all_sessions=True, session=None)


def test_list_orphans_only_keeps_exactly_the_rows_read_orphaned(rows_db):
    from bin.cli.approvals import cmd_list

    listed = json.loads(_run(cmd_list, json=True, session=None, orphans_only=True))
    assert {item["approval_id"]: item["state"] for item in listed["pending"]} == {
        OLD_ORPHAN: "orphaned", NEW_ORPHAN: "orphaned",
    }


def test_list_help_explains_every_state_column_the_table_prints(rows_db, capsys):
    import re

    from bin.cli.approvals import cmd_list, register

    table = _run(cmd_list, json=False, session=None, orphans_only=False)
    headers = [line for line in table.splitlines() if line.startswith(("APPROVAL_ID", "ID "))]
    printed = {column for column in ("STATE", "STATUS", "GRANT_STATE", "OUTCOME")
               if any(re.search(rf"\b{column}\b", header) for header in headers)}
    assert printed == {"STATE", "STATUS", "GRANT_STATE", "OUTCOME"}

    parser = argparse.ArgumentParser(prog="gaia")
    register(parser.add_subparsers())
    with pytest.raises(SystemExit):
        parser.parse_args(["approvals", "list", "--help"])
    help_text = capsys.readouterr().out
    assert {column for column in printed if re.search(rf"\b{column}\b", help_text)} == printed


def test_window_is_the_one_the_grant_runs_not_the_one_the_request_sealed(rows_db):
    """An approval sealed at 30 minutes whose grant was minted at 60 reads 60 (P-432f3c8e...)."""
    from bin.cli.approvals import cmd_show_v2

    approval_id = _pid(17)
    fresh = _new_payload("git rm -r -q skills/old", LIVE_SESSION)
    con = sqlite3.connect(rows_db)
    _row(con, approval_id, status="approved", session=LIVE_SESSION, agent=AGENT, payload=fresh,
         created=_iso(timedelta(minutes=80)), decided=_iso(timedelta(minutes=79)), events=[
             ("REQUESTED", LIVE_SESSION, None, fresh, _iso(timedelta(minutes=80))),
             ("APPROVED", LIVE_SESSION, None, None, _iso(timedelta(minutes=79))),
         ])
    _grant(con, approval_id, session=LIVE_SESSION, status="PENDING",
           created=_iso(timedelta(minutes=79)), expires=_iso(timedelta(minutes=19)))
    con.commit()
    con.close()

    shown = json.loads(_run(cmd_show_v2, approval_id=approval_id, json=True, consent_surface=False))
    assert shown["reading"]["window_minutes"] == 60
    text = _run(cmd_show_v2, approval_id=approval_id, json=False, consent_surface=False)
    assert "Window      : 60 min from the decision" in text
    unsealed = _run(cmd_show_v2, approval_id=OLD_STOP_FAILED, json=False, consent_surface=False)
    assert "Window      : not sealed" in unsealed


def test_an_unregistered_host_requester_reads_alive_while_its_request_is_recent():
    """OpenCode never heartbeats the registry: recent activity alone keeps its request pending."""
    from gaia.approvals.reading import ORPHANED, PENDING, decision_state, sign_of_life

    row = {"status": "pending", "session_id": "ses_opencode", "created_at": _iso(timedelta(minutes=2))}
    shown_late = [{"event_type": "SHOWN", "created_at": _iso(timedelta(minutes=1))}]
    assert decision_state(row, shown_late, live=set()) == PENDING

    quiet_row = dict(row, created_at=_iso(sign_of_life() + timedelta(minutes=1)))
    assert decision_state(quiet_row, [], live=set()) == ORPHANED
    assert decision_state(quiet_row, [], live={"ses_opencode"}) == PENDING


def test_history_reads_replaced_as_replaced_never_revoked(rows_db):
    from bin.cli.approvals import cmd_history

    rows = json.loads(_run(cmd_history, approval_id=None, limit=50, status=None, json=True))
    assert {row["id"]: row["state"] for row in rows} == EXPECTED_STATES
    text = _run(cmd_history, approval_id=None, limit=50, status=None, json=False)
    replaced_line = next(line for line in text.splitlines() if line.startswith(NEW_REPLACED[:10]))
    assert "replaced" in replaced_line and "revoked" not in replaced_line


def test_stats_never_counts_no_result_or_an_old_sweep_as_failed(rows_db):
    from bin.cli.approvals import cmd_stats

    stats = json.loads(_run(cmd_stats, json=True))
    assert stats["states"] == {
        "approved": 3, "expired": 2, "orphaned": 2, "pending": 1,
        "rejected": 1, "replaced": 1, "revoked": 1,
    }
    assert stats["outcomes"] == {"executed": 1, "legacy_failed": 1, "no_result": 1}
    assert stats["revoked"] == 1 and stats["rejected"] == 1
    assert "failed" not in stats["outcomes"]
    assert "No result" in _run(cmd_stats, json=False)


def test_session_start_and_subagent_stop_cleanup_read_old_rows(rows_db):
    from gaia.approvals import store
    from modules.security.approval_cleanup import cleanup, count_stale_db_pendings

    assert count_stale_db_pendings() == 1
    cleanup(agent_type="developer", session_id="ses-cleanup")
    assert store.get_by_id(OLD_PAST_TTL)["status"] == "expired"
    for untouched in (OLD_ORPHAN, NEW_ORPHAN, NEW_PENDING):
        assert store.get_by_id(untouched)["status"] == "pending"


def test_writer_expiry_keeps_no_result_apart_from_failure(rows_db):
    from bin.cli.approvals import cmd_show_v2

    assert writer.cleanup_expired_db_grants() == 1
    shown = json.loads(_run(cmd_show_v2, approval_id=NEW_NO_RESULT, json=True, consent_surface=False))
    assert shown["grant"]["status"] == "EXPIRED"
    assert shown["reading"]["outcome"] == "no_result"


def test_persister_records_the_decision_never_a_pending_as_approved(rows_db):
    from modules.agents.handoff_persister import handoff_approval_decision

    def decision(approval_id):
        decided = handoff_approval_decision(approval_id)
        return decided and decided[0]

    assert decision(NEW_PENDING) is None
    assert decision(OLD_ORPHAN) is None
    assert decision(NEW_REJECTED) == "REJECTED"
    assert decision(NEW_REPLACED) == "REVOKED"
    assert decision(OLD_SWEPT_EXPIRED) == "EXPIRED"
    assert decision(NEW_EXECUTED) == "APPROVED"
    assert decision(_pid(99)) is None


def test_crosscheck_names_the_derived_state(rows_db):
    from gaia.contract.crosscheck import validate_crosscheck

    result = validate_crosscheck({"approval_request": {"approval_id": NEW_REPLACED}}, db_path=rows_db)
    assert not result.ok
    assert "replaced" in result.errors[0].detail and "'revoked'" not in result.errors[0].detail
    assert validate_crosscheck(
        {"approval_request": {"approval_id": NEW_PENDING}}, db_path=rows_db,
    ).ok


def test_decision_audit_accepts_an_old_row(rows_db):
    from gaia.approvals.decision_audit import record_decision_not_activated

    record_decision_not_activated(
        lane="claude_code.ask_user_question", reason="activation_failed",
        approval_id=OLD_ORPHAN, session_id="default",
    )


def test_opencode_reads_refuse_by_the_derived_state(rows_db):
    from bin.cli.approvals import _opencode_binding, _opencode_presentation_refusal

    refusal = _opencode_presentation_refusal(argparse.Namespace(
        approval_id=NEW_REPLACED, session_id=LIVE_SESSION, agent_id=AGENT,
    ))
    assert "replaced" in refusal and "revoked" not in refusal
    _, error = _opencode_binding(argparse.Namespace(
        approval_id=OLD_SWEPT_EXPIRED, session_id="ses_old", call_id="c1", token="t",
    ))
    assert "expired" in error
