"""A user's wrapper counts as the CLI it wraps only when the user declares it.

Gaia ships no wrapper name of its own: a launcher the user installed around
``gh`` inherits ``gh``'s gates and publish guard through ``GAIA_CLI_ALIASES``,
and with nothing declared the same launcher is just an unknown command.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOKS_DIR = _REPO_ROOT / "hooks"
for entry in (str(_REPO_ROOT), str(_HOOKS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from modules.security import tiers as tiers_module  # noqa: E402
from modules.security.mutative_verbs import detect_mutative_command  # noqa: E402
from modules.security.publish_attribution_guard import (  # noqa: E402
    check as check_publish_attribution,
)

WRAPPER = "ghwrap"
FOOTER = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"

# Actions gh gates only through its anchors, so the wrapper reaches consent
# through the alias or not at all.
PUBLISHING_ACTIONS = [
    f"{WRAPPER} workflow run ci.yml --ref main",
    f"{WRAPPER} run rerun 123456",
]
READS = [
    f"{WRAPPER} pr view 42",
    f"{WRAPPER} run view 123456",
    f"{WRAPPER} workflow list",
]


@pytest.fixture(autouse=True)
def fresh_classifier(monkeypatch):
    monkeypatch.delenv("GAIA_CLI_ALIASES", raising=False)
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    yield
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


def _declare(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GAIA_CLI_ALIASES", value)


def _body_file(tmp_path, text: str) -> str:
    path = tmp_path / "body.md"
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize("command", PUBLISHING_ACTIONS)
def test_declared_wrapper_needs_the_consent_gh_needs(monkeypatch, command):
    gh_form = "gh" + command[len(WRAPPER):]
    assert detect_mutative_command(gh_form).is_mutative, gh_form

    _declare(monkeypatch, f"{WRAPPER}=gh")
    result = detect_mutative_command(command)
    assert result.is_mutative, f"{command!r} ran free under a declared alias"


@pytest.mark.parametrize("command", READS)
def test_declared_wrapper_reads_stay_free(monkeypatch, command):
    _declare(monkeypatch, f"{WRAPPER}=gh")
    assert not detect_mutative_command(command).is_mutative, command


@pytest.mark.parametrize("command", PUBLISHING_ACTIONS)
def test_undeclared_wrapper_inherits_nothing(command):
    assert not detect_mutative_command(command).is_mutative, (
        f"{command!r} took gh's anchors with no alias declared"
    )


def test_no_wrapper_name_is_built_in():
    assert not detect_mutative_command("ghx workflow run ci.yml").is_mutative


def test_an_alias_never_removes_a_cli_own_gates(monkeypatch):
    _declare(monkeypatch, "gh=kubectl")
    assert detect_mutative_command("gh workflow run ci.yml").is_mutative


def test_malformed_entries_are_ignored(monkeypatch):
    _declare(monkeypatch, f" , =gh, broken, {WRAPPER} = gh ,x=")
    assert detect_mutative_command(PUBLISHING_ACTIONS[0]).is_mutative


def test_declared_wrapper_is_held_to_the_publish_attribution_guard(monkeypatch, tmp_path):
    body = _body_file(tmp_path, f"Summary.\n\n{FOOTER}\n")
    command = f"{WRAPPER} -C {tmp_path} pr create --title t --body-file {body}"

    _declare(monkeypatch, f"{WRAPPER}=gh")
    allowed, reason = check_publish_attribution(command, str(tmp_path))
    assert not allowed, "a declared gh wrapper published Claude attribution"
    assert reason


def test_undeclared_wrapper_is_not_read_as_gh_by_the_guard(tmp_path):
    body = _body_file(tmp_path, f"Summary.\n\n{FOOTER}\n")
    command = f"{WRAPPER} pr create --title t --body-file {body}"
    allowed, _ = check_publish_attribution(command, str(tmp_path))
    assert allowed
