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
    assert result.reason == guard.REJECTION_MESSAGE, f"{case_id}: refused by another rule: {result.reason}"
    assert result.approval_id is None, case_id
    assert result.block_response is None, case_id


@pytest.mark.parametrize("case_id,command", SPELLINGS + VARIABLE_FORMS, ids=[c for c, _ in SPELLINGS + VARIABLE_FORMS])
def test_a_subagent_resume_point_write_is_refused_outright(case_id, command):
    _assert_categorical_refusal(_validate(command, is_subagent=True), case_id)


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
