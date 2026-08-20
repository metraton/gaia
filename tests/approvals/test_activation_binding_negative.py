"""Negative coverage for the binding checks on atomic COMMAND_SET activation."""

from __future__ import annotations

import sqlite3

import pytest

from gaia.approvals import store
from gaia.approvals.command_set import command_fingerprint, request_fingerprint
from gaia.store import writer


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    db_path = tmp_path / "gaia.db"
    con = sqlite3.connect(db_path)
    con.executescript(writer._SCHEMA_PATH.read_text())
    con.commit()
    con.close()
    return db_path


def _seed(db_path):
    commands = ["git push origin main"]
    items = [{"command": command, "fingerprint": command_fingerprint(command), "rationale": ""}
             for command in commands]
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


def _assert_activation_rejected(db_path, approval_id):
    con = sqlite3.connect(db_path)
    try:
        assert con.execute(
            "SELECT status FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()[0] == "pending"
        assert con.execute(
            "SELECT COUNT(*) FROM approval_grants WHERE approval_id=?", (approval_id,)
        ).fetchone()[0] == 0
    finally:
        con.close()


@pytest.mark.parametrize(
    ("agent_id", "binding", "message"),
    [
        (
            "agent-2",
            {"agent_id": "agent-2", "session_id": "session-1", "call_id": "call-1"},
            "agent_id mismatch",
        ),
        (
            "agent-1",
            {"agent_id": "agent-1", "session_id": "session-2", "call_id": "call-1"},
            "session_id mismatch",
        ),
        (
            "agent-1",
            {"agent_id": "agent-1", "session_id": "session-1"},
            "call_id is required",
        ),
    ],
)
def test_activation_rejects_mismatched_or_incomplete_binding(
    isolated_db, agent_id, binding, message
):
    approval_id, items, payload = _seed(isolated_db)

    with pytest.raises(ValueError, match=message):
        store.activate_command_set_atomically(
            approval_id,
            items,
            request_fingerprint=payload["request_fingerprint"],
            shown_payload=payload,
            approver_session="session-1",
            agent_id=agent_id,
            binding=binding,
        )

    _assert_activation_rejected(isolated_db, approval_id)
