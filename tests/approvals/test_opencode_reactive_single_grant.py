"""A reactive single approval decided from OpenCode leaves a usable semantic grant.

Observed 2026-09-17: the native reply marked the approval approved and created
no grant, so the signed retry was refused. The decision goes through the real
``opencode-present`` / ``opencode-decide`` commands over a real database.
"""

from __future__ import annotations

import argparse
import json

from tests.approvals.test_typed_atomic_activation import (  # noqa: F401  (isolated_db is a fixture)
    AGENT_ID,
    COMMAND,
    SESSION_ID,
    _grant_count,
    _seed,
    _semantic_payload,
    _status,
    isolated_db,
)

from cli.approvals import cmd_opencode_decide, cmd_opencode_present
from gaia.store import writer

CALL_ID = "call-reactive-original"


def _args(**overrides) -> argparse.Namespace:
    values = {
        "approval_id": None,
        "session_id": SESSION_ID,
        "agent_id": AGENT_ID,
        "call_id": CALL_ID,
        "token": "reactive-token",
        "reply": "once",
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _phrased_reactive_payload() -> dict:
    """A reactive block of one command, carrying the phrases a presentation requires."""
    return _semantic_payload() | {
        "what": "Publish the main branch.",
        "question": "Publish the branch?",
        "items": [{"command": COMMAND, "does": "Publishes the branch.", "impact": "The remote advances."}],
    }


def test_once_reply_on_a_reactive_single_approval_creates_the_grant_its_retry_needs(
    isolated_db, capsys
):
    approval_id = _seed(_phrased_reactive_payload())

    assert cmd_opencode_present(_args(approval_id=approval_id)) == 0
    assert cmd_opencode_decide(_args(approval_id=approval_id)) == 0

    decided = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert decided["status"] == "approved"
    assert decided["retry_descriptor"]["kind"] == "SCOPE_SEMANTIC_SIGNATURE"
    assert decided["retry_descriptor"]["command"] == COMMAND
    assert _status(isolated_db, approval_id) == "approved"
    assert _grant_count(isolated_db, approval_id) == 1

    grant = next(
        row for row in writer.list_approval_grants(status="PENDING", limit=1000)
        if row["approval_id"] == approval_id
    )
    assert grant["scope"] == "SCOPE_SEMANTIC_SIGNATURE"
    assert json.loads(grant["command_set_json"])["command"] == COMMAND
    assert grant["session_id"] == SESSION_ID
    assert grant["agent_id"] == AGENT_ID


def test_reject_reply_on_a_reactive_single_approval_creates_no_grant(isolated_db, capsys):
    approval_id = _seed(_phrased_reactive_payload())

    assert cmd_opencode_present(_args(approval_id=approval_id)) == 0
    assert cmd_opencode_decide(_args(approval_id=approval_id, reply="reject")) == 0

    assert _status(isolated_db, approval_id) == "rejected"
    assert _grant_count(isolated_db, approval_id) == 0
