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


# A mutative inner command changes nothing for the subagent: the wrapper is
# refused so the inner command runs directly and gets its own T3 gate, instead
# of the user being asked to sign a wrapped command.
MUTATIVE_WRAPPERS = [
    "bash -c 'git push origin x'",
    "sh -c 'git push origin x'",
    "eval 'git push origin x'",
]


@pytest.mark.parametrize("command", MUTATIVE_WRAPPERS)
def test_subagent_mutative_wrapper_is_denied_with_the_correct_form(command):
    result = _validate(command, is_subagent=True)

    assert not result.allowed
    assert result.approval_id is None
    assert read_permission_decision(result.block_response) == "deny"

    reason = read_permission_reason(result.block_response).lower()
    assert "directly" in reason
    assert "script" in reason


@pytest.mark.parametrize("command", MUTATIVE_WRAPPERS)
def test_orchestrator_mutative_wrapper_keeps_its_dialog(command):
    result = _validate(command, is_subagent=False)

    assert not result.allowed
    assert read_permission_decision(result.block_response) == "ask"


# A wrapper behind a chain reaches the compound-T3 deny before the wrapper
# refusal, so that message has to name the correct form on its own.
COMPOUND_MUTATIVE_WRAPPERS = [
    "cd /tmp && bash -c 'git push origin x'",
    "cd /tmp && sh -c 'git push origin x'",
    "cd /tmp && eval 'git push origin x'",
]


@pytest.mark.parametrize("command", COMPOUND_MUTATIVE_WRAPPERS)
def test_subagent_compound_mutative_wrapper_deny_names_the_correct_form(command):
    result = _validate(command, is_subagent=True)

    assert not result.allowed
    assert read_permission_decision(result.block_response) == "deny"

    reason = read_permission_reason(result.block_response).lower()
    assert "each command directly" in reason
    assert "no wrapper" in reason


# Every spelling that hands an sh-family shell a string as its program meets
# the refusal the canonical `bash -c` gets.
SHELL_STRING_SPELLINGS = [
    "bash -c 'echo hello'",
    "sh -c 'echo hello'",
    "bash -lc 'echo hello'",
    "bash -xc 'echo hello'",
    "bash -e -c 'echo hello'",
    "bash --login -c 'echo hello'",
    "bash -o pipefail -c 'echo hello'",
    "FOO=1 bash -c 'echo hello'",
    "timeout 10 bash -c 'echo hello'",
    "env -i FOO=1 bash -c 'echo hello'",
    "sudo -u root bash -c 'echo hello'",
    "nice -n 5 sh -c 'echo hello'",
    "/usr/local/bin/bash -lc 'echo hello'",
    "ksh -c 'echo hello'",
    "zsh -c 'echo hello'",
    "dash -c 'echo hello'",
    "bash -lc 'git push origin x'",
]


@pytest.mark.parametrize("command", SHELL_STRING_SPELLINGS)
def test_subagent_every_shell_string_spelling_is_refused(command):
    result = _validate(command, is_subagent=True)

    assert not result.allowed
    assert result.approval_id is None
    assert read_permission_decision(result.block_response) == "deny"
    assert read_permission_reason(result.block_response).startswith(
        "Shell wrapper refused"
    )


@pytest.mark.parametrize("command", SHELL_STRING_SPELLINGS)
def test_orchestrator_every_shell_string_spelling_keeps_its_dialog(command):
    result = _validate(command, is_subagent=False)

    assert not result.allowed
    assert read_permission_decision(result.block_response) == "ask"


# A shell given a script path, or only asked about itself, runs no string.
NOT_SHELL_STRINGS = [
    "bash --version",
    "bash -n script.sh",
    "gcc -c main.c",
]


@pytest.mark.parametrize("command", NOT_SHELL_STRINGS)
def test_subagent_shell_without_a_command_string_is_not_refused(command):
    result = _validate(command, is_subagent=True)

    assert result.allowed


def test_blocked_payload_behind_a_flag_cluster_stays_a_permanent_block():
    command = "bash -lc 'kubectl delete namespace production'"

    for is_subagent in (True, False):
        result = _validate(command, is_subagent=is_subagent)
        assert not result.allowed
        assert result.block_response is None


def test_subagent_direct_command_is_not_affected():
    result = _validate("gh pr checks 42 --repo metraton/gaia", is_subagent=True)

    assert result.allowed


def test_blocked_inner_command_stays_a_permanent_block_for_both_callers():
    command = "bash -c 'kubectl delete namespace production'"

    for is_subagent in (True, False):
        result = _validate(command, is_subagent=is_subagent)
        assert not result.allowed
        assert result.block_response is None
