"""Atomic decision -> executable COMMAND_SET regression coverage."""

from __future__ import annotations

import sqlite3

import pytest

from gaia.approvals import store
from gaia.approvals.command_set import command_fingerprint, request_fingerprint
from gaia.store import writer


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    con = sqlite3.connect(tmp_path / "gaia.db")
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return tmp_path / "gaia.db"


def _seed(db_path, commands):
    items = [{"command": c, "fingerprint": command_fingerprint(c), "rationale": ""} for c in commands]
    payload = {
        "request_type": "COMMAND_SET",
        "command_set": items,
        "request_fingerprint": request_fingerprint(commands),
        "scope": "COMMAND_SET",
        "operation": "publish",
        "exact_content": commands[0],
    }
    approval_id = store.insert_requested(payload, agent_id="agent-1", session_id="session-1")
    return approval_id, items, payload


def test_approval_and_executable_grant_commit_together(isolated_db):
    approval_id, items, payload = _seed(isolated_db, ["git push origin main", "docker push registry/app:1"])

    result = store.activate_command_set_atomically(
        approval_id,
        items,
        request_fingerprint=payload["request_fingerprint"],
        shown_payload={"scope": "COMMAND_SET", "exact_content": items[0]["command"]},
        approver_session="control-session",
    )

    assert result == {"status": "applied", "idempotent": False}
    con = sqlite3.connect(isolated_db)
    assert con.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0] == "approved"
    assert con.execute(
        "SELECT scope, status, request_fingerprint FROM approval_grants WHERE approval_id=?",
        (approval_id,),
    ).fetchone() == ("COMMAND_SET", "PENDING", payload["request_fingerprint"])
    assert con.execute(
        "SELECT COUNT(*) FROM approval_events WHERE approval_id=? AND event_type IN ('SHOWN','APPROVED')",
        (approval_id,),
    ).fetchone()[0] == 2
    con.close()


def test_grant_creation_failure_rolls_back_approval(isolated_db, monkeypatch):
    approval_id, items, payload = _seed(isolated_db, ["git push origin main"])

    monkeypatch.setattr(
        writer,
        "insert_plan_command_set",
        lambda *args, **kwargs: {"status": "error", "reason": "grant unavailable"},
    )
    with pytest.raises(ValueError, match="grant unavailable"):
        store.activate_command_set_atomically(
            approval_id, items,
            request_fingerprint=payload["request_fingerprint"],
            shown_payload={"scope": "COMMAND_SET"},
            approver_session="control-session",
        )

    con = sqlite3.connect(isolated_db)
    assert con.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0] == "pending"
    assert con.execute("SELECT COUNT(*) FROM approval_grants WHERE approval_id=?", (approval_id,)).fetchone()[0] == 0
    con.close()


def test_repeated_activation_is_idempotent_and_does_not_duplicate_grant(isolated_db):
    approval_id, items, payload = _seed(isolated_db, ["git push origin main"])
    kwargs = dict(
        request_fingerprint=payload["request_fingerprint"],
        shown_payload={"scope": "COMMAND_SET"},
        approver_session="control-session",
    )
    assert store.activate_command_set_atomically(approval_id, items, **kwargs)["idempotent"] is False
    assert store.activate_command_set_atomically(approval_id, items, **kwargs) == {
        "status": "applied", "idempotent": True,
    }
    con = sqlite3.connect(isolated_db)
    assert con.execute("SELECT COUNT(*) FROM approval_grants WHERE approval_id=?", (approval_id,)).fetchone()[0] == 1
    con.close()


def test_activation_rejects_tampered_set_without_approving(isolated_db):
    approval_id, items, payload = _seed(isolated_db, ["git push origin main"])
    tampered = [{**items[0], "command": "git push origin production"}]

    with pytest.raises(ValueError, match="fingerprint mismatch"):
        store.activate_command_set_atomically(
            approval_id,
            tampered,
            request_fingerprint=payload["request_fingerprint"],
            shown_payload=payload,
            approver_session="control-session",
        )

    con = sqlite3.connect(isolated_db)
    assert con.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0] == "pending"
    assert con.execute("SELECT COUNT(*) FROM approval_grants WHERE approval_id=?", (approval_id,)).fetchone()[0] == 0
    con.close()


def test_activation_savepoint_does_not_leak_into_caller_transaction(isolated_db, monkeypatch):
    approval_id, items, payload = _seed(isolated_db, ["git push origin main"])
    connection = sqlite3.connect(isolated_db)
    monkeypatch.setattr(
        writer,
        "insert_plan_command_set",
        lambda *args, **kwargs: {"status": "error", "reason": "grant unavailable"},
    )
    with pytest.raises(ValueError, match="grant unavailable"):
        store.activate_command_set_atomically(
            approval_id,
            items,
            request_fingerprint=payload["request_fingerprint"],
            shown_payload=payload,
            approver_session="control-session",
            con=connection,
        )
    connection.commit()
    assert connection.execute("SELECT status FROM approvals WHERE id=?", (approval_id,)).fetchone()[0] == "pending"
    connection.close()
