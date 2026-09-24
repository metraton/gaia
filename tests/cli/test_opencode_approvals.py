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
_AGENT_ID = "agent-1"
_PHRASED_PAYLOAD = json.dumps({
    "operation": "PUSH command intercepted: push",
    "exact_content": "git push origin main",
    "scope": "SINGULAR",
    "what": "Publicar la rama principal.",
    "question": "¿Publico la rama?",
    "items": [{
        "command": "git push origin main",
        "does": "Publica la rama principal.",
        "impact": "El remoto avanza.",
    }],
})


def _args(**overrides):
    values = {
        "approval_id": _APPROVAL_ID,
        "session_id": "ses-1",
        "agent_id": _AGENT_ID,
        "call_id": "call-1",
        "token": "secret-token",
        "reply": "once",
        "json": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _pending(session_id="ses-1"):
    return {
        "id": _APPROVAL_ID,
        "status": "pending",
        "session_id": session_id,
        "agent_id": _AGENT_ID,
        "payload_json": _PHRASED_PAYLOAD,
    }


def _recording_store(approval):
    """A store whose SHOWN events are kept, so presentation and decision see the same history."""
    store = MagicMock()
    events: list[dict[str, str]] = []
    store.get_by_id.return_value = approval
    store.get_history.side_effect = lambda _id, **_kw: events

    def record_event(*_args, **kwargs):
        events.append({
            "event_type": "SHOWN",
            "session_id": kwargs["session_id"],
            "metadata_json": kwargs["metadata_json"],
        })

    store.record_event.side_effect = record_event
    return store, events


def test_opencode_decision_requires_matching_presentation(capsys):
    store = MagicMock()
    store.get_by_id.return_value = _pending()
    store.get_history.return_value = []

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_decide(_args())

    assert rc == 1
    store.approve.assert_not_called()
    assert "No matching OpenCode permission presentation exists" in capsys.readouterr().out


def test_opencode_presentation_then_approval_is_bound_to_session_call_and_token(capsys):
    store, _events = _recording_store(_pending())
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
        agent_id=_AGENT_ID,
        binding={
            "agent_id": _AGENT_ID,
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


def test_an_approval_with_no_requesting_session_is_never_presented_or_adopted(capsys):
    """PD6: the requester is sealed at request time, so no presenter can claim it later."""
    store, events = _recording_store(_pending(session_id=None))

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_present(_args())

    assert rc == 1
    assert events == []
    store.adopt_session.assert_not_called()
    assert "has no requesting session" in capsys.readouterr().out


def test_owned_approval_rejects_a_foreign_session(capsys):
    store, events = _recording_store(_pending(session_id="ses-owner"))

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_present(_args(session_id="ses-intruder"))

    assert rc == 1
    assert events == []
    assert "OpenCode presentation must come from the requesting session" in capsys.readouterr().out


def test_owned_approval_rejects_another_agent_of_the_same_session(capsys):
    store, events = _recording_store(_pending())

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_present(_args(agent_id="agent-other"))

    assert rc == 1
    assert events == []
    assert "OpenCode presentation must come from the requesting agent" in capsys.readouterr().out


def test_a_repeated_presentation_of_the_same_call_is_idempotent(capsys):
    store, events = _recording_store(_pending())

    with patch("cli.approvals._import_approval_store", return_value=store):
        assert cmd_opencode_present(_args()) == 0
        assert cmd_opencode_present(_args()) == 0

    assert len(events) == 1
    lines = capsys.readouterr().out.splitlines()
    assert json.loads(lines[-1])["status"] == "presented"


def test_opencode_presentation_token_cannot_be_reused_for_a_different_call():
    store = MagicMock()
    events = [{
        "event_type": "SHOWN",
        "metadata_json": json.dumps({
            "host": "opencode",
            "call_id": "call-1",
            "token_sha256": "9d3b28b4f" * 7,
        }),
    }]
    store.get_by_id.return_value = _pending()
    store.get_history.return_value = events

    with patch("cli.approvals._import_approval_store", return_value=store):
        rc = cmd_opencode_decide(_args(call_id="call-2"))

    assert rc == 1
    store.approve.assert_not_called()
