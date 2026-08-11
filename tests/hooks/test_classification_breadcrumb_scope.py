#!/usr/bin/env python3
"""The breadcrumb a mutation leaves must not outlive the mutation.

``note_mutative_classification`` records that the gate had already classified a
command as state-mutating, so a later crash inside the gate denies instead of
degrading that classification into a permission. The breadcrumb is module-level
state, and nothing in production ever cleared it: the only caller of
``clear_classification`` was the test suite.

That was safe only because of an assumption held by a comment and enforced by
nothing -- one hook process per tool call. Under a persistent or batched hook
the assumption inverts the feature: the first mutation of the process marks it
permanently, and every subsequent READ inherits that mark, so a gate failure on
a harmless read is denied on the strength of a push that happened earlier.

This is the probe that measured it, driven in ONE process on purpose, since
process isolation is exactly the property that must stop being load-bearing.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
HOOKS_DIR = REPO_ROOT / "hooks"

if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from modules.security import fail_open
from modules.tools.bash_validator import validate_bash_command

MUTATING_COMMAND = "git push origin main"
READ_COMMAND = "ls -la"


@pytest.fixture(autouse=True)
def clean_breadcrumb():
    """Start and finish neutral, so the probe measures this process's own writes."""
    fail_open.clear_classification()
    yield
    fail_open.clear_classification()


def _validate(command):
    return validate_bash_command(
        command, is_subagent=False, session_id="breadcrumb-scope"
    )


def test_a_read_alone_leaves_no_breadcrumb():
    """The baseline arm of the probe: nothing mutating, nothing marked."""
    _validate(READ_COMMAND)
    assert fail_open.known_mutative_classification() is None


def test_a_mutation_leaves_its_breadcrumb():
    """The feature still works: the push is marked while it is being gated."""
    _validate(MUTATING_COMMAND)
    known = fail_open.known_mutative_classification()
    assert known is not None
    assert known.command == MUTATING_COMMAND


def test_a_read_after_a_mutation_does_not_inherit_the_mark():
    """The residue: in one process, the read that FOLLOWS a push came back marked."""
    _validate(MUTATING_COMMAND)
    assert fail_open.known_mutative_classification() is not None

    _validate(READ_COMMAND)

    known = fail_open.known_mutative_classification()
    assert known is None, (
        "a read inherited the breadcrumb of a push validated earlier in the "
        f"same process; the mark still names {known.command!r}, not the command "
        "being gated"
    )


def test_a_gate_failure_on_that_read_does_not_deny_it():
    """The consequence, stated as the outcome rather than as the mark.

    A stale breadcrumb is only a problem because of what it makes the fail-open
    path decide. Measured before the fix: the read was DENIED on the strength
    of the push before it.
    """
    _validate(MUTATING_COMMAND)
    _validate(READ_COMMAND)

    outcome = fail_open.decide_fail_open("unhandled_exception", "RuntimeError: probe")

    assert outcome.blocked is False, (
        "a gate failure on a plain read was turned into a denial by a mutation "
        "that had already been gated -- the bounded exception escaped its bound"
    )
    assert outcome.exit_code != 2


def test_the_bounded_exception_still_fires_for_the_mutation_itself():
    """Scoping the breadcrumb must not disarm it for the command it describes."""
    _validate(MUTATING_COMMAND)

    outcome = fail_open.decide_fail_open("unhandled_exception", "RuntimeError: probe")

    assert outcome.blocked is True
    assert outcome.exit_code == 2
