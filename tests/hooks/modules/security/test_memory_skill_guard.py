"""The orchestrator's memory writes wait for Skill('memory') in the current session.

Each property is driven through the host entry points: Claude Code's PreToolUse
and PostToolUse adapters, OpenCode's tool.execute.after, and the shared
SessionStart maintenance both hosts run on compaction. Every test pairs its
property with the refusal it contrasts against, so none can pass on a tree where
orchestrator memory writes are never refused.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
for _path in (REPO_ROOT, REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

SESSION = "ses-memory-skill-guard"
SUBAGENT_ID = "a" + "5" * 16
WRITES = (
    "gaia memory add --type project --initiative gaia --name probe --body probe",
    "gaia memory append probe_slug --body more",
    "gaia memory reclassify probe_slug --class anchor",
    "gaia memory link probe_slug other_slug",
)
ADD = WRITES[0]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setattr("gaia.store.writer.is_harness_session_bound", lambda _session: True)


def _orchestrator_bash(command: str) -> dict:
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_pre_tool_use(adapter.parse_event(json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": SESSION,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_use_id": "toolu-bash",
        "cwd": "/",
    })))
    if isinstance(response.output, str):
        return {"decision": "deny", "reason": response.output, "approval_id": response.approval_id}
    specific = response.output.get("hookSpecificOutput", {})
    return {
        "decision": specific.get("permissionDecision", "allow" if response.exit_code == 0 else "deny"),
        "reason": specific.get("permissionDecisionReason", ""),
        "approval_id": response.approval_id,
    }


def _claude_skill(skill: str, *, agent_id: str = "") -> None:
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": SESSION,
        "tool_name": "Skill",
        "tool_input": {"skill": skill},
        "tool_response": {"success": True, "commandName": skill},
        "tool_use_id": "toolu-skill",
        "cwd": "/",
    }
    if agent_id:
        payload.update(agent_id=agent_id, agent_type="developer")
    adapter = ClaudeCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps(payload)))


def _opencode_skill(name: str) -> None:
    adapter = OpenCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.after",
        "sessionID": SESSION,
        "callID": "call-skill",
        "tool": "skill",
        "args": {"name": name},
        "result": {"output": "skill body"},
    })))


def _compact(tmp_path: Path) -> None:
    from modules.session.session_lifecycle import SessionStart, run_start_maintenance

    run_start_maintenance(SessionStart(
        session_id=SESSION, source="compact", is_headless=True,
        pinned_build=None, workspace_dir=tmp_path,
    ))


def _refused(verdict: dict) -> bool:
    return verdict["decision"] == "deny" and "MEMORY_SKILL_REQUIRED" in verdict["reason"]


@pytest.mark.parametrize("command", WRITES)
def test_write_is_refused_without_the_skill(command):
    verdict = _orchestrator_bash(command)

    assert _refused(verdict), verdict
    assert "Skill('memory')" in verdict["reason"]
    assert "identical command" in verdict["reason"]
    assert verdict["approval_id"] is None


@pytest.mark.parametrize("skill", ("gaia:memory", "memory"))
def test_write_is_allowed_after_claude_code_loads_the_skill(skill):
    assert _refused(_orchestrator_bash(ADD))

    _claude_skill(skill)

    assert _orchestrator_bash(ADD)["decision"] != "deny"


def test_write_is_allowed_after_opencode_loads_the_skill():
    validator = BashValidator()
    assert not validator.validate(ADD, session_id=SESSION).allowed

    _opencode_skill("memory")

    assert validator.validate(ADD, session_id=SESSION).allowed


def test_another_skill_does_not_unlock_the_write():
    _claude_skill("gaia:agent-protocol")

    assert _refused(_orchestrator_bash(ADD))


def test_compaction_drops_the_loaded_skill(tmp_path):
    _claude_skill("gaia:memory")
    assert _orchestrator_bash(ADD)["decision"] != "deny"

    _compact(tmp_path)

    assert _refused(_orchestrator_bash(ADD))
    _claude_skill("gaia:memory")
    assert _orchestrator_bash(ADD)["decision"] != "deny"


@pytest.mark.parametrize("command", (
    "gaia memory search probe",
    "gaia memory show probe_slug",
    "gaia memory list --type project",
    "gaia memory add --help",
))
def test_reads_and_help_stay_free(command):
    assert _refused(_orchestrator_bash(ADD))

    assert _orchestrator_bash(command)["decision"] != "deny"


def test_subagents_keep_the_subagent_guard():
    assert _refused(_orchestrator_bash(ADD))
    validator = BashValidator()

    operator = validator.validate(ADD, is_subagent=True, session_id=SESSION, agent_type="gaia-operator")
    developer = validator.validate(ADD, is_subagent=True, session_id=SESSION, agent_type="developer")

    assert operator.allowed
    assert not developer.allowed
    assert "subagent dispatch context" in developer.reason
    assert "MEMORY_SKILL_REQUIRED" not in developer.reason


def test_a_subagent_loading_the_skill_does_not_unlock_the_orchestrator():
    _claude_skill("gaia:memory", agent_id=SUBAGENT_ID)

    assert _refused(_orchestrator_bash(ADD))


def test_unreadable_database_fails_open_with_a_warning(tmp_path, monkeypatch, caplog):
    assert _refused(_orchestrator_bash(ADD))
    unreadable = tmp_path / "not-a-database"
    unreadable.mkdir()
    monkeypatch.setenv("GAIA_DB", str(unreadable))

    with caplog.at_level(logging.WARNING):
        result = BashValidator().validate(ADD, session_id=SESSION)

    assert result.allowed
    assert any("memory skill guard failed open" in r.getMessage() for r in caplog.records)
