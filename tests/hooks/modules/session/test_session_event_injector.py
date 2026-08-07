#!/usr/bin/env python3
"""
Tests for the session-events kernel block (modules/session/session_event_injector.py).

Kernel injection is agnostic to the receiving agent -- pinned here:

  * every dispatch gets the identical last-``_MAX_EVENTS`` digest, with no
    filter keyed to event type or to the agent's name/type (the retired
    ``AGENT_EVENT_FILTERS`` dict and its ``.get(agent, [])`` branch);
  * an agent absent from every registry -- one that does not exist in any
    Gaia agent list -- receives the SAME digest a named specialist does;
  * the heading reads ``# Recent Session Events (last 24h)``, with no
    "Auto-Injected" implementation vocabulary;
  * no events in the session -> no block (``None``), not an empty heading.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.session.session_event_injector import (  # noqa: E402
    build_session_events,
    format_events_summary,
    recent_events,
)

# Agents that appear in NO registry Gaia carries anywhere (task_validator's
# AVAILABLE_AGENTS/META_AGENTS, or anywhere else) -- the exact case the old
# "known project agent" gate excluded outright.
_UNREGISTERED_AGENT = "totally-invented-agent-xyz"

_SAMPLE_EVENTS = [
    {
        "event_type": "git_commit",
        "timestamp": "2026-08-06T13:11:00Z",
        "commit_hash": "a44822fdeadbeef",
        "commit_message": "fix(cert-manager): use kube-dns for dns01 self-check",
    },
    {
        "event_type": "infrastructure_change",
        "timestamp": "2026-08-06T11:32:00Z",
        "command": "kubectl apply -f demo.yaml",
    },
]


def _seed_context(session_dir: Path, events: list) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "context.json").write_text(
        json.dumps({"critical_events": events}), encoding="utf-8",
    )


def test_recent_events_bounds_to_the_last_ten_regardless_of_content():
    events = [{"event_type": "git_commit", "n": i} for i in range(25)]
    bounded = recent_events(events)
    assert len(bounded) == 10
    assert bounded[-1]["n"] == 24


@pytest.mark.parametrize("agent_type", [
    "gaia-verifier",
    "gaia-orchestrator",
    _UNREGISTERED_AGENT,
    "cloud-troubleshooter",
])
def test_build_session_events_is_identical_for_any_agent(
    tmp_path, monkeypatch, agent_type,
):
    """No branch on agent name/type: every dispatch gets the same digest.

    ``build_session_events`` no longer takes an agent identity at all -- the
    parametrization over agent names exists to illustrate that removing it
    was correct, not to define the property (the property is that the
    function's signature carries no agent parameter to branch on).
    """
    from modules.core import paths as core_paths

    session_dir = tmp_path / "session" / agent_type
    monkeypatch.setattr(core_paths, "get_session_dir", lambda: session_dir)
    _seed_context(session_dir, _SAMPLE_EVENTS)

    result = build_session_events({"subagent_type": agent_type})

    assert result is not None
    assert result.startswith("# Recent Session Events (last 24h)\n")
    assert "cert-manager" in result
    assert "kubectl apply" in result


def test_heading_carries_no_implementation_vocabulary(tmp_path, monkeypatch):
    from modules.core import paths as core_paths

    session_dir = tmp_path / "session"
    monkeypatch.setattr(core_paths, "get_session_dir", lambda: session_dir)
    _seed_context(session_dir, _SAMPLE_EVENTS)

    result = build_session_events({"subagent_type": "developer"})

    assert result.splitlines()[0] == "# Recent Session Events (last 24h)"
    assert "Auto-Injected" not in result


def test_no_events_yields_none(tmp_path, monkeypatch):
    from modules.core import paths as core_paths

    session_dir = tmp_path / "session"
    monkeypatch.setattr(core_paths, "get_session_dir", lambda: session_dir)
    _seed_context(session_dir, [])

    assert build_session_events({"subagent_type": _UNREGISTERED_AGENT}) is None


def test_no_context_file_yields_none(tmp_path, monkeypatch):
    from modules.core import paths as core_paths

    session_dir = tmp_path / "session-missing"
    monkeypatch.setattr(core_paths, "get_session_dir", lambda: session_dir)

    assert build_session_events({"subagent_type": _UNREGISTERED_AGENT}) is None


def test_format_events_summary_renders_every_recognized_type():
    summary = format_events_summary(_SAMPLE_EVENTS)
    assert "Commit a44822f: fix(cert-manager)" in summary
    assert "Infrastructure: kubectl apply -f demo.yaml" in summary


def test_no_agent_event_filters_symbol_remains():
    """The per-agent event-type allowlist is gone, not merely unused."""
    import modules.session.session_event_injector as mod

    assert not hasattr(mod, "AGENT_EVENT_FILTERS")
