"""A user's wrapper counts as the CLI it wraps only when the user declares it.

Gaia ships no wrapper name of its own: a launcher the user installed around
``gh`` inherits ``gh``'s gates and publish guard through a ``kind=tool`` row
with ``replaces=gh``, and with no row the same launcher is just an unknown command.
"""

from __future__ import annotations

import re
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

# Remote triggers: gated by command shape on any binary, alias or not.
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
def fresh_classifier():
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()
    yield
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


def _body_file(tmp_path, text: str) -> str:
    path = tmp_path / "body.md"
    path.write_text(text)
    return str(path)


@pytest.mark.parametrize("command", PUBLISHING_ACTIONS)
def test_declared_wrapper_needs_the_consent_gh_needs(declare_alias, command):
    gh_form = "gh" + command[len(WRAPPER):]
    assert detect_mutative_command(gh_form).is_mutative, gh_form

    declare_alias(f"{WRAPPER}=gh")
    result = detect_mutative_command(command)
    assert result.is_mutative, f"{command!r} ran free under a declared alias"


@pytest.mark.parametrize("command", READS)
def test_declared_wrapper_reads_stay_free(declare_alias, command):
    declare_alias(f"{WRAPPER}=gh")
    assert not detect_mutative_command(command).is_mutative, command


@pytest.mark.parametrize("command", PUBLISHING_ACTIONS)
def test_undeclared_wrapper_pays_the_consent_for_a_remote_trigger(command):
    """The trigger shape is classified by command shape, so no alias is needed."""
    assert detect_mutative_command(command).is_mutative, (
        f"{command!r} ran free: a remote trigger asks on any binary"
    )
    assert tiers_module.classify_command_tier(command).name == "T3_BLOCKED"


@pytest.mark.parametrize("command", READS)
def test_undeclared_wrapper_reads_stay_free(command):
    assert not detect_mutative_command(command).is_mutative, command


def test_no_wrapper_name_is_built_in():
    """Gaia knows no wrapper by name: nothing resolves one, and no source names one."""
    from modules.security.cli_aliases import as_wrapped_cli

    assert as_wrapped_cli("ghx pr list") is None

    wrapper_name = re.compile(r"\bghx\b")
    offenders = [
        str(path)
        for root in ("hooks", "gaia", "bin")
        for path in (_REPO_ROOT / root).rglob("*")
        if path.suffix in {".py", ".md", ".sh"}
        and "__pycache__" not in path.parts
        and any(
            wrapper_name.search(line) and "--binary=ghx" not in line
            for line in path.read_text(errors="ignore").splitlines()
        )
    ]
    assert offenders == []


def test_an_alias_never_removes_a_cli_own_gates(declare_alias):
    declare_alias("gh=kubectl")
    assert detect_mutative_command("gh workflow run ci.yml").is_mutative


def test_tool_rows_that_alias_nothing_are_ignored(declare_alias):
    """Replaces the malformed-entries test: a tool row names its CLI or none, and the writer refuses the rest."""
    from modules.security import cli_aliases

    declare_alias("gh=gh", "plain=", f"{WRAPPER}=gh")
    assert cli_aliases.declared_aliases() == {WRAPPER: "gh"}
    assert detect_mutative_command(PUBLISHING_ACTIONS[0]).is_mutative


def test_declared_wrapper_is_held_to_the_publish_attribution_guard(declare_alias, tmp_path):
    body = _body_file(tmp_path, f"Summary.\n\n{FOOTER}\n")
    command = f"{WRAPPER} -C {tmp_path} pr create --title t --body-file {body}"

    declare_alias(f"{WRAPPER}=gh")
    allowed, reason = check_publish_attribution(command, str(tmp_path))
    assert not allowed, "a declared gh wrapper published Claude attribution"
    assert reason


# Built from parts so this file never spells the irreversible command itself.
_REPO_DELETE = " ".join(("repo", "del" + "ete", "o/r", "--yes"))


def test_declared_wrapper_inherits_gh_permanent_blocks(declare_alias):
    assert is_blocked_command(f"gh {_REPO_DELETE}").is_blocked

    declare_alias(f"{WRAPPER}=gh")
    for command in (
        f"{WRAPPER} {_REPO_DELETE}",
        f"FOO=1 {WRAPPER} {_REPO_DELETE}",
        f"sudo {WRAPPER} {_REPO_DELETE}",
        f"/usr/local/bin/{WRAPPER} {_REPO_DELETE}",
    ):
        assert is_blocked_command(command).is_blocked, command


def test_declared_wrapper_is_blocked_at_the_bash_boundary(declare_alias):
    declare_alias(f"{WRAPPER}=gh")
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
def test_embedded_wrapper_is_blocked_at_the_floor_wherever_gh_is(declare_alias, form):
    assert is_blocked_command(form.format(cli="gh", op=_REPO_DELETE)).is_blocked

    declare_alias(f"{WRAPPER}=gh")
    command = form.format(cli=WRAPPER, op=_REPO_DELETE)
    assert is_blocked_command(command).is_blocked, command


@pytest.mark.parametrize("form", EMBEDDED_FORMS)
def test_embedded_wrapper_is_denied_at_the_bash_boundary_like_gh(declare_alias, form):
    gh_result = _validate_as_subagent(form.format(cli="gh", op=_REPO_DELETE))
    assert not gh_result.allowed and not gh_result.approval_id

    declare_alias(f"{WRAPPER}=gh")
    command = form.format(cli=WRAPPER, op=_REPO_DELETE)
    result = _validate_as_subagent(command)
    assert not result.allowed, command
    assert not result.approval_id, f"{command!r} got an approvable path to a gh block"


@pytest.mark.parametrize("form", EMBEDDED_FORMS)
def test_embedded_wrapper_resolves_only_as_a_whole_token(declare_alias, form):
    declare_alias(f"{WRAPPER}=gh")
    command = form.format(cli=f"{WRAPPER}er", op=_REPO_DELETE)
    assert not is_blocked_command(command).is_blocked, command


def test_undeclared_wrapper_gets_no_gh_block():
    assert not is_blocked_command(f"{WRAPPER} {_REPO_DELETE}").is_blocked


def test_declared_alias_cycle_terminates(declare_alias):
    declare_alias("gh=kubectl", "kubectl=gh")
    assert not detect_mutative_command("gh status").is_mutative
    assert not is_blocked_command("gh status").is_blocked


@pytest.mark.parametrize("command", [
    f"{WRAPPER} pr create --title t --body-file {{body}}",
    "forgectl pr create --title t --body-file {body}",
    "glab mr create --title t --description-file {body}",
    "forgectl -C /work/tree pr create --title t --body-file {body}",
    "forgectl -R o/r pr edit 42 --body-file={body}",
])
def test_publish_attribution_guard_reads_the_change_request_body_of_any_binary(tmp_path, command):
    tainted = _body_file(tmp_path, f"Summary.\n\n{FOOTER}\n")
    assert not check_publish_attribution(command.format(body=tainted), str(tmp_path))[0]
    clean = tmp_path / "clean.md"
    clean.write_text("Summary.\n", encoding="utf-8")
    assert check_publish_attribution(command.format(body=clean), str(tmp_path))[0]
