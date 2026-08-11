#!/usr/bin/env python3
"""A wrong entry in the boolean short-flag table costs a prompt, never a gate.

``BOOLEAN_SHORT_FLAGS`` is a table of hand-audited facts about a dozen CLIs:
which of their single-letter global flags take no value. Correct entries make
``-q`` read as ``--quiet`` already reads. The question this file answers is what
a WRONG entry does -- a flag listed as valueless that really does take a value.

It does the damage in the other direction. The value it declines to absorb
stands as a positional, so the head shifts by one and every anchor path and
subcommand key that matches from position 0 misses -- the same corruption the
old absorption caused, mirrored. Left alone, that means the table's correctness
is load-bearing for the gate, and a table of facts about a dozen third-party
CLIs is not a thing to bet a gate on.

So the verdict is floored rather than trusted: a command that comes back
non-mutative is re-read under the OLD absorbing grammar (``_absorbing_form``),
and the higher of the two verdicts wins.

WHAT THE FLOOR GUARANTEES, STATED SO IT IS TRUE. The guarantee is on the GATE
and only on the gate:

    is_mutative(command, today) >= is_mutative(command, pre-table reading)

It is NOT a guarantee about tiers, and the same inequality written over tiers is
false. Two measured shapes of counterexample:

* ``npm -g ls audit`` classifies T0 today where its pre-table reading
  ``npm -g audit`` classified T2 -- a drop inside the band below T3, with both
  readings non-mutative.
* ``npm -D plan install`` classifies T2 today where its pre-table reading
  ``npm -D install`` classified T3 -- a drop across the T3 label itself, and yet
  BOTH readings are ``is_mutative=True``. The gate held; only the label moved.

The second one shows where the tier number comes from and why it is not the
thing to state a guarantee over: ``tiers.py``'s ``_classify_command_tier_cached``
matches its T2 and T1 regexes and returns before it ever calls
``detect_mutative_command``, so a command landing in one of those lanes is
labelled without the detector -- and therefore without the floor -- being
consulted at all. The floor raises ``is_mutative``, which is what the validator
gates on; the tier label is metadata computed by a different ladder.

WHY THAT INEQUALITY IS ARGUED HERE AND NOT ASSERTED. The shipped verdict is
literally ``primary or floor(pre-table reading)``, so comparing the two readings
in one process compares a value with a term of itself: the inequality holds by
construction and no assertion over it can fail. The property is real; a test of
it in that shape measures nothing. What IS falsifiable is whether the floor is
wired in and reaching the shipped verdict, and that is measured by asserting an
ABSOLUTE expected verdict on the shape a wrong entry actually produces -- the
flag written with a value standing after it. That is
``test_the_floor_gates_a_flag_written_with_a_value`` below, and it is the test
that fails if the floor is removed.

The floor is the whole defense of the table, and that test is what defends the
floor. Nothing here can detect a wrong entry itself: a correct flag followed by
a stray positional and a wrongly declared flag followed by its real value
produce the identical token stream, so the difference between a right entry and
a wrong one is a fact about the third-party CLI and not an observable. Injecting
``-x`` into gcloud's entry leaves the equivalence suite green, which is why that
suite must not be read as a guard on the table.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
# The corpus is shared with the equivalence suite rather than copied: two
# corpora drift, and the whole point is that both properties are measured over
# the SAME commands.
sys.path.insert(0, str(Path(__file__).parent))

from modules.security.command_semantics import BOOLEAN_SHORT_FLAGS, analyze_command
from modules.security.mutative_verbs import (
    _absorbing_form,
    _detect_mutative_command,
    detect_mutative_command,
)
from test_boolean_short_flag_equivalence import (
    CORPUS,
    GATED_FORMS,
    VERDICT_CHANGING_SHORT_FLAGS,
    with_flag,
)

# Every corpus form crossed with every flag declared for its CLI: the commands
# on which the two readings can actually differ.
FLAGGED_FORMS = [
    (f"{case_id}+{flag}", with_flag(command, flag))
    for case_id, command, _ in CORPUS
    for flag in sorted(BOOLEAN_SHORT_FLAGS.get(analyze_command(command).base_cmd, ()))
]


def with_flag_and_value(command: str, flag: str) -> str:
    """Write *flag* the way a flag that TAKES a value is written.

    This is the shape a wrong table entry produces at runtime, and the reason it
    can be built without injecting anything: the classifier cannot tell this
    from a wrongly declared flag carrying its real value, so a correct entry
    written this way exercises exactly the same corruption.
    """
    tool, rest = command.split(" ", 1)
    return f"{tool} {flag} SOME-VALUE {rest}"


# Only the gated forms: a read that stays a read proves nothing about a gate.
# `-h` is excluded for the reason the equivalence suite excludes it -- it
# replaces the operation with printing usage, so it is not verdict-neutral.
VALUE_CARRYING_FORMS = [
    (f"{case_id}+{flag}", with_flag_and_value(command, flag))
    for case_id, command in GATED_FORMS
    for flag in sorted(BOOLEAN_SHORT_FLAGS.get(analyze_command(command).base_cmd, ()))
    if flag not in VERDICT_CHANGING_SHORT_FLAGS
]


@pytest.mark.parametrize(
    "case_id,command",
    VALUE_CARRYING_FORMS,
    ids=[row[0] for row in VALUE_CARRYING_FORMS],
)
def test_the_floor_gates_a_flag_written_with_a_value(case_id, command):
    """A gated operation stays gated with a token standing where a value would.

    The expectation is absolute -- ``is_mutative`` is True -- rather than a
    comparison against the pre-table reading, which is what keeps this
    falsifiable. Remove the floor from ``detect_mutative_command`` and the
    gcloud IAM cases here go non-mutative, because their verdict comes from an
    anchor path that matches from position 0 and the stray token displaced it.
    """
    assert detect_mutative_command(command).is_mutative is True, (
        f"{case_id}: {command!r} classified is_mutative=False. A token standing "
        f"where a declared-valueless flag's value would go shifted the head and "
        f"dropped the gate -- which is exactly what a wrong table entry does at "
        f"runtime. The floor is what is supposed to hold this."
    )


def test_the_floor_is_the_only_gate_on_at_least_one_form():
    """The test above must depend on the floor, or it stops measuring it.

    If every value-carrying form were gated by the primary reading alone, the
    parametrized test would pass with the floor deleted and would be guarding
    nothing. This names the cases where the floor is the only thing standing,
    so that condition is measured instead of assumed.
    """
    unfloored = [
        (case_id, command)
        for case_id, command in VALUE_CARRYING_FORMS
        if not _detect_mutative_command(command).is_mutative
    ]

    assert unfloored, (
        "no value-carrying form depends on the floor: the primary reading now "
        "gates all of them, so test_the_floor_gates_a_flag_written_with_a_value "
        "would pass with the floor removed. Rebuild the corpus so the floor is "
        "measured, or the table has no guarded defense left."
    )


def test_a_wrong_entry_cannot_open_a_gate():
    """The adversarial case: declare a value-taking flag valueless, on purpose.

    ``gcloud`` has no single-letter flag that takes a value, so the failure has
    to be built rather than found. ``-x`` is injected into the table as if
    someone had audited it wrongly, and the command written the way that flag
    would really be used. Without the floor the injected entry leaves
    ``my-project`` standing at the head, the bucket-IAM anchor misses, and a
    public-access grant classifies T0. With it, the corrupted reading is still
    consulted and the gate holds.
    """
    command = (
        "gcloud -x my-project storage buckets add-iam-policy-binding gs://b "
        "--member=allUsers --role=roles/storage.objectViewer"
    )

    original = BOOLEAN_SHORT_FLAGS["gcloud"]
    BOOLEAN_SHORT_FLAGS["gcloud"] = original | {"-x"}
    analyze_command.cache_clear()
    detect_mutative_command.cache_clear()
    try:
        # The head really is shifted: this is the damage, not a hypothetical.
        assert analyze_command(command).non_flag_tokens[0] == "my-project"

        floored = detect_mutative_command(command)
        naive = _detect_mutative_command(command)

        assert naive.is_mutative is False, (
            "the injected wrong entry no longer breaks the anchor, so this test "
            "is measuring nothing -- rebuild the adversarial case"
        )
        assert floored.is_mutative is True, (
            f"a wrong table entry opened the gate: {command!r} classified "
            f"is_mutative=False. The floor did not hold."
        )
    finally:
        BOOLEAN_SHORT_FLAGS["gcloud"] = original
        analyze_command.cache_clear()
        detect_mutative_command.cache_clear()


def test_an_omitted_entry_leaves_todays_behaviour_byte_for_byte():
    """The other failure mode: a boolean flag nobody listed.

    Omission is the safe error, and it is safe by being no change at all -- an
    undeclared flag still absorbs, so the reading is the one the tree already
    had. The pre-existing hole survives for that flag; nothing new is opened.
    """
    # `-Z` is not declared for gcloud, and is not a real gcloud flag either.
    undeclared = "gcloud -Z storage buckets list"
    assert _absorbing_form(undeclared) is None
    assert analyze_command(undeclared).non_flag_tokens == ("buckets", "list")


def test_the_floor_costs_nothing_where_no_declared_flag_fires():
    """The floor is not a second classification pass on every command.

    It runs only when a declared flag actually fires AND the first reading came
    back non-mutative. Everything else -- every CLI outside the table, every
    command inside it that carries no declared flag -- gets no second reading.
    """
    assert _absorbing_form("terraform apply") is None
    assert _absorbing_form("aws s3 rm s3://b/o") is None
    assert _absorbing_form("gcloud storage buckets list") is None
    assert _absorbing_form("git -C /repo push origin main") is None
    # Fires: the flag is declared, and it precedes a token it used to swallow.
    assert _absorbing_form("gcloud -q storage buckets list") == "gcloud -q buckets list"


def test_the_differential_corpus_still_exercises_the_defect():
    """At least one corpus form is gated today that the old grammar let through.

    This is the half of the differential comparison that can actually fail. The
    other half -- that no verdict was lowered -- holds by construction and is
    argued in this module's header rather than asserted, because the shipped
    verdict contains the pre-table verdict as a term of itself.
    """
    raised = 0
    for _, command in FLAGGED_FORMS:
        absorbing = _absorbing_form(command)
        if absorbing is None:
            continue
        shipped = detect_mutative_command(command).is_mutative
        pre_table = _detect_mutative_command(absorbing).is_mutative
        raised += int(shipped and not pre_table)

    assert raised > 0, (
        "no corpus form changed verdict: the differential corpus is not "
        "exercising the defect this change exists to close"
    )
