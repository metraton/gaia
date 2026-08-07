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
  2. The delegate gate is ON, but its shape changed since this test was
     first written (commits f8db56c/2e37984/84f6af3): the orchestrator
     (main session, no agent_id) now gets ONE deliberate Bash lane
     (``_orchestrator_bash_is_allowed``), with enforcement moved to the
     gaia-cli-only guard that lane feeds into (Phase 0 of
     ``bash_validator.validate()``). A non-gaia-CLI command such as ``ls``
     must still be denied there, unconditionally and with no mode guard in
     front of it.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import validate_bash_command
from modules.orchestrator.delegate_mode import check_delegate_mode
from modules.security import gaia_cli_only_guard


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
    """Security moved, did not disappear: delegate_mode grants the
    orchestrator ONE Bash lane (`_orchestrator_bash_is_allowed`, commits
    f8db56c/2e37984/84f6af3), so ``check_delegate_mode`` no longer blocks
    Bash for the orchestrator role -- that assertion is retired. What must
    still be unconditionally true is the invariant this test exists to pin:
    an orchestrator command that is NOT the trusted, installed gaia CLI is
    still denied, categorically, by the guard the Bash lane now feeds into
    (``gaia_cli_only_guard``, wired as Phase 0 of ``bash_validator.validate()``).
    """
    payload = {
        "session_id": "no-mode-session",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        # No agent_id -> orchestrator (main session) context.
    }

    # The lane itself is open: delegate_mode no longer blocks Bash for the
    # orchestrator role.
    result = check_delegate_mode("Bash", payload)
    assert not result.blocked, (
        "The orchestrator's Bash lane (_orchestrator_bash_is_allowed) must "
        "let Bash reach the gaia-cli-only guard -- delegate_mode itself no "
        "longer restricts it."
    )

    # CRITICAL: the security invariant survives -- moved, not removed. A
    # non-allowlisted command (here, a bare `ls`) must still be denied
    # outright by gaia_cli_only_guard, not approvable.
    allowed, reason = gaia_cli_only_guard.check("ls", payload)
    assert not allowed, (
        "gaia_cli_only_guard must categorically deny a non-gaia-CLI command "
        f"for the orchestrator role. Got allowed=True, reason={reason!r}"
    )
