"""Fail-closed backstop + parent-event bind for the OpenCode adapter
(plan 65, task 9's sibling; task 10, gates 1012/1013).

Two halves:

  (1) BACKSTOP (gate 1013): a dispatched child (a non-primary OpenCode
      session -- ``host_agent_id`` truthy, the plugin's own
      ``dispatchHandle``) is denied its first tool call until
      ``gaia.store.writer.is_harness_session_bound`` reports its session
      bound. The deny NAMES ``_CHILD_BINDING_BACKSTOP_EMITTER`` literally as
      the emitter, so a deny from any other policy lane (delegate_mode, an
      identity rejection, the tier classifier) can never be mistaken for it.
  (2) BIND (gate 1012, adapter side): ``adapt_subagent_start`` reads the
      exact callID<->child-session pair from a ``message.part.updated``-
      shaped payload (the PARENT's own event) and calls
      ``bind_harness_child_session`` with it -- never for a session.created-
      shaped payload, which carries no callID at all.

Mocks every collaborator at the module boundary it is imported from
(``gaia.store.writer.is_harness_session_bound`` /
``gaia.store.writer.bind_harness_child_session``, ``ClaudeCodeAdapter.
adapt_pre_tool_use`` for the delegated policy), per this directory's own
convention of a pure adapter test that never touches the DB.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.opencode import OpenCodeAdapter, _CHILD_BINDING_BACKSTOP_EMITTER
from adapters.types import HookResponse


def _child_tool_event(agent_id: str = "child-ses-1", session_id: str = "child-ses-1"):
    """A dispatched CHILD's own tool call -- non-primary, so agentID (the
    plugin's dispatchHandle) is truthy, exactly as OpenCode reports it."""
    return OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": session_id,
        "callID": "call-child-1",
        "agentID": agent_id,
        "tool": "bash",
        "args": {"command": "echo hi"},
    }))


def _primary_tool_event():
    """The ROOT session's own tool call -- dispatchHandle is undefined for
    the primary, so this event carries no agentID at all."""
    return OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-root",
        "callID": "call-root-1",
        "tool": "bash",
        "args": {"command": "echo hi"},
    }))


# ---------------------------------------------------------------------------
# (1) backstop -- gate 1013
# ---------------------------------------------------------------------------

def test_unbound_dispatched_child_is_denied_by_the_named_backstop(monkeypatch):
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda *a, **k: False)

    response = OpenCodeAdapter().adapt_pre_tool_use(_child_tool_event())

    assert response.output["action"] == "deny"
    assert response.exit_code == 2
    assert _CHILD_BINDING_BACKSTOP_EMITTER in response.output["reason"]


def test_bound_dispatched_child_passes_through_to_ordinary_policy(monkeypatch):
    from adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda *a, **k: True)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "adapt_pre_tool_use",
        lambda _self, event: HookResponse(output={"action": "allow"}),
    )

    response = OpenCodeAdapter().adapt_pre_tool_use(_child_tool_event())

    assert response.output["action"] == "allow"
    assert _CHILD_BINDING_BACKSTOP_EMITTER not in str(response.output)


def test_backstop_never_engages_for_the_primary_sessions_own_tool_call(monkeypatch):
    """The primary carries no dispatch handle at all -- the backstop must
    never even consult the binding table for it (a deny here would be a
    denial-of-service against the orchestrator itself)."""
    from adapters.claude_code import ClaudeCodeAdapter

    called = {"checked": False}

    def fail_if_called(*_a, **_k):
        called["checked"] = True
        return False

    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", fail_if_called)
    monkeypatch.setattr(
        ClaudeCodeAdapter, "adapt_pre_tool_use",
        lambda _self, event: HookResponse(output={"action": "allow"}),
    )

    response = OpenCodeAdapter().adapt_pre_tool_use(_primary_tool_event())

    assert response.output["action"] == "allow"
    assert called["checked"] is False


def test_backstop_fails_closed_when_the_binding_check_itself_errors(monkeypatch):
    def _raise(*_a, **_k):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", _raise)

    response = OpenCodeAdapter().adapt_pre_tool_use(_child_tool_event())

    assert response.output["action"] == "deny"
    assert _CHILD_BINDING_BACKSTOP_EMITTER in response.output["reason"]


# ---------------------------------------------------------------------------
# (2) bind -- gate 1012, adapter side
# ---------------------------------------------------------------------------

def test_subagent_start_binds_from_the_parents_message_part_updated_event(monkeypatch):
    captured = {}

    def fake_bind(**kwargs):
        captured.update(kwargs)
        return {"status": "applied"}

    monkeypatch.setattr("gaia.store.writer.bind_harness_child_session", fake_bind)

    raw = {
        "event": "message.part.updated",
        "sessionID": "ses-parent",
        "callID": "call-child-1",
        "state": {"metadata": {"sessionId": "child-ses-1"}},
    }

    OpenCodeAdapter().adapt_subagent_start(raw)

    assert captured == {
        "dispatch_tool_use_id": "call-child-1",
        "harness_agent_id": "child-ses-1",
    }


def test_subagent_start_never_binds_a_session_created_shaped_payload(monkeypatch):
    """session.created carries no callID -- it is not even routed to
    SUBAGENT_START (_EVENT_TYPES has no entry for it), but this asserts the
    binding call ITSELF only ever fires when a callID is present, so the
    absence holds even if a future transport change forwarded it anyway."""
    called = {"bound": False}

    def fail_if_called(**_k):
        called["bound"] = True
        return {"status": "applied"}

    monkeypatch.setattr("gaia.store.writer.bind_harness_child_session", fail_if_called)

    raw = {
        "event": "session.created",
        "sessionID": "child-ses-1",
    }

    OpenCodeAdapter().adapt_subagent_start(raw)

    assert called["bound"] is False


def test_subagent_start_never_binds_when_child_session_id_is_missing(monkeypatch):
    called = {"bound": False}

    def fail_if_called(**_k):
        called["bound"] = True
        return {"status": "applied"}

    monkeypatch.setattr("gaia.store.writer.bind_harness_child_session", fail_if_called)

    raw = {
        "event": "message.part.updated",
        "sessionID": "ses-parent",
        "callID": "call-child-1",
        "state": {"metadata": {}},
    }

    OpenCodeAdapter().adapt_subagent_start(raw)

    assert called["bound"] is False
