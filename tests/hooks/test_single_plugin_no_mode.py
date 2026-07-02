#!/usr/bin/env python3
"""Option A (single-plugin purge) invariant test.

The former runtime mode distinction was removed: there is nothing left to
"detect". This test pins the chosen Option A behavior under a *total
detection failure* environment -- no plugin-registry.json and no
CLAUDE_PLUGIN_ROOT -- and asserts that the behavior
is unconditional:

  1. The main-session T3 mutation-safety floor SURVIVES. A T3 command in
     the main session (is_subagent=False -> has_orchestrator_above=False)
     still yields a native ``ask``. This floor is driven solely by
     ``has_orchestrator_above`` and is independent of any plugin mode.
  2. The delegate gate is ON. The orchestrator (main session, no agent_id)
     is restricted to dispatch-only tools -- an investigation tool such as
     Bash is blocked -- with no mode guard in front of it.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import validate_bash_command
from modules.orchestrator.delegate_mode import check_delegate_mode


@pytest.fixture(autouse=True)
def _total_detection_failure(tmp_path, monkeypatch):
    """Simulate an environment with nothing to detect a plugin from."""
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    # Point the plugin data dir at an empty tmp so no plugin-registry.json
    # exists anywhere the resolver could find one.
    monkeypatch.setattr(
        "modules.core.paths.get_plugin_data_dir", lambda: tmp_path
    )
    yield


def test_main_session_t3_still_yields_native_ask():
    """Floor survives: a main-session T3 command routes to native ask."""
    result = validate_bash_command(
        "terraform apply -auto-approve",
        is_subagent=False,
        session_id="no-mode-session",
    )
    assert not result.allowed
    out = result.block_response["hookSpecificOutput"]
    assert out["permissionDecision"] == "ask", (
        "The main-session T3 mutation-safety floor must return a native "
        "'ask' even with no plugin detected -- it is independent of mode."
    )
    # Native ask, not the orchestrator deny+approval_id path.
    assert "approval_id:" not in out["permissionDecisionReason"]


def test_delegate_gate_is_on_for_orchestrator():
    """Delegate gate active with no mode guard: Bash blocked for orchestrator."""
    payload = {
        "session_id": "no-mode-session",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        # No agent_id -> orchestrator (main session) context.
    }
    result = check_delegate_mode("Bash", payload)
    assert result.blocked, (
        "The delegate gate must restrict the orchestrator to dispatch-only "
        "tools unconditionally (no runtime mode guard)."
    )
