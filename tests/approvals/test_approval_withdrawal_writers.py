"""The approvals CLI withdraws through the core (plan 76, task 6, PD8).

reject, reject --all, reject-all, revoke and clean each go through the core's
withdrawal: a rejected pending records REJECTED -- never REVOKED -- under either
host, a revoked pending or grant records REVOKED, an expired pending records its
expiry reason, and every event names the identity the requester resolver
returns, not a ``cli-reject`` label.
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

AGENT = "gaia-operator"
HOSTS = {
    "claude_code": ("CLAUDE_CODE_SESSION_ID", "ses-claude-code"),
    "opencode": ("GAIA_HOST_SESSION_ID", "ses_opencode"),
}


@pytest.fixture(params=sorted(HOSTS))
def host(request, tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DB", raising=False)
    for name in ("CLAUDE_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "GAIA_HOST_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    variable, session = HOSTS[request.param]
    monkeypatch.setenv(variable, session)
    monkeypatch.setenv("GAIA_DISPATCH_AGENT", AGENT)
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return {"session": session, "db": tmp_path / "gaia.db"}


def _pending(command: str) -> str:
    from gaia.approvals import store

    return store.insert_requested(
        {"commands": [command], "exact_content": command,
         "operation": "MUTATIVE command intercepted: push"},
        agent_id="developer", session_id="ses-requester",
    )


def _set_created(approval_id: str, age: timedelta) -> None:
    from gaia.approvals import store

    created = (datetime.now(timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = store._open_db()
    try:
        con.execute("UPDATE approvals SET created_at = ? WHERE id = ?", (created, approval_id))
        con.commit()
    finally:
        con.close()


def _last_event(approval_id: str) -> dict:
    from gaia.approvals import store

    return store.get_history(approval_id)[-1]


def _run(func, **values) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        assert func(argparse.Namespace(**values)) == 0
    return out.getvalue()


def _assert_rejected_by_resolver(approval_id: str, session: str) -> None:
    from gaia.approvals import store

    assert store.get_by_id(approval_id)["status"] == "rejected"
    event = _last_event(approval_id)
    assert event["event_type"] == "REJECTED"
    assert (event["session_id"], event["agent_id"]) == (session, AGENT)
    assert not any(e["event_type"] == "REVOKED" for e in store.get_history(approval_id))


def test_reject_records_rejected_under_the_resolved_identity(host):
    from bin.cli.approvals import cmd_reject

    approval_id = _pending("git push origin one")
    _run(cmd_reject, approval_id=approval_id, all=False, reason="ya no hace falta", json=True)
    _assert_rejected_by_resolver(approval_id, host["session"])
    assert json.loads(_last_event(approval_id)["metadata_json"])["reason"] == "ya no hace falta"


def test_reject_all_flag_rejects_every_pending(host):
    from bin.cli.approvals import cmd_reject

    ids = [_pending("git push origin two"), _pending("git push origin three")]
    _run(cmd_reject, approval_id=None, all=True, reason=None, json=True)
    for approval_id in ids:
        _assert_rejected_by_resolver(approval_id, host["session"])


def test_reject_all_subcommand_rejects_every_pending(host):
    from bin.cli.approvals import cmd_reject_all

    ids = [_pending("git push origin four"), _pending("git push origin five")]
    _run(cmd_reject_all, dry_run=False, workspace=None, json=False)
    for approval_id in ids:
        _assert_rejected_by_resolver(approval_id, host["session"])


def _live_grant(db, command: str) -> str:
    from gaia.approvals import store

    approval_id = _pending(command)
    store.approve(approval_id, "ses-user")
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO approval_grants (approval_id, agent_id, session_id, command_set_json, "
        "expires_at, status) VALUES (?, 'developer', 'ses-requester', '[]', ?, 'PENDING')",
        (approval_id, (datetime.now(timezone.utc) + timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    con.commit()
    con.close()
    return approval_id


def _assert_grant_closed_by_resolver(approval_id: str, session: str, source: str) -> None:
    """The grant is closed, the decision stands, and the chain says who closed it."""
    from gaia.approvals import store

    assert writer.list_approval_grants(status="REVOKED")[0]["approval_id"] == approval_id
    assert store.get_by_id(approval_id)["status"] == "approved"
    event = _last_event(approval_id)
    assert event["event_type"] == "REVOKED"
    assert (event["session_id"], event["agent_id"]) == (session, AGENT)
    assert json.loads(event["metadata_json"])["source"] == source


def test_reject_on_a_live_grant_revokes_the_grant(host):
    from bin.cli.approvals import cmd_reject

    approval_id = _live_grant(host["db"], "git push origin six")
    _run(cmd_reject, approval_id=approval_id, all=False, reason=None, json=True)
    _assert_grant_closed_by_resolver(approval_id, host["session"], "gaia approvals reject")


def test_revoke_on_a_live_grant_records_who_revoked_it(host):
    from bin.cli.approvals import cmd_revoke

    approval_id = _live_grant(host["db"], "git push origin nine")
    _run(cmd_revoke, approval_id=approval_id, yes=True, json=False)
    _assert_grant_closed_by_resolver(approval_id, host["session"], "gaia approvals revoke")


def test_revoke_on_a_pending_records_revoked_under_the_resolved_identity(host):
    from bin.cli.approvals import cmd_revoke
    from gaia.approvals import store

    approval_id = _pending("git push origin ten")
    _run(cmd_revoke, approval_id=approval_id, yes=True, json=False)
    assert store.get_by_id(approval_id)["status"] == "revoked"
    event = _last_event(approval_id)
    assert event["event_type"] == "REVOKED"
    assert (event["session_id"], event["agent_id"]) == (host["session"], AGENT)
    assert json.loads(event["metadata_json"])["source"] == "gaia approvals revoke"


def test_clean_records_the_expiry_reason_under_the_resolved_identity(host):
    from bin.cli.approvals import cmd_clean
    from gaia.approvals import store

    stale = _pending("git push origin seven")
    _set_created(stale, timedelta(hours=30))
    fresh = _pending("git push origin eight")
    _run(cmd_clean, dry_run=False, json=True)
    assert store.get_by_id(stale)["status"] == "expired"
    event = _last_event(stale)
    assert event["event_type"] == "REVOKED"
    assert json.loads(event["metadata_json"])["reason"] == "expired_ttl"
    assert (event["session_id"], event["agent_id"]) == (host["session"], AGENT)
    assert store.get_by_id(fresh)["status"] == "pending"
