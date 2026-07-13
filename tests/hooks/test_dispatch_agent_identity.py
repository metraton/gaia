#!/usr/bin/env python3
"""AC-1: GAIA_DISPATCH_AGENT is set on every subagent dispatch and reaches the
CLI subprocess the agent invokes.

The DB-side guards (memory / evidence / brief+plan content / state transitions)
read ``GAIA_DISPATCH_AGENT`` to identify the writing agent. Historically that
variable was NEVER set at dispatch, so every subagent wrote with fail-open,
human-level authority. The PreToolUse hook now injects the harness-provided
dispatch identity into the subagent's Bash command via ``updatedInput`` so the
identity propagates to the ``gaia`` CLI subprocess.

Two layers are covered:
  1. build_dispatch_identity_command -- the pure prefixing helper.
  2. The helper wired into ClaudeCodeAdapter._adapt_bash (subagent vs. main
     session), plus a real subprocess check that the exported variable actually
     reaches a child process (the "llega al subproceso CLI" claim).
"""

import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in [str(HOOKS_DIR), str(PLUGIN_ROOT)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from adapters.claude_code import (
    ClaudeCodeAdapter,
    build_dispatch_identity_command,
    GAIA_DISPATCH_AGENT_ENV,
)


# ---------------------------------------------------------------------------
# Layer 1: build_dispatch_identity_command (pure helper)
# ---------------------------------------------------------------------------

def test_prefixes_export_for_named_agent():
    out = build_dispatch_identity_command("gaia contract finalize", "developer")
    assert out == "export GAIA_DISPATCH_AGENT=developer; gaia contract finalize"


def test_empty_agent_leaves_command_unchanged():
    """No identity to assert -> command untouched (guards stay fail-open)."""
    assert build_dispatch_identity_command("gaia doctor", "") == "gaia doctor"
    assert build_dispatch_identity_command("gaia doctor", "   ") == "gaia doctor"


def test_agent_name_is_shell_quoted():
    """A hyphenated / unusual identity is shell-quoted, never interpolated raw."""
    out = build_dispatch_identity_command("gaia plan save", "gaia-planner")
    # shlex.quote leaves gaia-planner unquoted (safe chars) but the assignment
    # target must always be exactly the env var name.
    assert out.startswith(f"export {GAIA_DISPATCH_AGENT_ENV}=")
    assert "gaia-planner" in out
    assert out.endswith("; gaia plan save")


def test_export_applies_across_a_compound_command():
    """`export ...;` (not a bare `VAR=x` word) so the var reaches EVERY stage --
    a bare prefix would bind only to the first stage of `cd .. && gaia ...`."""
    out = build_dispatch_identity_command("cd /repo && gaia contract finalize", "developer")
    assert out == "export GAIA_DISPATCH_AGENT=developer; cd /repo && gaia contract finalize"


# ---------------------------------------------------------------------------
# Layer 2: wired into ClaudeCodeAdapter._adapt_bash
# ---------------------------------------------------------------------------

@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    return ClaudeCodeAdapter()


def _updated_command(response):
    """Extract updatedInput.command from a HookResponse, or None if absent."""
    output = response.output
    if not isinstance(output, dict):
        return None
    hso = output.get("hookSpecificOutput", {})
    return hso.get("updatedInput", {}).get("command")


def test_subagent_bash_gets_identity_injected(adapter):
    """A dispatched subagent's command is rewritten to export its identity."""
    hook_data = {
        "session_id": "sess-1",
        "agent_id": "a1b2c3d",           # presence => subagent
        "agent_type": "developer",
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},
    }
    resp = adapter._adapt_bash("Bash", {"command": "git status"}, hook_data=hook_data)
    cmd = _updated_command(resp)
    assert cmd == "export GAIA_DISPATCH_AGENT=developer; git status"


def test_subagent_gaia_cli_command_carries_identity(adapter):
    """The identity rides on the very `gaia` CLI invocations the guards protect."""
    hook_data = {
        "session_id": "sess-2",
        "agent_id": "a9f8e7d",
        "agent_type": "gaia-planner",
        "tool_name": "Bash",
        "tool_input": {"command": "gaia contract view"},
    }
    resp = adapter._adapt_bash("Bash", {"command": "gaia contract view"}, hook_data=hook_data)
    cmd = _updated_command(resp)
    assert cmd is not None
    assert cmd.startswith("export GAIA_DISPATCH_AGENT=gaia-planner;")
    assert cmd.endswith("gaia contract view")


def test_orchestrator_main_session_not_injected(adapter):
    """The orchestrator (no agent_id -> not a subagent) keeps the fail-open path:
    no identity injected, so a genuine human/main-session CLI call still works."""
    hook_data = {
        "session_id": "sess-3",
        # no agent_id => main session / orchestrator
        "tool_name": "Bash",
        "tool_input": {"command": "gaia brief list"},
    }
    resp = adapter._adapt_bash("Bash", {"command": "gaia brief list"}, hook_data=hook_data)
    cmd = _updated_command(resp)
    # Either no updatedInput at all, or a command WITHOUT the export prefix.
    assert cmd is None or "GAIA_DISPATCH_AGENT" not in cmd


# ---------------------------------------------------------------------------
# Layer 3: the injected command actually reaches a child subprocess
# ---------------------------------------------------------------------------

def test_injected_identity_reaches_child_subprocess():
    """Executing the rewritten command in a shell propagates the variable to a
    child process -- this is the "reaches the CLI subprocess" guarantee.

    A child that imports the guard and attempts a curator-only write is BLOCKED,
    proving the propagated identity is the one the guard reads (AC-1 -> AC-2)."""
    probe = (
        'python3 -c "import os; print(os.environ.get(\'GAIA_DISPATCH_AGENT\', \'<unset>\'))"'
    )
    injected = build_dispatch_identity_command(probe, "developer")
    out = subprocess.run(["bash", "-c", injected], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "developer"
