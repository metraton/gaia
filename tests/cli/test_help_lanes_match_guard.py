"""`gaia --help` declares exactly what the orchestrator CLI guard allows.

Both sides are derived from live code: the lanes are parsed from the help text
the real ``bin/gaia`` prints, and the allowed set is read from the guard's own
tables and then exercised through ``gaia_cli_only_guard.check`` with an
orchestrator payload and the repository's real, provenance-trusted binary.
A verb the guard admits that the help never names is undiscoverable; a verb
the help promises that the guard denies is a broken map. Either direction fails.

The lane grammar parsed here is the one ``_EPILOG`` in ``bin/gaia`` follows: a
row starts with exactly two spaces and a command head; the rest of that first
line is its verb spec (``a | b`` separates alternatives, ``x|y`` alternates the
token it sits in, parentheses qualify and are not verbs, a trailing ``|``
continues the spec on the next line); an empty spec means the bare command;
other deeper-indented lines are prose.
"""

from __future__ import annotations

import itertools
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
_HOOKS_DIR = _REPO_ROOT / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.security import gaia_cli_only_guard as guard  # noqa: E402

_ORCHESTRATOR_PAYLOAD: dict = {}
_READ_HEADER = "READ --"
_WRITE_HEADER = "WRITE the orchestrator owns --"
_SHAPE_DENIAL = "does not match its bounded coordination shape"


def _help_text() -> str:
    result = subprocess.run(
        [sys.executable, str(_GAIA_BIN), "--help"],
        capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def _section_rows(help_text: str, header: str) -> list[str]:
    """First lines of every row in the lane that starts with *header*."""
    rows: list[str] = []
    inside = False
    for line in help_text.splitlines():
        if line.startswith(header):
            inside = True
            continue
        if not inside or not line.strip():
            continue
        if not line.startswith(" "):
            break
        if rows and rows[-1].endswith("|"):
            rows[-1] = f"{rows[-1]} {line.strip()}"
        elif line.startswith("  ") and not line.startswith("   "):
            rows.append(line.strip())
    assert rows, f"no rows found under lane header {header!r}"
    return rows


def _row_phrases(row: str) -> set[tuple[str, ...]]:
    head, _, spec = row.partition("  ")
    spec = re.sub(r"\([^)]*\)", "", spec).strip()
    if not spec:
        return {(head,)}
    phrases: set[tuple[str, ...]] = set()
    for alternative in spec.split(" | "):
        choices = [token.split("|") for token in alternative.split()]
        for combo in itertools.product(*choices):
            phrases.add((head, *combo))
    return phrases


def _lane(help_text: str, header: str) -> set[tuple[str, ...]]:
    phrases: set[tuple[str, ...]] = set()
    for row in _section_rows(help_text, header):
        phrases |= _row_phrases(row)
    return phrases


def _guard_read_set() -> set[tuple[str, ...]]:
    return set(guard.ALLOWED_READ_PHRASES) | {
        (flag,) for flag in guard.ALLOWED_BARE_READ_FLAGS
    }


def _guard_write_set() -> set[tuple[str, ...]]:
    return set(guard.ALLOWED_WRITE_PHRASES)


def _drift(declared: set, allowed: set) -> dict:
    return {
        "declared_by_help_but_not_allowed": sorted(declared - allowed),
        "allowed_by_guard_but_not_declared": sorted(allowed - declared),
    }


def _no_drift(drift: dict) -> bool:
    return not any(drift.values())


def _check(phrase: tuple[str, ...], *extra: str):
    command = " ".join((str(_GAIA_BIN), *phrase, *extra))
    return guard.check(command, _ORCHESTRATOR_PAYLOAD)


@pytest.fixture(scope="module")
def help_text() -> str:
    return _help_text()


def test_real_binary_is_trusted_so_checks_below_are_live():
    assert guard.is_trusted_gaia_binary(str(_GAIA_BIN))


def test_read_lane_is_exactly_the_guard_read_set(help_text):
    drift = _drift(_lane(help_text, _READ_HEADER), _guard_read_set())
    assert _no_drift(drift), drift


def test_write_lane_is_exactly_the_guard_write_set(help_text):
    drift = _drift(_lane(help_text, _WRITE_HEADER), _guard_write_set())
    assert _no_drift(drift), drift


def test_every_read_lane_verb_runs_for_the_orchestrator(help_text):
    denied = {}
    for phrase in _lane(help_text, _READ_HEADER):
        allowed, reason = _check(phrase)
        if not allowed:
            denied[phrase] = reason
    assert not denied, denied


def test_every_write_lane_verb_is_admitted_at_verb_level(help_text):
    """A bare write may still fail its bounded shape; it must never be refused as a verb."""
    denied = {}
    for phrase in _lane(help_text, _WRITE_HEADER):
        allowed, reason = _check(phrase)
        if not allowed and _SHAPE_DENIAL not in reason:
            denied[phrase] = reason
    assert not denied, denied


@pytest.mark.parametrize(
    "phrase,args",
    [
        (("brief", "ac", "add"), ("my-brief", "--id", "AC-1", "--text", "x")),
        (("brief", "ac", "add"), ("my-brief", "--id=AC-1", "--text", "x")),
        (("brief", "ac", "edit"), ("my-brief", "--id", "AC-1", "--text", "x")),
        (("brief", "ac", "edit"), ("my-brief", "--id=AC-1", "--text", "x")),
        (("brief", "ac", "remove"), ("my-brief", "--id", "AC-1")),
        (("brief", "ac", "remove"), ("my-brief", "--id=AC-1")),
        (("brief", "new"), ("--title", "t", "--headless")),
        (("brief", "new"), ("--title=t", "--headless")),
    ],
)
def test_declared_write_accepts_both_argparse_spellings_of_its_value(phrase, args):
    allowed, reason = _check(phrase, *args)
    assert allowed, reason


@pytest.mark.parametrize(
    "mutate_declared,mutate_allowed,direction",
    [
        (set(), {("approvals", "approve")}, "allowed_by_guard_but_not_declared"),
        ({("approvals", "approve")}, set(), "declared_by_help_but_not_allowed"),
    ],
)
def test_comparison_fails_on_a_one_entry_drift_either_way(
    help_text, mutate_declared, mutate_allowed, direction
):
    declared = _lane(help_text, _WRITE_HEADER)
    allowed = _guard_write_set()
    baseline = set(_drift(declared, allowed)[direction])
    drift = _drift(declared | mutate_declared, allowed | mutate_allowed)
    assert set(drift[direction]) - baseline == {("approvals", "approve")}
