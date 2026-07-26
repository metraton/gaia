"""Delegate mode observed through the real PreToolUse entry point.

The unit tests next door exercise ``check_delegate_mode`` in-process. These run
the actual ``hooks/pre_tool_use.py`` script as a subprocess with the payload
shape Claude Code sends, so the decision is read off the hook's real stdout and
exit code rather than asserted against the function that produces it.

Two properties are pinned here, and the second is the one that must never
regress: a ``--agent <specialist>`` main thread is NOT stopped by the delegate
gate, and the orchestrator still is -- under BOTH spellings its main thread can
arrive in (no ``agent_type`` at all, and ``agent_type`` naming the orchestrator,
which is what Gaia's own ``agent:`` setting produces).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[4]
PRE_TOOL_USE = WORKTREE / "hooks" / "pre_tool_use.py"

DELEGATION_MARKER = "DELEGATION REQUIRED"


def _run_pre_tool_use(payload: dict, tmp_path: Path):
    """Run the real hook script on ``payload``; return (exit_code, stdout, stderr)."""
    env = os.environ.copy()
    # Keep the hook off the developer's own install: no plugin-dir detection,
    # and every path it may write to points inside the test's tmp dir.
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["CLAUDE_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    env["GAIA_DATA_DIR"] = str(tmp_path / "gaia-data")

    proc = subprocess.run(
        [sys.executable, str(PRE_TOOL_USE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(WORKTREE),
        timeout=90,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _bash_payload(**identity) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "delegate-entrypoint-probe",
        "tool_name": "Bash",
        "tool_input": {"command": "echo delegate-probe"},
        **identity,
    }


def _decision(stdout: str):
    """Extract permissionDecision from the hook's stdout, or None."""
    if not stdout.strip():
        return None
    try:
        return json.loads(stdout).get("hookSpecificOutput", {}).get(
            "permissionDecision"
        )
    except json.JSONDecodeError:
        return None


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param({}, id="unnamed-main-thread"),
        pytest.param({"agent_type": "gaia-orchestrator"}, id="named-orchestrator"),
    ],
)
def test_orchestrator_still_cannot_run_bash(identity, tmp_path):
    """The barrier holds: the orchestrator's Bash is denied either way it arrives.

    An unnamed main thread and one carrying ``agent_type: gaia-orchestrator``
    (what `agent:` in settings.local.json produces) are both the orchestrator.
    """
    code, stdout, stderr = _run_pre_tool_use(_bash_payload(**identity), tmp_path)

    assert _decision(stdout) == "deny", (
        f"orchestrator Bash must be denied; got exit={code} stdout={stdout!r} "
        f"stderr={stderr!r}"
    )
    assert DELEGATION_MARKER in stdout, (
        "the denial must be the delegate-mode block, not some other refusal"
    )


def test_named_specialist_main_thread_can_run_bash(tmp_path):
    """`claude --agent developer`: main thread, no agent_id, not the orchestrator.

    This is the production defect: the delegate gate read the absent agent_id as
    orchestrator-ness and denied Bash to the specialist the user named.
    """
    code, stdout, stderr = _run_pre_tool_use(
        _bash_payload(agent_type="developer"), tmp_path
    )

    assert DELEGATION_MARKER not in stdout + stderr, (
        f"a named specialist must not hit the delegate gate; exit={code} "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    assert _decision(stdout) != "deny", (
        f"a T0 command from a named specialist must not be denied; "
        f"stdout={stdout!r}"
    )


def test_dispatched_subagent_can_run_bash(tmp_path):
    """The pre-existing subagent path is unchanged by the taxonomy."""
    code, stdout, stderr = _run_pre_tool_use(
        _bash_payload(agent_id="a1234567890abcdef", agent_type="developer"),
        tmp_path,
    )

    assert DELEGATION_MARKER not in stdout + stderr, (
        f"exit={code} stdout={stdout!r} stderr={stderr!r}"
    )
