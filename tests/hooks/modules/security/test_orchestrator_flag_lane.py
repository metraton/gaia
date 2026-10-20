"""The orchestrator lane is closed by default at flag level, not only at verb level.

An admitted phrase matches by prefix, so what follows it is the verb's own
argparse grammar. ``_KNOWN_SAFE_FLAGS`` lists, per admitted phrase, the options
vetted for the orchestrator; any other option is refused. The table is pinned
against the LIVE parsers in bin/cli/*.py, so a flag added to a verb tomorrow
fails here until someone either vets it or withholds it.
"""

import importlib.machinery
import importlib.util
import argparse
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "hooks"))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402
from modules.security.gaia_cli_only_guard import (  # noqa: E402
    ALLOWED_PHRASES,
    _KNOWN_SAFE_FLAGS,
    _validate_known_safe_flags,
)

TRUSTED = "/trusted/node_modules/gaia/bin/gaia"

# Options argparse defines for an admitted phrase that the orchestrator must not
# carry. The first three are signed operations; the others sweep or override.
WITHHELD = {
    ("doctor",): {"--fix"},
    ("memory", "add"): {"--replace"},
    ("memory", "link"): {"--delete"},
    ("approvals", "reject"): {"--all"},
    ("task", "set-status"): {"--override", "--reason"},
    ("notifications", "ack"): {"--all-workspaces"},
}

BASELINES = {
    ("brief", "new"): "brief new demo --headless --title=T",
    ("brief", "edit"): "brief edit demo --headless --field=title --content=X",
    ("brief", "close"): "brief close demo",
    ("brief", "set-status"): "brief set-status demo open",
    ("brief", "set-project"): "brief set-project demo proj",
    ("brief", "ac", "add"): "brief ac add demo --id=AC-1",
    ("brief", "ac", "edit"): "brief ac edit demo --id=AC-1",
    ("brief", "ac", "remove"): "brief ac remove demo --id=AC-1",
    ("brief", "decision", "add"): "brief decision add demo --text=T",
    ("plan", "set-status"): "plan set-status demo active",
    ("plan", "pause"): "plan pause demo --reason=r",
    ("plan", "resume"): "plan resume demo",
    ("plan", "change", "request"): "plan change request demo --reason=r",
    ("plan", "change", "approve"): "plan change approve demo 1",
    ("task", "gate", "reverify"): "task gate reverify demo 1 2 --reason=r",
    ("task", "set-status"): "task set-status demo 1 done",
    ("session", "resume-point", "set"): "session resume-point set --session-id=s --text=t",
    ("notifications", "ack"): "notifications ack 12",
    ("notifications", "add"): "notifications add --kind=reminder --headline=h --at=2030-01-01T10:00:00Z",
    ("notifications", "snooze"): "notifications snooze 3 --for=1h",
    ("notifications", "cancel"): "notifications cancel 3",
    ("memory", "add"): "memory add --name=x",
    ("memory", "append"): "memory append x",
    ("memory", "reclassify"): "memory reclassify x",
    ("memory", "link"): "memory link a b",
    ("approvals", "reject"): "approvals reject 5",
    ("approvals", "revoke"): "approvals revoke 5",
}

# Command lines the orchestrator identity and skills show it running today
# (agents/gaia-orchestrator.md and the skills it loads): each must keep passing.
USED_TODAY = [
    "brief ac add demo --id=AC-1 --description=d",
    "brief ac edit demo --id=AC-1 --description=d",
    "brief decision add demo --text=t --rationale=r",
    "brief edit demo --headless --field=title --content=X",
    "brief new demo --headless --title=T --status=draft",
    "brief show demo --json",
    "context get --section stack",
    "context get-contract --section project_identity",
    "contract view --draft-id d",
    "contract view --harness-id h",
    "doctor --workspace me",
    "evidence list --brief demo",
    "memory add --name x --type feedback --body-file - --project p",
    "memory append x --body-file -",
    "memory checkpoint --file f",
    "memory get-relevant --initiative i --sections anchor --json",
    "memory link a b --kind supersedes",
    "memory reclassify x --class thread --status closed",
    "memory search q --scope curated",
    "memory show x --links --workspace me --json",
    "memory add --name x --body '- a bullet that starts with a dash'",
    "notifications add --kind reminder --headline h --at 2030-01-01T10:00:00Z",
    "notifications list --upcoming",
    "notifications snooze 3 --for 1h",
    "plan change request demo --reason r",
    "plan pause demo --reason r",
    "query --surface memory",
    "scan --workspace me",
    "session resume-point set --session-id s --text t",
    "session snapshot --session-id s",
    "usage show --plan 3",
    "worktree list --repo /r",
    "approvals history --status pending",
    "approvals question 1 --details",
    "--json brief show demo",
]


