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


# 97c8197 requires the complete canonical P-<32-hex> id for every approval
# lookup; a short display label like "P-open-1" is rejected outright by the
# CLI's _require_canonical_approval_id identity check.
_APPROVAL_ID = "P-" + "a1c0de00" * 4


def _args(**overrides):
    values = {
        "approval_id": _APPROVAL_ID,
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
        "id": _APPROVAL_ID,
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
    approval = {"id": _APPROVAL_ID, "status": "pending", "session_id": "ses-1"}
    events: list[dict[str, str]] = []
    store.get_by_id.return_value = approval
    store.get_history.side_effect = lambda _id: events

    def record_event(*_args, **kwargs):
        events.append({
            "event_type": "SHOWN",
            "session_id": kwargs["session_id"],
            "metadata_json": kwargs["metadata_json"],
        })

    store.record_event.side_effect = record_event
    store.activate_approval_atomically.return_value.success = True
    store.activate_approval_atomically.return_value.retry_descriptor = {
        "version": 1,
        "kind": "SCOPE_SEMANTIC_SIGNATURE",
        "approval_id": _APPROVAL_ID,
        "command": "git push origin main",
    }
    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0
        assert cmd_opencode_decide(_args()) == 0

    store.activate_approval_atomically.assert_called_once_with(
        _APPROVAL_ID,
        approver_session="ses-1",
        agent_id="opencode-plugin",
        binding={
            "agent_id": "opencode-plugin",
            "session_id": "ses-1",
            "call_id": "call-1",
        },
    )
    emitted = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert emitted["status"] == "approved"
    assert emitted["approval_id"] == _APPROVAL_ID
    assert emitted["decision"] == "once"
    assert emitted["decision_lane"] == "preferred"
    assert emitted["correlation_id"].startswith("C-")
    assert emitted["protocol_version"] == "1"
    assert emitted["retry_descriptor"] == store.activate_approval_atomically.return_value.retry_descriptor


def _unowned_store():
    """A pending approval minted without --session-id, as request-set persists it."""
    store = MagicMock()
    approval = {"id": _APPROVAL_ID, "status": "pending", "session_id": None}
    events: list[dict[str, str]] = []
    store.get_by_id.return_value = approval
    store.get_history.side_effect = lambda _id, **_kw: events

    def adopt_session(_id, session_id, **_kwargs):
        approval["session_id"] = session_id

    def record_event(*_args, **kwargs):
        events.append({
            "event_type": "SHOWN",
            "session_id": kwargs["session_id"],
            "metadata_json": kwargs["metadata_json"],
        })

    store.adopt_session.side_effect = adopt_session
    store.record_event.side_effect = record_event
    return store, approval, events


def test_unowned_approval_is_adopted_by_the_presenting_session(capsys):
    store, approval, events = _unowned_store()

    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0

    store.adopt_session.assert_called_once()
    assert store.adopt_session.call_args.args[:2] == (_APPROVAL_ID, "ses-1")
    assert approval["session_id"] == "ses-1"
    assert [e["event_type"] for e in events] == ["SHOWN"]
    assert events[0]["session_id"] == "ses-1"
    emitted = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert emitted["status"] == "presented"
    assert emitted["approval_id"] == _APPROVAL_ID


def test_adopted_approval_rejects_presentation_from_another_session(capsys):
    store, _approval, events = _unowned_store()

    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args(session_id="ses-a", call_id="call-a")) == 0
        rc = cmd_opencode_present(_args(session_id="ses-b", call_id="call-b"))

    assert rc == 1
    assert len(events) == 1
    assert store.adopt_session.call_count == 1
    assert "OpenCode session does not own this approval" in capsys.readouterr().out


def test_owned_approval_still_rejects_a_foreign_session(capsys):
    store = MagicMock()
    store.get_by_id.return_value = {
        "id": _APPROVAL_ID, "status": "pending", "session_id": "ses-owner",
    }
    store.get_history.return_value = []

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_present(_args(session_id="ses-intruder"))

    assert rc == 1
    store.adopt_session.assert_not_called()
    store.record_event.assert_not_called()
    assert "OpenCode session does not own this approval" in capsys.readouterr().out


def test_reload_after_adoption_is_idempotent(capsys):
    store, _approval, events = _unowned_store()

    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0
        assert cmd_opencode_present(_args()) == 0

    assert len(events) == 1
    assert store.adopt_session.call_count == 1
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["status"] == "presented"


def test_opencode_presentation_token_cannot_be_reused_for_a_different_call():
    store = MagicMock()
    approval = {"id": _APPROVAL_ID, "status": "pending", "session_id": "ses-1"}
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
