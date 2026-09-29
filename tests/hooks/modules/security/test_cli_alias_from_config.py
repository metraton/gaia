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
from modules.security.blocked_commands import is_blocked_command  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402
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


# Built from parts so this file never spells the irreversible command itself.
_REPO_DELETE = " ".join(("repo", "del" + "ete", "o/r", "--yes"))


def test_declared_wrapper_inherits_gh_permanent_blocks(monkeypatch):
    assert is_blocked_command(f"gh {_REPO_DELETE}").is_blocked

    _declare(monkeypatch, f"{WRAPPER}=gh")
    for command in (
        f"{WRAPPER} {_REPO_DELETE}",
        f"FOO=1 {WRAPPER} {_REPO_DELETE}",
        f"sudo {WRAPPER} {_REPO_DELETE}",
        f"/usr/local/bin/{WRAPPER} {_REPO_DELETE}",
    ):
        assert is_blocked_command(command).is_blocked, command


def test_declared_wrapper_is_blocked_at_the_bash_boundary(monkeypatch):
    _declare(monkeypatch, f"{WRAPPER}=gh")
    result = BashValidator().validate(
        f"{WRAPPER} {_REPO_DELETE}", is_subagent=True, session_id="s-782",
        agent_type="developer",
    )
    assert not result.allowed
    assert not result.approval_id, "a declared wrapper got an approvable path to a gh block"


# Positions off the head of the command where gh's unanchored rule still
# matches; a declared wrapper must be held there too (D107).
EMBEDDED_FORMS = [
    "echo $({cli} {op})",
    "echo `{cli} {op}`",
    "(sudo {cli} {op})",
    "true; {cli} {op}",
    "true && {cli} {op}",
]


def _validate_as_subagent(command: str):
    return BashValidator().validate(
        command, is_subagent=True, session_id="s-782-embedded",
        agent_type="developer",
    )


@pytest.mark.parametrize("form", EMBEDDED_FORMS)
def test_embedded_wrapper_is_blocked_at_the_floor_wherever_gh_is(monkeypatch, form):
    assert is_blocked_command(form.format(cli="gh", op=_REPO_DELETE)).is_blocked

    _declare(monkeypatch, f"{WRAPPER}=gh")
    command = form.format(cli=WRAPPER, op=_REPO_DELETE)
    assert is_blocked_command(command).is_blocked, command


@pytest.mark.parametrize("form", EMBEDDED_FORMS)
def test_embedded_wrapper_is_denied_at_the_bash_boundary_like_gh(monkeypatch, form):
    gh_result = _validate_as_subagent(form.format(cli="gh", op=_REPO_DELETE))
    assert not gh_result.allowed and not gh_result.approval_id

    _declare(monkeypatch, f"{WRAPPER}=gh")
    command = form.format(cli=WRAPPER, op=_REPO_DELETE)
    result = _validate_as_subagent(command)
    assert not result.allowed, command
    assert not result.approval_id, f"{command!r} got an approvable path to a gh block"


@pytest.mark.parametrize("form", EMBEDDED_FORMS)
def test_embedded_wrapper_resolves_only_as_a_whole_token(monkeypatch, form):
    _declare(monkeypatch, f"{WRAPPER}=gh")
    command = form.format(cli=f"{WRAPPER}er", op=_REPO_DELETE)
    assert not is_blocked_command(command).is_blocked, command


def test_undeclared_wrapper_gets_no_gh_block():
    assert not is_blocked_command(f"{WRAPPER} {_REPO_DELETE}").is_blocked


def test_declared_alias_cycle_terminates(monkeypatch):
    _declare(monkeypatch, "a1=a2,a2=a1")
    assert not detect_mutative_command("a1 status").is_mutative
    assert not is_blocked_command("a1 status").is_blocked


def test_undeclared_wrapper_is_not_read_as_gh_by_the_guard(tmp_path):
    body = _body_file(tmp_path, f"Summary.\n\n{FOOTER}\n")
    command = f"{WRAPPER} pr create --title t --body-file {body}"
    allowed, _ = check_publish_attribution(command, str(tmp_path))
    assert allowed