@pytest.fixture
def run_guard(monkeypatch):
    monkeypatch.setattr(guard, "is_trusted_gaia_binary", lambda _binary: True)

    def _run(argline: str):
        return guard.check(f"{TRUSTED} {argline}", {"session_id": "s"})

    return _run


def _baseline(phrase) -> str:
    return BASELINES.get(phrase, " ".join(phrase))


def _live_parser():
    path = ROOT / "bin" / "gaia"
    loader = importlib.machinery.SourceFileLoader("gaia_cli_entry", str(path))
    spec = importlib.util.spec_from_loader("gaia_cli_entry", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module._build_parser(module._discover_plugins())


def _live_options(parser, phrase) -> set:
    for token in phrase:
        subparsers = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        parser = subparsers.choices[token]
    return {
        option
        for action in parser._actions
        if not isinstance(action, (argparse._HelpAction, argparse._SubParsersAction))
        for option in action.option_strings
    }


@pytest.fixture(scope="module")
def live_options():
    parser = _live_parser()
    return {phrase: _live_options(parser, phrase) for phrase in ALLOWED_PHRASES}


PHRASES = sorted(ALLOWED_PHRASES)


def test_table_covers_exactly_the_admitted_phrases() -> None:
    assert set(_KNOWN_SAFE_FLAGS) == set(ALLOWED_PHRASES)


@pytest.mark.parametrize("phrase", PHRASES, ids=" ".join)
def test_table_equals_the_live_parser_minus_the_withheld(live_options, phrase) -> None:
    withheld = WITHHELD.get(phrase, set())
    assert withheld <= live_options[phrase]
    assert set(_KNOWN_SAFE_FLAGS[phrase]) == live_options[phrase] - withheld


@pytest.mark.parametrize("phrase", PHRASES, ids=" ".join)
def test_every_known_safe_flag_passes(live_options, phrase) -> None:
    safe = live_options[phrase] - WITHHELD.get(phrase, set())
    for flag in sorted(safe):
        assert _validate_known_safe_flags(phrase + (flag,), phrase) is None, flag
        assert _validate_known_safe_flags(phrase + (f"{flag}=v",), phrase) is None, flag


@pytest.mark.parametrize("phrase", PHRASES, ids=" ".join)
def test_every_known_safe_flag_passes_the_whole_guard_on_a_read_verb(
    run_guard, live_options, phrase
) -> None:
    if phrase in guard.ALLOWED_WRITE_PHRASES:
        pytest.skip("write shapes constrain which flags combine; covered by USED_TODAY")
    for flag in sorted(live_options[phrase] - WITHHELD.get(phrase, set())):
        assert run_guard(f"{_baseline(phrase)} {flag}") == (True, None), flag


@pytest.mark.parametrize("phrase", PHRASES, ids=" ".join)
def test_unknown_flag_is_refused_under_every_admitted_verb(run_guard, phrase) -> None:
    assert run_guard(_baseline(phrase)) == (True, None)
    for spelling in ("--zz-unknown", "--zz-unknown=1"):
        allowed, reason = run_guard(f"{_baseline(phrase)} {spelling}")
        assert allowed is False, spelling
        assert reason and "not approvable" in reason


@pytest.mark.parametrize("phrase,flags", sorted(WITHHELD.items()), ids=lambda v: " ".join(v) if isinstance(v, tuple) else "")
def test_withheld_flags_stay_outside_the_lane(run_guard, phrase, flags) -> None:
    for flag in sorted(flags):
        for spelling in (flag, f"{flag}=x"):
            assert run_guard(f"{_baseline(phrase)} {spelling}")[0] is False, spelling


@pytest.mark.parametrize("argline", USED_TODAY)
def test_flags_the_orchestrator_uses_today_still_pass(run_guard, argline) -> None:
    assert run_guard(argline) == (True, None)


@pytest.mark.parametrize(
    "argline",
    [
        "brief show demo --workspac me",
        "brief show demo -- --json",
        "brief show demo -j",
        "memory search q -l 5",
        "--zz brief show demo",
        "--workspace=me brief show demo",
    ],
)
def test_abbreviations_separators_and_foreign_leading_flags_are_refused(run_guard, argline) -> None:
    assert run_guard(argline)[0] is False
