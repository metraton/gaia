"""``_find_composition_hazard``: only literal characters pass; every other one is named."""

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
        ("gaia doctor >&2", "shell redirect (`>`) detected"),
        ("gaia doctor &> x", "shell redirect (`&>`) detected"),
        ("gaia doctor < x", "shell redirect (`<`) detected"),
        ("gaia doctor <(cat /etc/passwd)", "process substitution (`<(` / `>(`) detected"),
        ("gaia doctor &", "bare background operator (`&`) detected"),
        ("gaia doctor & echo x", "bare background operator (`&`) detected"),
        ("gaia doctor && gaia status", "shell character (`&`) outside quotes detected"),
        ("gaia doctor | gaia status", "shell character (`|`) outside quotes detected"),
        ("gaia doctor ; gaia status", "shell character (`;`) outside quotes detected"),
        ("gaia memory show $SLUG", "shell character (`$`) outside quotes detected"),
        ("gaia memory show x*", "shell character (`*`) outside quotes detected"),
        ("gaia doctor {--fix,--json}", "shell character (`{`) outside quotes detected"),
        ("gaia memory show ~", "shell character (`~`) outside quotes detected"),
        ("gaia memory show a~b", "shell character (`~`) outside quotes detected"),
        (r"gaia memory search a\>b", "shell character (`\\`) outside quotes detected"),
        ("gaia memory search x\\ #y", "shell character (`\\`) outside quotes detected"),
        ("gaia memory list # note", "shell character (`#`) outside quotes detected"),
        ("gaia memory search x!", "shell character (`!`) outside quotes detected"),
        ('gaia memory search "${Q}"', "shell expansion (`$`) detected"),
        ('gaia memory search "$[1+1]"', "shell expansion (`$`) detected"),
        ('gaia memory search "a\\$x"', "backslash inside double quotes detected"),
        ('gaia memory search "`id`"', "command substitution detected"),
        ("gaia memory search 'open", "unterminated quote detected"),
        ('gaia memory search "open', "unterminated quote detected"),
    ],
)
def test_every_non_literal_character_is_named(command, hazard):
    assert _hazard(command) == hazard


@pytest.mark.parametrize(
    "command",
    [
        "gaia memory search 'a > b | c & d ; e # f \\ ` $(x) ~ !'",
        "gaia memory search 'x*' --json",
        'gaia memory search "{a,b} ~ x? # ; | & > < !"',
        'gaia memory search "price $" "cost 5$" "a $ b"',
        "gaia memory search 'say \"hi\"' \"it's\"",
        "gaia notifications add --kind reminder --headline h --at 2030-01-01T10:00:00Z",
        "gaia memory show a_b.c/d:e=f@g,h+i%j-k",
        "gaia memory search diseño",
    ],
)
def test_literal_text_passes(command):
    assert _hazard(command) is None


def test_fd_duplication_denied_end_to_end_through_check():
    allowed, reason = _check(f"{_GAIA} doctor 2>&1")
    assert allowed is False
    assert "not approvable" in reason


def test_composition_of_two_trusted_invocations_is_denied():
    allowed, reason = _check(f"{_GAIA} doctor && {_GAIA} status")
    assert allowed is False
    assert "not approvable" in reason


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
