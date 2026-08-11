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
non-mutative is re-read under the OLD absorbing grammar
(``_absorbing_form``), and the higher of the two verdicts wins. That makes the
direction of a table error one-way, which is the property this file measures:

    tier(command, today) >= tier(command, before the table existed)

for every command, whether or not the entry that fired was correct. The
pre-table reading is reconstructible exactly -- it is the command with the flag
kept and the token it used to swallow dropped -- so the two readings can be
compared in one process, without a second checkout to compare against.

The floor never lowers a verdict. The old reading is the corrupted one and is
consulted only as a source of escalation, which is why the property is an
inequality and not an equality.
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
from modules.security.tiers import SecurityTier

from test_boolean_short_flag_equivalence import CORPUS, with_flag

# Ordered worst-to-least so "no verdict decreased" is a comparison and not a
# special case per tier.
TIER_ORDER = {
    SecurityTier.T0_READ_ONLY: 0,
    SecurityTier.T1_VALIDATION: 1,
    SecurityTier.T2_DRY_RUN: 2,
    SecurityTier.T3_BLOCKED: 3,
}

# Every corpus form crossed with every flag declared for its CLI: the commands
# on which the two readings can actually differ.
FLAGGED_FORMS = [
    (f"{case_id}+{flag}", with_flag(command, flag))
    for case_id, command, _ in CORPUS
    for flag in sorted(BOOLEAN_SHORT_FLAGS.get(analyze_command(command).base_cmd, ()))
]


@pytest.mark.parametrize(
    "case_id,command", FLAGGED_FORMS, ids=[row[0] for row in FLAGGED_FORMS]
)
def test_the_verdict_never_falls_below_the_pre_table_reading(case_id, command):
    """The shipped verdict is at least the verdict the old grammar produced."""
    absorbing = _absorbing_form(command)
    assert absorbing is not None, (
        f"{case_id}: {command!r} carries a declared boolean flag, so the two "
        f"readings must be distinguishable -- _absorbing_form found no difference"
    )

    shipped = detect_mutative_command(command).is_mutative
    pre_table = _detect_mutative_command(absorbing).is_mutative

    assert shipped >= pre_table, (
        f"{case_id}: the change LOWERED a verdict. {command!r} is now "
        f"is_mutative={shipped}, but the pre-table reading {absorbing!r} was "
        f"is_mutative={pre_table}. A gate that existed before this change must "
        f"still exist after it."
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


def test_no_corpus_verdict_was_lowered_overall():
    """The aggregate form of the property, over the whole differential corpus.

    Per-case parametrization says which case broke; this says whether the change
    is monotone as a whole, and reports the count that moved upward -- a change
    that repaired nothing would pass every inequality above and still be worth
    catching here.
    """
    raised = 0
    for _, command in FLAGGED_FORMS:
        absorbing = _absorbing_form(command)
        shipped = detect_mutative_command(command).is_mutative
        pre_table = _detect_mutative_command(absorbing).is_mutative
        assert shipped >= pre_table
        raised += int(shipped and not pre_table)

    assert raised > 0, (
        "no corpus form changed verdict: the differential corpus is not "
        "exercising the defect this change exists to close"
    )
