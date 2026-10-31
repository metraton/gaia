"""The resume point is the orchestrator's: a subagent may read the session, never write its resume point."""

import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "bin"))

from modules.security import subagent_resume_point_guard as guard  # noqa: E402
from modules.security.tiers import SecurityTier  # noqa: E402
from modules.tools.bash_validator import BashValidator  # noqa: E402

SET = "gaia session resume-point set --session-id s --text t"

# Spellings the shell rejoins into the same `resume-point` word.
SPELLINGS = [
    ("plain", SET),
    ("backslash-escaped", "gaia session resume-poin\\t set --session-id s --text t"),
    ("single-quoted-part", "gaia session 'resume'-point set --session-id s --text t"),
    ("empty-quotes-inside", 'gaia session resume""-point set --session-id s --text t'),
    ("redispatched", f"python3 /x/bin/{SET}"),
    ("chained", f"cd /tmp && {SET}"),
]

# A command word only the shell can resolve: never free for a subagent.
VARIABLE_FORMS = [
    ("variable-command", "G=gaia; $G session resume-point set --session-id s --text t"),
    ("braced-variable-command", "${G} session resume-point set --session-id s --text t"),
]


def _validate(command, *, is_subagent):
    return BashValidator().validate(command, is_subagent=is_subagent, agent_type="developer" if is_subagent else "")


def _assert_categorical_refusal(result, case_id):
    assert result.allowed is False, case_id
    assert result.tier == SecurityTier.T3_BLOCKED, case_id
    assert result.rule == "floor.subagent-resume-point", f"{case_id}: refused by another rule: {result.reason}"
    assert result.reason.split("\n", 1)[0] == guard.REJECTION_MESSAGE, case_id
    assert result.approval_id is None, case_id
    assert result.block_response is None, case_id


# Subcommand words only the shell can resolve, and session verbs the map does
# not declare: nothing proves these are not the resume-point write.
UNPROVEN_VERBS = [
    ("variable-verb", "S=set; gaia session resume-point $S --session-id s --text t"),
    ("variable-group-verb", "gaia session $R set --session-id s --text t"),
    ("backtick-verb", "gaia session resume-point `printf set` --session-id s --text t"),
    ("glob-verb", "gaia session resume-po?nt set --session-id s --text t"),
    ("variable-group", "gaia $X resume-point set --session-id s --text t"),
    ("undeclared-session-verb", "gaia session frobnicate --session-id s"),
]

# A shell-resolved word after `gaia` may spell a host consent verb, whose
# categorical guard runs ahead of this one in the validator; both refuse with
# no approval.
REFUSED_FIRST_BY_THE_CONSENT_VERB_GUARD = {"variable-group", "variable-group-verb", "glob-verb"}
REFUSED_FIRST_BY_THE_LITERAL_WORDS_RULE = {"variable-verb", "backtick-verb"}


@pytest.mark.parametrize(
    "case_id,command",
    SPELLINGS + VARIABLE_FORMS + UNPROVEN_VERBS,
    ids=[c for c, _ in SPELLINGS + VARIABLE_FORMS + UNPROVEN_VERBS],
)
def test_a_subagent_resume_point_write_is_refused_outright(case_id, command):
    assert guard.check(command, is_subagent=True) == (False, guard.REJECTION_MESSAGE), case_id
    result = _validate(command, is_subagent=True)
    if case_id in REFUSED_FIRST_BY_THE_CONSENT_VERB_GUARD | REFUSED_FIRST_BY_THE_LITERAL_WORDS_RULE:
        assert result.allowed is False and result.approval_id is None and result.block_response is None
        assert "not approvable" in result.reason, result.reason
    else:
        _assert_categorical_refusal(result, case_id)


