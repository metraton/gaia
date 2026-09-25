"""A plugin-prefixed agent name is the same agent; a foreign prefix is not.

Claude Code renames a plugin's agents to ``<plugin>:<name>``. Every identity
check Gaia makes must treat ``gaia:<name>`` exactly like ``<name>`` and must
keep refusing ``other:<name>``, which is another plugin's agent that happens to
share the bare name. Each check is exercised through the ingress that feeds it
in production: the Claude Code adapter's ``parse_event`` for hook payloads,
``TaskValidator`` for a dispatch, and ``GAIA_DISPATCH_AGENT`` for the CLI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
for _path in (REPO_ROOT / "hooks", REPO_ROOT / "bin"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from gaia.agent_identity import PLUGIN_NAMESPACE, canonical_agent_name  # noqa: E402

GAIA_PREFIX = f"{PLUGIN_NAMESPACE}:"
FOREIGN_PREFIX = "other:"


def _spellings(bare: str) -> list:
    """(spelling, same_agent) pairs for one bare agent name."""
    return [
        (bare, True),
        (GAIA_PREFIX + bare, True),
        (FOREIGN_PREFIX + bare, False),
    ]


def _parsed_payload(**fields) -> dict:
    """A Claude Code PreToolUse payload as the hooks receive it after parsing."""
    from adapters.claude_code import ClaudeCodeAdapter

    raw = {
        "hook_event_name": "PreToolUse",
        "session_id": "s-identity",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        **fields,
    }
    return ClaudeCodeAdapter().parse_event(json.dumps(raw)).payload


# --------------------------------------------------------------------------- #
# The normalization point
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "name, expected",
    [
        ("developer", "developer"),
        ("gaia:developer", "developer"),
        (" ", " "),
        ("other:developer", "other:developer"),
        ("other:gaia:developer", "other:gaia:developer"),
        ("gaia:", "gaia:"),
        ("", ""),
        (None, ""),
    ],
)
def test_canonical_agent_name_strips_only_gaias_namespace(name, expected):
    assert canonical_agent_name(name) == expected


def test_plugin_namespace_is_the_name_the_plugin_ships_under():
    plugin_json = json.loads(
        (REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert plugin_json["name"] == PLUGIN_NAMESPACE
    assert (REPO_ROOT / "build" / f"{PLUGIN_NAMESPACE}.manifest.json").is_file()


# --------------------------------------------------------------------------- #
# Hook-side checks, fed through ClaudeCodeAdapter.parse_event
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("agent_type, is_orchestrator", _spellings("gaia-orchestrator"))
def test_orchestrator_main_thread_keeps_its_role_and_bash_lane(agent_type, is_orchestrator):
    from modules.orchestrator.delegate_mode import (
        SessionRole,
        check_delegate_mode,
        classify_session_role,
    )
    from modules.security.gaia_cli_only_guard import is_orchestrator_role

    payload = _parsed_payload(agent_type=agent_type)
    expected_role = SessionRole.ORCHESTRATOR if is_orchestrator else SessionRole.NAMED_SPECIALIST

    assert classify_session_role(payload) is expected_role
    assert is_orchestrator_role(payload) is is_orchestrator
    result = check_delegate_mode("Bash", payload)
    assert result.blocked is not is_orchestrator
    if not is_orchestrator:
        assert "NOT RUNNABLE STANDALONE" in result.reason


@pytest.mark.parametrize("agent_type, allowed", _spellings("gaia-operator"))
def test_subagent_memory_write_guard_admits_the_operator_under_either_spelling(
    agent_type, allowed,
):
    from modules.security import subagent_memory_write_guard as guard

    command = "gaia memory add --type project --slug s --content c"
    assert guard.is_memory_write_attempt(command)
    payload = _parsed_payload(agent_id="a0123456789abcdef", agent_type=agent_type)

    ok, _reason = guard.check(command, is_subagent=True, agent_type=payload["agent_type"])
    assert ok is allowed


@pytest.mark.parametrize("agent_type, is_gaia_agent", _spellings("developer"))
def test_subagent_stop_roster_recognises_gaia_agents_under_either_spelling(
    agent_type, is_gaia_agent,
):
    """A Gaia agent read as native skips the contract gate entirely."""
    from adapters.claude_code import ClaudeCodeAdapter

    payload = _parsed_payload(
        hook_event_name="SubagentStop", agent_id="a0123456789abcdef", agent_type=agent_type,
    )
    roster = ClaudeCodeAdapter()._get_gaia_agent_names()

    assert roster
    assert (payload["agent_type"] in roster) is is_gaia_agent


@pytest.mark.parametrize("agent_type, _same", _spellings("gaia-operator"))
def test_dispatch_identity_exported_to_the_cli_is_canonical(agent_type, _same):
    from adapters.tool_policy import build_dispatch_identity_command

    payload = _parsed_payload(agent_id="a0123456789abcdef", agent_type=agent_type)
    command = build_dispatch_identity_command("gaia memory list", payload["agent_type"])

    assert f"GAIA_DISPATCH_AGENT={canonical_agent_name(agent_type)}" in command


# --------------------------------------------------------------------------- #
# Dispatch-side check: the Agent/Task tool's subagent_type
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("subagent_type, known", _spellings("developer"))
def test_task_validator_accepts_and_canonicalizes_the_dispatched_agent(subagent_type, known):
    from modules.tools.task_validator import TaskValidator

    result = TaskValidator().validate({"subagent_type": subagent_type, "prompt": "read the README"})

    assert result.allowed is known
    if known:
        assert result.agent_name == "developer"


# --------------------------------------------------------------------------- #
# CLI-side guards, fed through GAIA_DISPATCH_AGENT
# --------------------------------------------------------------------------- #

def _raises(check) -> bool:
    try:
        check()
    except PermissionError:
        return True
    return False


@pytest.mark.parametrize("agent, allowed", _spellings("gaia-operator"))
def test_curated_memory_write_guard(monkeypatch, agent, allowed):
    from gaia.store.writer import _assert_dispatch_can_write_memory

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)
    assert _raises(_assert_dispatch_can_write_memory) is not allowed


@pytest.mark.parametrize("agent, allowed", _spellings("gaia-orchestrator"))
def test_curator_state_transition_guard(monkeypatch, agent, allowed):
    from gaia.state.permissions import _assert_dispatch_can_advance_state

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)
    assert _raises(lambda: _assert_dispatch_can_advance_state("plans")) is not allowed


@pytest.mark.parametrize("agent, allowed", _spellings("gaia-planner"))
def test_plan_content_author_guard(monkeypatch, agent, allowed):
    from gaia.state.permissions import _assert_dispatch_can_write_content

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)
    assert _raises(lambda: _assert_dispatch_can_write_content("plans")) is not allowed


@pytest.mark.parametrize("agent, allowed", _spellings("developer"))
def test_handoff_writer_guard(monkeypatch, agent, allowed):
    from gaia.state.permissions import is_handoff_writer
    from gaia.store.writer import _assert_dispatch_can_write_handoff

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)
    assert is_handoff_writer(agent) is allowed
    assert _raises(_assert_dispatch_can_write_handoff) is not allowed


@pytest.mark.parametrize("agent, allowed", _spellings("developer"))
def test_evidence_producer_guard(monkeypatch, agent, allowed):
    from gaia.evidence.store import _assert_dispatch_can_write_evidence

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)
    assert _raises(_assert_dispatch_can_write_evidence) is not allowed


@pytest.mark.parametrize("agent, _same", _spellings("developer"))
def test_approval_requester_is_sealed_under_the_canonical_name(monkeypatch, agent, _same):
    """The hook matches a grant against the canonical agent_type it parsed."""
    import cli.approvals as approvals_mod

    monkeypatch.setenv("GAIA_DISPATCH_AGENT", agent)
    _session, requester = approvals_mod._requester_identity(
        SimpleNamespace(agent_id=None, session_id=None),
    )
    assert requester == canonical_agent_name(agent)
    assert requester == _parsed_payload(agent_type=agent)["agent_type"]
