"""SessionStart attests the agent the host really started; UserPromptSubmit enforces it.

Both hooks are driven the way Claude Code drives them: an event on stdin, JSON on
stdout, with HOME and the Gaia data dir pointed at a temporary tree.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parents[2] / "hooks"
_SESSION_ID = "sess-identity"
_OK_LINE_SUFFIX = " · gaia-orchestrator · build "


@pytest.fixture
def env(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / ".claude").mkdir(parents=True)
    values = {
        key: value for key, value in os.environ.items()
        if key not in {"CLAUDE_PLUGIN_ROOT", "CLAUDE_SESSION_ID", "CLAUDE_PROJECT_DIR",
                       "GAIA_DB", "GAIA_ALLOW_NON_ORCHESTRATOR"}
    }
    values.update({
        "HOME": str(tmp_path / "home"),
        "GAIA_DATA_DIR": str(tmp_path / "data"),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "plugin-data"),
    })
    return workspace, values


def _run(hook: str, event: dict, workspace: Path, env: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / hook)],
        input=json.dumps({"session_id": _SESSION_ID, **event}),
        capture_output=True, text=True, env=env, cwd=str(workspace), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _start(workspace, env, **fields) -> dict:
    return _run("session_start.py",
                {"hook_event_name": "SessionStart", "source": "startup", **fields},
                workspace, env)


def _prompt(workspace, env) -> dict:
    return _run("user_prompt_submit.py",
                {"hook_event_name": "UserPromptSubmit", "prompt": "hola"},
                workspace, env)


def _attestation(env) -> dict:
    registry = Path(env["HOME"]) / ".claude" / "session_registry.json"
    return json.loads(registry.read_text())["sessions"][_SESSION_ID]["identity"]


@pytest.mark.parametrize("agent_type", ["gaia:gaia-orchestrator", "gaia-orchestrator"])
def test_orchestrator_session_shows_ok_line_and_is_attested(env, agent_type):
    workspace, values = env

    response = _start(workspace, values, agent_type=agent_type)

    line = response["systemMessage"].splitlines()[0]
    assert line.startswith("Gaia ") and _OK_LINE_SUFFIX in line, line
    attestation = _attestation(values)
    assert attestation["agent_type"] == "gaia-orchestrator"
    assert attestation["workspace"] == str(workspace)
    assert attestation["build"] and attestation["channel"] and attestation["attested_at"]
    context = response["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("## Identity\n" + line)
    assert "decision" not in _prompt(workspace, values)


@pytest.mark.parametrize("fields", [{"agent_type": "general-purpose"}, {}])
def test_session_without_the_orchestrator_warns_and_blocks_prompts(env, fields):
    workspace, values = env

    response = _start(workspace, values, **fields)

    line = response["systemMessage"].splitlines()[0]
    assert "WARNING" in line and "gaia doctor" in line, line
    assert _attestation(values)["agent_type"] == fields.get("agent_type", "")
    blocked = _prompt(workspace, values)
    assert blocked["decision"] == "block"
    assert "gaia doctor" in blocked["reason"]
    assert "gaia-orchestrator" in blocked["reason"]


def test_explicit_escape_hatch_lets_prompts_through(env):
    workspace, values = env
    _start(workspace, values, agent_type="general-purpose")

    values["GAIA_ALLOW_NON_ORCHESTRATOR"] = "1"

    assert "decision" not in _prompt(workspace, values)


def test_session_never_attested_is_not_blocked(env):
    workspace, values = env

    assert "decision" not in _prompt(workspace, values)


def test_compaction_keeps_the_identity_the_session_started_with(env):
    workspace, values = env
    _start(workspace, values, agent_type="gaia:gaia-orchestrator")

    _start(workspace, values, source="compact")

    assert _attestation(values)["agent_type"] == "gaia-orchestrator"
    assert "decision" not in _prompt(workspace, values)


def test_subagent_event_does_not_replace_the_session_identity(env):
    workspace, values = env
    _start(workspace, values, agent_type="gaia:gaia-orchestrator")

    _start(workspace, values, source="resume", agent_type="developer", agent_id="a1234")

    assert _attestation(values)["agent_type"] == "gaia-orchestrator"