@pytest.mark.parametrize("case_id,command", UNPROVEN_VERBS[:4], ids=[c for c, _ in UNPROVEN_VERBS[:4]])
def test_an_unresolved_session_verb_is_not_free_for_the_orchestrator(case_id, command):
    result = _validate(command, is_subagent=False)
    assert not (result.allowed and result.tier == SecurityTier.T0_READ_ONLY), f"{case_id}: {result.reason}"


def test_a_symlinked_gaia_binary_is_refused_for_a_subagent(monkeypatch):
    from modules.security import gaia_cli_only_guard

    monkeypatch.setattr(gaia_cli_only_guard, "is_trusted_gaia_binary", lambda token: token == "/opt/tools/g")
    _assert_categorical_refusal(
        _validate("/opt/tools/g session resume-point set --session-id s --text t", is_subagent=True), "symlink"
    )


def test_a_declared_gaia_wrapper_is_refused_for_a_subagent(declare_alias):
    declare_alias("gx=gaia")
    _assert_categorical_refusal(
        _validate("gx session resume-point set --session-id s --text t", is_subagent=True), "alias"
    )


@pytest.mark.parametrize("command", [
    "sudo gaia session resume-point set --session-id s --text t",
    "timeout 5 gaia session resume-point set --session-id s --text t",
    "FOO=1 gaia session resume-point set --session-id s --text t",
    "nice -n 5 python3 /x/bin/gaia session resume-point set --session-id s --text t",
])
def test_a_wrapped_resume_point_write_is_refused(command):
    _assert_categorical_refusal(_validate(command, is_subagent=True), command)


# A path that ends in `/gaia` is an argument, never the program: searches over
# Gaia's own tree are reads, whatever words follow the path.
SEARCHES_OVER_A_GAIA_PATH = [
    "rg -n pattern /home/x/gaia --glob '*.py'",
    "rg -n session /home/x/ws/me/gaia -g '*.md'",
    "grep -rn pattern /home/x/gaia --include '*.py'",
    "grep -rn resume-point /home/x/gaia session",
    "find /home/x/gaia -name '*.py'",
    "find /home/x/gaia -path '*/session/*' -name '*.py'",
]


@pytest.mark.parametrize("command", SEARCHES_OVER_A_GAIA_PATH)
def test_a_search_over_a_gaia_path_is_free_for_a_subagent(command):
    assert guard.check(command, is_subagent=True) == (True, None), command
    result = _validate(command, is_subagent=True)
    assert result.allowed and result.tier == SecurityTier.T0_READ_ONLY, f"{command}: {result.reason}"


@pytest.mark.parametrize("case_id,command", SPELLINGS[:4], ids=[c for c, _ in SPELLINGS[:4]])
def test_the_orchestrator_writes_its_resume_point_free(case_id, command):
    result = _validate(command, is_subagent=False)
    assert result.allowed is True, f"{case_id}: {result.reason}"
    assert result.tier == SecurityTier.T0_READ_ONLY, case_id


@pytest.mark.parametrize("command", [
    "gaia session snapshot --session-id s --json",
    "gaia session preview",
    "gaia session resume-point set --help",
    "gaia session 'resume'-point set --help",
])
def test_a_subagent_session_read_is_free(command):
    result = _validate(command, is_subagent=True)
    assert result.allowed is True, result.reason
    assert result.tier == SecurityTier.T0_READ_ONLY


def _leaf_paths(parser, prefix=()):
    subparsers = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subparsers:
        yield prefix
        return
    for action in subparsers:
        for name, child in action.choices.items():
            yield from _leaf_paths(child, prefix + (name,))


def test_every_gaia_session_verb_is_classified():
    from cli import session

    root = argparse.ArgumentParser()
    session.register(root.add_subparsers())
    declared = set(_leaf_paths(root))

    assert declared == {("session",) + path for path in guard.SESSION_VERBS}, (
        "a `gaia session` verb was added or removed: classify it in "
        "subagent_resume_point_guard.SESSION_VERBS before it rides the group's read-only tier"
    )
