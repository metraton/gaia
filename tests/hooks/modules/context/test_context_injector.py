"""Tests for the anchor-saving session_id wiring in build_project_context.

Regression coverage for the anchor-tracking version of Bug B / P-a11d14e0:
save_anchors() must be keyed by the real host session id (event.session_id,
threaded in via the ``session_id`` parameter), not by
get_or_create_session_id()'s synthetic fallback -- the same id SubagentStop
never resolves to, since it prefers the parsed event's session_id.
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
def capture_save_anchors(monkeypatch):
    """Replace save_anchors with a spy that records its call args."""
    captured = {}

    def _fake_save_anchors(session_id, agent_type, anchors):
        captured["session_id"] = session_id
        captured["agent_type"] = agent_type
        captured["anchors"] = anchors
        return None

    monkeypatch.setattr(context_injector, "save_anchors", _fake_save_anchors)
    return captured


@pytest.fixture
def stub_reminder_and_events(monkeypatch):
    """Avoid touching gaia.db from these unit tests -- neutral no-op stubs."""
    monkeypatch.setattr(
        context_injector, "build_context_update_reminder", lambda *a, **k: ""
    )


class TestBuildProjectContextAnchorSessionWiring:
    """save_anchors must be keyed by the real event session_id, not the
    synthetic get_or_create_session_id() fallback."""

    def test_uses_real_session_id_when_provided(
        self, stub_context_payload, capture_save_anchors, stub_reminder_and_events,
        monkeypatch,
    ):
        """The real host session id (as SubagentStop would resolve it via
        event.session_id) must reach save_anchors verbatim."""
        monkeypatch.setattr(
            context_injector, "get_or_create_session_id",
            lambda: "synthetic-should-not-be-used",
        )

        real_session_id = "01830964-78fe-45f5-a059-3f13be3c0ec5"
        context_injector.build_project_context(
            {"subagent_type": "platform-architect", "prompt": "investigate terraform"},
            ["platform-architect"],
            session_id=real_session_id,
        )

        assert capture_save_anchors.get("session_id") == real_session_id, (
            "build_project_context must thread the caller's real session_id "
            "into save_anchors instead of deriving its own synthetic id -- "
            "this is the exact key SubagentStop's load_anchors(event.session_id, "
            "agent_type) must match."
        )
        assert capture_save_anchors.get("agent_type") == "platform-architect"
        assert "qxo-monorepo/terraform" in capture_save_anchors.get("anchors", set())

    def test_falls_back_to_synthetic_when_session_id_omitted(
        self, stub_context_payload, capture_save_anchors, stub_reminder_and_events,
        monkeypatch,
    ):
        """Backward compatibility: a caller that does not pass session_id
        (e.g. a legacy or test caller) still gets a saved anchor file, keyed
        by the synthetic fallback -- degraded, not broken."""
        monkeypatch.setattr(
            context_injector, "get_or_create_session_id",
            lambda: "synthetic-fallback-id",
        )

        context_injector.build_project_context(
            {"subagent_type": "platform-architect", "prompt": "investigate terraform"},
            ["platform-architect"],
        )

        assert capture_save_anchors.get("session_id") == "synthetic-fallback-id"

    def test_empty_session_id_string_falls_back_to_synthetic(
        self, stub_context_payload, capture_save_anchors, stub_reminder_and_events,
        monkeypatch,
    ):
        """An explicitly empty session_id (a caller that resolved no real id)
        must not be saved as the literal empty string -- fall back the same
        way an omitted argument would."""
        monkeypatch.setattr(
            context_injector, "get_or_create_session_id",
            lambda: "synthetic-fallback-id",
        )

        context_injector.build_project_context(
            {"subagent_type": "platform-architect", "prompt": "investigate terraform"},
            ["platform-architect"],
            session_id="",
        )

        assert capture_save_anchors.get("session_id") == "synthetic-fallback-id"
