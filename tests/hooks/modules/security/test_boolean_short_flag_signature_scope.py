#!/usr/bin/env python3
"""Narrowing absorption does not widen any approval grant.

``analyze_command`` feeds ``build_approval_signature`` and
``matches_approval_signature``, so changing which token a short flag swallows
changes what a T3 grant covers. Two things had to be true before the change
could be allowed in, and they are what this file measures.

NOTHING IS LOST. The change does not drop a token, it MOVES one: the token a
boolean flag used to swallow leaves ``normalized_flags`` and arrives in
``semantic_tokens``. Both halves are compared at match time, so the command text
stays bound end to end -- and it arrives in the half that is an ORDERED tuple,
having left the half that is a sorted set. Information about the command goes
up, not down. This is the opposite direction from the normalization a previous
turn refused inside ``analyze_command``: ``_peel_release_track_prefix`` DELETES
``alpha``, which would have collapsed ``gcloud alpha X`` and ``gcloud X`` onto
one grant, and it stays outside the signature path for exactly that reason.

NOTHING COLLAPSES THAT WAS NOT ALREADY COLLAPSED. One equivalence class does
widen, and it is worth naming precisely rather than hiding: a grant minted for
``gcloud -q storage buckets ...`` now also covers ``gcloud storage buckets ...
-q``, because a flag written before the subcommand and the same flag written
after it produce the same ordered positionals and the same flag set. That class
is not introduced here. ``normalized_flags`` has always been a sorted set, so
``gcloud --quiet storage buckets ...`` and ``gcloud storage buckets ... --quiet``
ALREADY share one signature, and have for as long as the scope has existed. The
change makes the short spelling behave as the long one does; it does not invent
a tolerance the model lacked. The two forms are the same operation -- these are
global flags, whose position the CLI itself ignores.

WHAT IT COSTS INSTEAD. A grant minted BEFORE the change, against the old
tokenization, no longer matches its own command: the stored ``semantic_tokens``
are missing the swallowed token and the stored ``normalized_flags`` still carry
it, and neither side agrees with what the command produces now. The user is
asked to sign once more. That is the direction an error is supposed to fall.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(Path(__file__).parent))

from modules.security.approval_scopes import (
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
    matches_approval_signature,
)
from modules.security.command_semantics import BOOLEAN_SHORT_FLAGS, analyze_command
from modules.security.mutative_verbs import _absorbing_form

from test_boolean_short_flag_equivalence import GATED_FORMS, with_flag

# `-h` turns the command into a usage print, so it has no mutative verb and no
# semantic signature to build. Every other declared flag leaves the operation
# intact and is exercised here.
SIGNABLE_FLAGS = {
    cli: sorted(flags - {"-h"}) for cli, flags in BOOLEAN_SHORT_FLAGS.items()
}


def base_cmd_of(command: str) -> str:
    return analyze_command(command).base_cmd


def signature(command: str):
    return build_approval_signature(command, SCOPE_SEMANTIC_SIGNATURE)


def _one_spelling_per_letter(flags):
    """Drop case variants of one letter, keeping the first spelling seen.

    ``-D`` and ``-d`` are different flags on gsutil, and they key to the SAME
    grant -- ``_normalize_flag_token`` lowercases every flag token. That
    collapse is real, it is not this change's, and it is pinned on its own in
    ``test_case_variant_short_flags_already_shared_a_signature``. Feeding both
    spellings into the collision check would only re-measure it there, under a
    name that says this change caused it.
    """
    kept = {}
    for flag in flags:
        kept.setdefault(flag.lower(), flag)
    return list(kept.values())


FLAGGED_GATED = [
    (f"{case_id}+{flag}", command, flag)
    for case_id, command in GATED_FORMS
    for flag in SIGNABLE_FLAGS.get(base_cmd_of(command), ())
]


@pytest.mark.parametrize(
    "case_id,command,flag", FLAGGED_GATED, ids=[row[0] for row in FLAGGED_GATED]
)
def test_a_grant_still_matches_its_own_command(case_id, command, flag):
    """Reflexivity: the command that minted a grant is covered by it.

    This is the property a retry depends on. It broke once already, for curl,
    when build and match derived identity from different sources -- so it is
    asserted directly rather than assumed from the two paths sharing a function.
    """
    flagged = with_flag(command, flag)
    sig = signature(flagged)
    assert sig is not None, f"{case_id}: {flagged!r} produced no signature"
    assert matches_approval_signature(sig, flagged), (
        f"{case_id}: {flagged!r} does not match the grant minted from itself"
    )


@pytest.mark.parametrize(
    "case_id,command,flag", FLAGGED_GATED, ids=[row[0] for row in FLAGGED_GATED]
)
def test_the_unswallowed_token_stays_bound_to_the_signature(case_id, command, flag):
    """The token moved between the halves of the signature; it did not leave."""
    flagged = with_flag(command, flag)
    subcommand = command.split(" ", 1)[1].split(" ", 1)[0].lower()

    sig = signature(flagged)
    assert subcommand in sig.semantic_tokens, (
        f"{case_id}: {subcommand!r} is missing from semantic_tokens -- the token "
        f"the flag used to swallow must arrive here"
    )
    assert subcommand not in sig.normalized_flags, (
        f"{case_id}: {subcommand!r} is still bound as a flag value; the flag was "
        f"declared valueless, so nothing may be spent on it"
    )


@pytest.mark.parametrize(
    "case_id,command,flag", FLAGGED_GATED, ids=[row[0] for row in FLAGGED_GATED]
)
def test_a_grant_does_not_stretch_to_a_different_operation(case_id, command, flag):
    """The grant covers its own operation and not the ones beside it."""
    sig = signature(with_flag(command, flag))
    for other_id, other_command in GATED_FORMS:
        if other_command == command:
            continue
        assert not matches_approval_signature(sig, with_flag(other_command, flag)), (
            f"{case_id}: a grant for {command!r} reached {other_command!r}"
        )
        assert not matches_approval_signature(sig, other_command), (
            f"{case_id}: a grant for {command!r} reached the unflagged "
            f"{other_command!r}"
        )


def test_no_two_distinct_operations_share_a_signature():
    """The collapse check, over every form and every declared flag at once.

    Distinct commands must key to distinct grants. Run across the whole corpus
    rather than pair by pair, so a collision between two forms nobody thought to
    compare is caught by the same assertion.
    """
    seen: dict = {}
    for case_id, command in GATED_FORMS:
        forms = [command] + [
            with_flag(command, flag)
            for flag in _one_spelling_per_letter(
                SIGNABLE_FLAGS.get(base_cmd_of(command), ())
            )
        ]
        for form in forms:
            sig = signature(form)
            assert sig is not None, f"{case_id}: {form!r} produced no signature"
            key = (sig.base_cmd, sig.semantic_tokens, sig.normalized_flags)
            if key in seen and seen[key] != form:
                pytest.fail(
                    f"two distinct commands collapsed onto one grant:\n"
                    f"  {seen[key]!r}\n  {form!r}\n  shared key: {key}"
                )
            seen[key] = form


@pytest.mark.parametrize(
    "case_id,command,flag", FLAGGED_GATED, ids=[row[0] for row in FLAGGED_GATED]
)
def test_flag_position_tolerance_is_the_one_the_long_form_already_had(
    case_id, command, flag
):
    """The one class that widens, measured against the model that already had it.

    A global flag before the subcommand and the same flag after it now share a
    grant. That is not a new tolerance: ``normalized_flags`` is a sorted set, so
    the LONG spelling of the very same flag has always behaved this way. The
    assertion is written as an agreement between the two spellings rather than
    as a bare "these two collapse", because the point is not that the collapse
    happens -- it is that the short form is no longer the odd one out.
    """
    leading = with_flag(command, flag)
    trailing = f"{command} {flag}"

    leading_sig = signature(leading)
    assert leading_sig is not None
    assert matches_approval_signature(leading_sig, trailing), (
        f"{case_id}: {leading!r} and {trailing!r} are the same operation with a "
        f"global flag moved, and must share one grant"
    )


LONG_FORM_PRECEDENT = [
    ("gcloud-quiet", "gcloud pubsub topics delete t", "--quiet"),
    ("git-paginate", "git push origin main", "--paginate"),
    ("npm-global", "npm install left-pad", "--global"),
]


@pytest.mark.parametrize(
    "case_id,command,long_flag",
    LONG_FORM_PRECEDENT,
    ids=[row[0] for row in LONG_FORM_PRECEDENT],
)
def test_the_long_form_already_collapsed_across_positions(
    case_id, command, long_flag
):
    """The precedent itself, pinned.

    Without this the claim above -- that the widened class is one the model
    already accepted -- rests on an argument. Here it rests on a measurement,
    made on a spelling this change does not touch.
    """
    leading = with_flag(command, long_flag)
    trailing = f"{command} {long_flag}"

    sig = signature(leading)
    assert sig is not None
    assert matches_approval_signature(sig, trailing), (
        f"{case_id}: the long form does NOT collapse across positions, so the "
        f"short form must not either -- the justification for this change is void"
    )


def test_case_variant_short_flags_already_shared_a_signature():
    """A collapse this change did not cause, recorded rather than left implicit.

    ``gsutil -D`` (debug output) and ``gsutil -d`` are different flags that key
    to one grant, because ``_normalize_flag_token`` lowercases every flag token
    before it reaches ``normalized_flags``. The collision check above found it,
    which is the check working; what it is NOT is a consequence of narrowing
    absorption. Both readings are compared here to show that: under the
    pre-table grammar the two spellings were already indistinguishable, and they
    still are. ``_normalize_flag_token`` is untouched by this change, so closing
    it belongs to whoever takes on flag-token case, not here.
    """
    upper = "gsutil -D rm gs://b/o.txt"
    lower = "gsutil -d rm gs://b/o.txt"

    shipped_upper = analyze_command(upper)
    shipped_lower = analyze_command(lower)
    assert shipped_upper.semantic_tokens == shipped_lower.semantic_tokens
    assert shipped_upper.flag_tokens == shipped_lower.flag_tokens

    pre_upper = analyze_command(_absorbing_form(upper))
    pre_lower = analyze_command(_absorbing_form(lower))
    assert pre_upper.semantic_tokens == pre_lower.semantic_tokens, (
        "the two spellings were distinguishable before this change, which would "
        "make the collapse this change's doing after all"
    )
    assert pre_upper.flag_tokens == pre_lower.flag_tokens


def test_a_pre_change_grant_no_longer_covers_its_own_command():
    """A grant minted under the old tokenization asks to be signed again.

    This is the cost of the change, asserted rather than hoped for. The stored
    signature is reconstructed exactly as the old grammar produced it -- the
    subcommand sitting in ``normalized_flags`` as the flag's value and missing
    from ``semantic_tokens`` -- and it now matches nothing, so the grant is
    inert rather than over-broad. The user pays one more prompt; no command
    inherits authority it was not given.
    """
    command = (
        "gcloud -q storage buckets add-iam-policy-binding gs://b "
        "--member=allUsers --role=roles/storage.objectViewer"
    )
    current = signature(command)
    assert current is not None
    assert matches_approval_signature(current, command)

    # What build_approval_signature produced for this command before the table:
    # `storage` spent as the value of `-q`.
    stale = type(current)(
        scope_type=SCOPE_SEMANTIC_SIGNATURE,
        base_cmd="gcloud",
        cli_family=current.cli_family,
        danger_category=current.danger_category,
        verb=current.verb,
        semantic_tokens=("gcloud", "buckets", "add-iam-policy-binding", "gs://b"),
        normalized_flags=tuple(sorted(set(current.normalized_flags) | {"storage"})),
        dangerous_flags=current.dangerous_flags,
        exact_tokens=current.exact_tokens,
    )

    assert not matches_approval_signature(stale, command), (
        "a grant minted under the old tokenization still matches, which would "
        "mean the two readings agree -- they do not, and pretending otherwise "
        "hides which one the grant was actually consented to"
    )
    assert stale.semantic_tokens != current.semantic_tokens
