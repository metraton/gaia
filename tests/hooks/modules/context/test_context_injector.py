"""Tests for how build_project_context surfaces context anchors.

build_project_context() no longer saves anchors itself: at PreToolUse:Task
dispatch time the host has not yet assigned this dispatch its agent_id (see
anchor_tracker.py's module docstring), so the anchors it extracts here travel
forward -- via the telemetry snapshot this function returns -- to whichever
caller reaches SubagentStart, where agent_id becomes available and the
caller can finally call save_anchors() with the full (session_id,
agent_type, agent_id) key.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

import modules.context.context_injector as context_injector


@pytest.fixture
def stub_context_payload(monkeypatch):
    """Stub build_context_payload with a fixed payload carrying one anchor."""
    payload = {
        "project_knowledge": {
            "terraform_infrastructure": {
                "layout": {"base_path": "./qxo-monorepo/terraform"},
            },
        },
        "metadata": {},
        "write_permissions": {},
        "agent_contract_handoff": {},
        "surface_routing": {},
        "historical_context": {},
    }

    class _FakeModule:
        @staticmethod
        def build_context_payload(agent_name, user_task):
            return payload

    monkeypatch.setitem(sys.modules, "tools.context.context_provider", _FakeModule())
    return payload


@pytest.fixture
def stub_reminder_and_events(monkeypatch):
    """Avoid touching gaia.db from these unit tests -- neutral no-op stubs."""
    monkeypatch.setattr(
        context_injector, "build_context_update_reminder", lambda *a, **k: ""
    )


class TestBuildProjectContextAnchorTelemetry:
    """Anchors extracted during context build must surface in the returned
    telemetry snapshot -- the handoff to the caller's own save-at-SubagentStart
    step -- and build_project_context itself must never call save_anchors."""

    def test_anchors_surface_in_telemetry(
        self, stub_context_payload, stub_reminder_and_events,
    ):
        _context_text, telemetry = context_injector.build_project_context(
            {"subagent_type": "platform-architect", "prompt": "investigate terraform"},
            ["platform-architect"],
        )

        assert "qxo-monorepo/terraform" in telemetry.get("anchors", []), (
            "build_project_context must carry extracted anchors forward in "
            "its telemetry snapshot so the caller can save them once "
            "agent_id is known (at SubagentStart), instead of saving them "
            "itself here where agent_id does not yet exist."
        )

    def test_does_not_call_save_anchors_itself(
        self, stub_context_payload, stub_reminder_and_events, monkeypatch,
    ):
        """build_project_context must not import/call save_anchors directly
        -- that call now lives at SubagentStart, the only place agent_id is
        available. A regression here would resurrect the two-part key."""
        assert not hasattr(context_injector, "save_anchors"), (
            "context_injector must not import save_anchors: saving anchors "
            "here (before agent_id exists) is exactly the bug this rekey fixes."
        )

    def test_no_context_payload_yields_no_anchors(self, monkeypatch):
        """A non-project agent (context build skipped) yields no telemetry
        and therefore no anchors -- nothing to carry forward."""
        context_text, telemetry = context_injector.build_project_context(
            {"subagent_type": "not-a-project-agent", "prompt": "irrelevant"},
            ["platform-architect"],
        )
        assert context_text is None
        assert telemetry == {}
