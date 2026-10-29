"""``_find_composition_hazard``: every shell form beyond one plain invocation is named.

``2>&1`` is a redirect like any other: the orchestrator's lane refuses every
fd form, and the tool already returns stderr.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import gaia_cli_only_guard as guard

_GAIA = "/abs/path/bin/gaia"
_ORCHESTRATOR_PAYLOAD: dict = {}


@pytest.fixture(autouse=True)
def _trust_fake_binary(monkeypatch):
    monkeypatch.setattr(
        guard, "is_trusted_gaia_binary", lambda token: token == _GAIA
    )


def _hazard(command: str):
    return guard._find_composition_hazard(command)


def _check(command: str):
    return guard.check(command, _ORCHESTRATOR_PAYLOAD)


@pytest.mark.parametrize(
    "command,hazard",
    [
        ("gaia doctor 2>&1", "shell redirect (`>`) detected"),
        ("gaia doctor 1>&2", "shell redirect (`>`) detected"),
        ("gaia doctor >&2", "shell redirect (`>`) detected"),
        ("gaia doctor &> x", "shell redirect (`&>`) detected"),
        ("gaia doctor < x", "shell redirect (`<`) detected"),
        ("gaia memory show $SLUG", "shell expansion (`$`) detected"),
        ('gaia memory search "${Q}"', "shell expansion (`$`) detected"),
        ("gaia memory show x*", "glob or brace expansion (`*`) detected"),
        ("gaia doctor {--fix,--json}", "glob or brace expansion (`{`) detected"),
        ("gaia memory show ~", "tilde expansion (`~`) detected"),
        ("gaia memory show --workspace=~/x", "tilde expansion (`~`) detected"),
    ],
)
def test_shell_forms_are_named(command, hazard):
    assert _hazard(command) == hazard


@pytest.mark.parametrize(
    "command",
    [
        "gaia memory search 'a > b | c & d'",
        "gaia memory search 'x*' --json",
        'gaia memory search "{a,b} ~ x?"',
        "gaia memory search 'cost $5'",
        "gaia memory show a~b",
        r"gaia memory search a\>b",
    ],
)
def test_quoted_or_escaped_shell_characters_are_words(command):
    assert _hazard(command) is None


def test_fd_duplication_denied_end_to_end_through_check():
    allowed, reason = _check(f"{_GAIA} doctor 2>&1")
    assert allowed is False
    assert "not approvable" in reason


def test_trailing_bare_background_still_flagged():
    assert _hazard("gaia doctor &") == "bare background operator (`&`) detected"


def test_mid_command_bare_background_still_flagged():
    assert (
        _hazard("gaia doctor & echo x")
        == "bare background operator (`&`) detected"
    )


def test_trailing_bare_background_still_denied_end_to_end():
    allowed, reason = _check(f"{_GAIA} doctor &")
    assert allowed is False
    assert "bare background operator" in reason


def test_double_ampersand_composition_still_not_a_hazard():
    """`&&` between two trusted gaia invocations is composition, not a
    background hazard -- the fd-dup fix must not disturb this."""
    assert _hazard(f"{_GAIA} doctor && {_GAIA} status") is None


def test_double_ampersand_between_trusted_invocations_still_allowed():
    allowed, reason = _check(f"{_GAIA} doctor && {_GAIA} status")
    assert allowed is True
    assert reason is None


def test_pipe_to_untrusted_binary_still_denied():
    allowed, reason = _check(f"{_GAIA} doctor | rm -rf /tmp/x")
    assert allowed is False
    assert reason is not None


def test_process_substitution_still_flagged_as_hazard():
    assert (
        _hazard("gaia doctor <(cat /etc/passwd)")
        == "process substitution (`<(` / `>(`) detected"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
