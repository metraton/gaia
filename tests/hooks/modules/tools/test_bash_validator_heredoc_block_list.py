#!/usr/bin/env python3
"""A substitution in an unquoted heredoc body is held to the permanent block list.

The body of an unquoted heredoc is shell text: ``$(...)`` in it runs. The
substitution guard of the block list judged each body as written, without the
wrapper peel a command typed alone gets, so ``$(sudo dd ...)`` read as an
approvable T3 chain, with a dialog the user could answer, instead of the
categorical refusal ``sudo dd ...`` gets on its own.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

DD = "dd if=/dev/zero of=/dev/sda"

BLOCKED_BODIES = [
    DD,
    f"sudo {DD}",
    f"env {DD}",
    f"X=1 {DD}",
    f"timeout 10 {DD}",
    "sudo mkfs.ext4 /dev/sda1",
    f"echo $(sudo {DD})",
]


def _validate(command, *, is_subagent):
    from modules.tools.bash_validator import BashValidator

    return BashValidator().validate(
        command, is_subagent=is_subagent, session_id="t",
        agent_type="gaia-planner" if is_subagent else "",
    )


def _unquoted_heredoc(body_line):
    return f"cat <<EOF\nnote: $({body_line})\nEOF"


@pytest.mark.parametrize("is_subagent", [False, True], ids=["primary", "subagent"])
@pytest.mark.parametrize("body", BLOCKED_BODIES)
def test_blocked_command_in_an_unquoted_body_is_blocked_not_asked(body, is_subagent):
    result = _validate(_unquoted_heredoc(body), is_subagent=is_subagent)
    assert result.allowed is False
    assert "blocked by security policy" in result.reason, result.reason


@pytest.mark.parametrize("body", BLOCKED_BODIES)
def test_blocked_command_in_a_backtick_body_is_blocked_not_asked(body):
    result = _validate(f"cat <<EOF\nnote: `{body}`\nEOF", is_subagent=False)
    assert result.allowed is False
    assert "blocked by security policy" in result.reason, result.reason


@pytest.mark.parametrize("body", BLOCKED_BODIES[:6])
def test_the_same_command_typed_alone_is_blocked(body):
    result = _validate(body, is_subagent=False)
    assert result.allowed is False
    assert "blocked by security policy" in result.reason, result.reason


def test_blocked_command_in_a_quoted_body_stays_prose():
    command = f"cat <<'EOF'\nnote: $(sudo {DD})\nEOF"
    assert _validate(command, is_subagent=False).allowed is True


@pytest.mark.parametrize("is_subagent", [False, True], ids=["primary", "subagent"])
@pytest.mark.parametrize("body", ["date", "git rev-parse HEAD", "sudo ls /root"])
def test_read_only_substitution_in_an_unquoted_body_runs_free(body, is_subagent):
    result = _validate(_unquoted_heredoc(body), is_subagent=is_subagent)
    assert result.allowed is True, result.reason


def test_mutative_but_not_blocked_substitution_still_asks_a_signature():
    result = _validate(_unquoted_heredoc("sudo kubectl delete pod web"), is_subagent=False)
    assert result.allowed is False
    assert "blocked by security policy" not in result.reason, result.reason
