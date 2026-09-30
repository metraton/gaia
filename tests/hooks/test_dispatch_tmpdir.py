"""A dispatched agent's tool temporaries land under Gaia's tmp root, not /tmp.

Origin: a full-suite pytest run inside a dispatched agent wrote its basetemp to
/tmp (a 2 GB tmpfs), filled it, and the harness lost every shell's output. The
Claude Code half is proven here; the OpenCode half runs through the real plugin
in tests/integration/test_opencode_shell_env.py.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"
for _p in [str(HOOKS_DIR), str(REPO_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from adapters.claude_code import ClaudeCodeAdapter

PROBE = "python3 -c \"import os, tempfile; print(os.environ.get('TMPDIR', '<unset>')); print(tempfile.gettempdir())\""


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    return ClaudeCodeAdapter()


def _rewritten(adapter, agent_id, command=PROBE):
    hook_data = {
        "session_id": "sess-tmpdir",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if agent_id:
        hook_data.update(agent_id=agent_id, agent_type="developer")
    response = adapter._adapt_bash("Bash", {"command": command}, hook_data=hook_data)
    output = response.output if isinstance(response.output, dict) else {}
    return output.get("hookSpecificOutput", {}).get("updatedInput", {}).get("command")


def _child_tmpdirs(command):
    run = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                         env={**os.environ, "TMPDIR": "/tmp"})
    assert run.returncode == 0, run.stderr
    exported, used = run.stdout.strip().splitlines()
    return exported, used


def test_dispatch_tmpdir_claude_subagent_shell_uses_gaia_tmp(adapter):
    from gaia.paths import dispatch_tmp_dir, tmp_dir

    command = _rewritten(adapter, "a1b2c3d0f1e2d3c4b")
    assert command is not None, "subagent command was not rewritten"
    exported, used = _child_tmpdirs(command)

    expected = dispatch_tmp_dir("a1b2c3d0f1e2d3c4b")
    assert exported == str(expected)
    assert Path(exported).parent == tmp_dir()
    # tempfile ignores a TMPDIR that does not exist and silently falls back to
    # /tmp, so the directory has to be there before the command runs.
    assert used == str(expected)
    assert re.fullmatch(r"[0-9a-f]{12}", expected.name)


def test_dispatch_tmpdir_is_per_dispatch_stable_and_absent_for_main_session(adapter):
    first = _child_tmpdirs(_rewritten(adapter, "a1b2c3d0f1e2d3c4b"))[0]
    again = _child_tmpdirs(_rewritten(adapter, "a1b2c3d0f1e2d3c4b"))[0]
    other = _child_tmpdirs(_rewritten(adapter, "a9f8e7d6c5b4a3210"))[0]
    assert first == again
    assert first != other
    main_session = _rewritten(adapter, "")
    assert main_session is None or "TMPDIR" not in main_session
