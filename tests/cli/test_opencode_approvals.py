"""Tests for the OpenCode-bound approval broker commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli.approvals import cmd_opencode_decide, cmd_opencode_present


def _args(**overrides):
    values = {
        "approval_id": "P-open-1",
        "session_id": "ses-1",
        "call_id": "call-1",
        "token": "secret-token",
        "reply": "once",
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_opencode_decision_requires_matching_presentation(capsys):
    store = MagicMock()
    store.get_by_id.return_value = {
        "id": "P-open-1",
        "status": "pending",
        "session_id": "ses-1",
    }
    store.get_history.return_value = []

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_decide(_args())

    assert rc == 1
    store.approve.assert_not_called()
    assert "No matching OpenCode permission presentation exists" in capsys.readouterr().out


def test_opencode_presentation_then_approval_is_bound_to_session_call_and_token(capsys):
    store = MagicMock()
    approval = {"id": "P-open-1", "status": "pending", "session_id": "ses-1"}
    events: list[dict[str, str]] = []
    store.get_by_id.return_value = approval
    store.get_history.side_effect = lambda _id: events

    def record_event(*_args, **kwargs):
        events.append({"event_type": "SHOWN", "metadata_json": kwargs["metadata_json"]})

    store.record_event.side_effect = record_event
    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0
        assert cmd_opencode_decide(_args()) == 0

    store.approve.assert_called_once_with("P-open-1", "ses-1", agent_id="opencode-plugin")
    assert json.loads(capsys.readouterr().out.splitlines()[-1]) == {
        "status": "approved",
        "approval_id": "P-open-1",
    }


def test_opencode_presentation_token_cannot_be_reused_for_a_different_call():
    store = MagicMock()
    approval = {"id": "P-open-1", "status": "pending", "session_id": "ses-1"}
    events = [{
        "event_type": "SHOWN",
        "metadata_json": json.dumps({
            "host": "opencode",
            "call_id": "call-1",
            "token_sha256": "9d3b28b4f" * 7,
        }),
    }]
    store.get_by_id.return_value = approval
    store.get_history.return_value = events

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_decide(_args(call_id="call-2"))

    assert rc == 1
    store.approve.assert_not_called()
