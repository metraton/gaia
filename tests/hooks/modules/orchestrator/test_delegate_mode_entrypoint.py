"""Delegate mode observed through the real PreToolUse entry point.

The unit tests next door exercise ``check_delegate_mode`` in-process. These run
the actual ``hooks/pre_tool_use.py`` script as a subprocess with the payload
shape Claude Code sends, so the decision is read off the hook's real stdout and
exit code rather than asserted against the function that produces it.

Four roles are pinned here. A dispatched subagent (``agent_id`` present) is
never stopped by the delegate gate. The orchestrator's main thread is denied
under BOTH spellings it can arrive in (no ``agent_type`` at all, and
``agent_type`` naming the orchestrator, which is what Gaia's own ``agent:``
setting produces) -- losing that distinction would open Bash to the
orchestrator. And a ``--agent <specialist>`` main thread (NAMED_SPECIALIST)
is ALSO denied: this mode runs outside the real dispatch path and never
receives the per-agent context a genuine orchestrator dispatch builds, so
granting it tools would leave it acting with less information than a real
dispatch provides. It must be denied under its OWN reason, never the
orchestrator's "delegate to a specialist" message -- that would tell a
specialist to delegate to itself.
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
NOT_STANDALONE_MARKER = "NOT RUNNABLE STANDALONE"


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


def _bash_payload(command: str = "echo delegate-probe", **identity) -> dict:
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "delegate-entrypoint-probe",
        "tool_name": "Bash",
        "tool_input": {"command": command},
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
def test_orchestrator_non_memory_bash_reaches_cli_only_guard(identity, tmp_path):
    """Non-memory Bash is denied by Phase 0 for either orchestrator spelling.

    An unnamed main thread and one carrying ``agent_type: gaia-orchestrator``
    (what `agent:` in settings.local.json produces) are both the orchestrator.
    """
    code, stdout, stderr = _run_pre_tool_use(_bash_payload(**identity), tmp_path)

    assert code == 2 and _decision(stdout) is None, (
        f"orchestrator non-memory Bash must be a plain denial; got exit={code} stdout={stdout!r} "
        f"stderr={stderr!r}"
    )
    assert "GAIA CLI ONLY" in stdout + stderr


@pytest.mark.parametrize("identity", [{}, {"agent_type": "gaia-orchestrator"}])
def test_orchestrator_can_run_trusted_memory_read(identity, tmp_path):
    command = f"{WORKTREE / 'bin' / 'gaia'} memory stats"
    code, stdout, stderr = _run_pre_tool_use(
        _bash_payload(command=command, **identity), tmp_path
    )
    assert code == 0, f"stdout={stdout!r} stderr={stderr!r}"
    assert "GAIA CLI ONLY" not in stdout + stderr


def test_named_specialist_main_thread_denied_with_its_own_reason(tmp_path):
    """`claude --agent developer`: main thread, no agent_id, not the orchestrator.

    Inverted on purpose from the prior assertion (this mode is out of Gaia's
    design -- agents are used through the orchestrator, never invoked
    standalone): Bash is denied, but the denial must be the truthful,
    specialist-specific reason, never the orchestrator's "delegate to a
    specialist" message -- which would be absurd for a specialist to receive.
    """
    code, stdout, stderr = _run_pre_tool_use(
        _bash_payload(agent_type="developer"), tmp_path
    )

    assert _decision(stdout) == "deny", (
        f"a named specialist's main thread must be denied Bash; got exit={code} "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    assert NOT_STANDALONE_MARKER in stdout, (
        f"the denial must be the named-specialist reason; stdout={stdout!r}"
    )
    assert DELEGATION_MARKER not in stdout, (
        "a named specialist must never receive the orchestrator's "
        "'delegate to a specialist' message"
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
