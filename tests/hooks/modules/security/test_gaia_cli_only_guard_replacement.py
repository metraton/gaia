"""The orchestrator's denial of a non-gaia binary names what to run instead, and stays categorical."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
if str(_REPO / "hooks") not in sys.path:
    sys.path.insert(0, str(_REPO / "hooks"))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402

_ORCHESTRATOR_PAYLOAD: dict = {}


def _denial(command: str) -> str:
    allowed, reason = guard.check(command, _ORCHESTRATOR_PAYLOAD)
    assert not allowed
    assert "not approvable" in reason
    assert "approval_id" not in reason
    return reason


@pytest.mark.parametrize("command", ["date", "timedatectl", "/usr/bin/date +%H:%M"])
def test_a_clock_read_points_to_gaia_now_and_an_absolute_at(command):
    reason = _denial(command)

    assert "gaia now" in reason
    assert "absolute --at" in reason
    assert "--in" not in reason


@pytest.mark.parametrize("command", ["gh pr checks 42", "glab ci status", "bb pipeline"])
def test_a_forge_read_points_to_a_specialist_and_gaia_contract_view(command):
    reason = _denial(command)

    assert "PR/CI state is a specialist's evidence" in reason
    assert "gaia contract view" in reason


def test_any_other_binary_points_to_the_help_lanes():
    reason = _denial("ls /tmp")

    assert "gaia --help" in reason
    assert "gaia now" not in reason


def test_the_claude_code_pre_tool_use_hook_delivers_the_replacement(tmp_path):
    env = os.environ.copy()
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["HOME"] = str(tmp_path / "home")
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    env["GAIA_DATA_DIR"] = str(tmp_path / "gaia-data")
    env["GAIA_DB"] = str(tmp_path / "gaia-data" / "gaia.db")
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": "replacement-probe",
        "tool_name": "Bash",
        "tool_input": {"command": "date"},
    }

    result = subprocess.run(
        [sys.executable, str(_REPO / "hooks" / "pre_tool_use.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=env, cwd=str(_REPO), timeout=90,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 2, output
    assert "gaia now" in output and "absolute --at" in output, output
    assert "--in" not in output
