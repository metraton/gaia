#!/usr/bin/env python3
"""A shell wrapper launched by a subagent is refused, not put to the user.

AC-21: `bash -c`, `sh -c` or `eval` from a subagent gets a deny with no
approval_id whose message states the correct form (run the command directly,
or commit the logic to a script file). The same command from the orchestrator
keeps its native ask dialog. The caller is identified by `is_subagent`, which
the adapter derives from the hook payload's `agent_id`.
"""

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

import pytest

from modules.tools.bash_validator import BashValidator
from modules.tools.hook_response import (
    read_permission_decision,
    read_permission_reason,
)


@pytest.fixture(autouse=True)
def _isolated_gaia_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia"))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia" / "gaia.db"))


# Each wrapper carries a non-mutative inner command: the shapes that would run
# free if unwrapped, which is where the verdict used to depend on nothing.
WRAPPERS = [
    "bash -c 'gh pr checks 42 --repo metraton/gaia'",
    "sh -c 'echo hello'",
    "eval 'echo hello'",
    "cd /tmp && bash -c 'echo hello'",
]


def _validate(command: str, *, is_subagent: bool):
    return BashValidator().validate(
        command, is_subagent=is_subagent, session_id="s-test", agent_type="developer",
    )


@pytest.mark.parametrize("command", WRAPPERS)
def test_subagent_wrapper_is_denied_with_the_correct_form(command):
    result = _validate(command, is_subagent=True)

    assert not result.allowed
    assert result.approval_id is None
    assert read_permission_decision(result.block_response) == "deny"

    reason = read_permission_reason(result.block_response).lower()
    assert "directly" in reason
    assert "script" in reason


@pytest.mark.parametrize("command", WRAPPERS)
def test_orchestrator_wrapper_keeps_its_dialog(command):
    result = _validate(command, is_subagent=False)

    assert not result.allowed
    assert read_permission_decision(result.block_response) == "ask"


def test_subagent_direct_command_is_not_affected():
    result = _validate("gh pr checks 42 --repo metraton/gaia", is_subagent=True)

    assert result.allowed


def test_blocked_inner_command_stays_a_permanent_block_for_both_callers():
    command = "bash -c 'kubectl delete namespace production'"

    for is_subagent in (True, False):
        result = _validate(command, is_subagent=is_subagent)
        assert not result.allowed
        assert result.block_response is None
