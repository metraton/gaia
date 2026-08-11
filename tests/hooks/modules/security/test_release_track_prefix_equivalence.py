#!/usr/bin/env python3
"""A release-track prefix does not change what a command is classified as.

``gcloud`` spells its pre-GA surfaces as a track word placed between the tool
and the command: ``gcloud alpha projects remove-iam-policy-binding`` runs the
same operation as ``gcloud projects remove-iam-policy-binding``. The word shifts
every following token one position, and BOTH position-sensitive mechanisms in
the classifier read those positions:

* the anchored upgrade (Step 3e.5) compares ``semantics.non_flag_tokens`` to an
  anchor path as a PREFIX, so a leading track word makes every declared path
  miss; and
* the Step 4 verb scan hyphen-splits a compound subcommand only while
  ``semantic_index <= 2``, so the shift pushes ``remove-iam-policy-binding``
  past the window in which it would be split onto ``remove``.

Measured live before the fix, on the contract that reported it: the plain form
was T3 and the ``alpha`` form was T0. One extra word walked past the gate --
past the six anchors added for IAM AND past the two forms the verb scan had
been deciding on its own since before them.

The property asserted here is an EQUIVALENCE, not a list of repaired cases:
for every form in the corpus the verdict with a track prefix equals the verdict
without it. Stated that way, one property catches both failure directions at
once. A gated form that goes free with the prefix fails it -- that is the
bypass. A free form that starts paying a toll with the prefix fails it too --
that is the over-correction, which a corpus of gated cases alone would never
see.

The second suite is the other half of the over-correction guard. ``alpha`` and
``beta`` are ordinary English words: a bucket, a branch, a make target, a
project name. Outside the track position they must carry no meaning at all,
which is asserted as its own equivalence -- the same command with the word
swapped for a neutral one classifies identically.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import (
    RELEASE_TRACK_PREFIXES,
    _peel_release_track_prefix,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# The two tracks gcloud actually publishes. `preview` was a third one years ago
# and is gone; it is left out rather than guessed, because every word declared
# here is a word this layer will delete from a command before reading it.
RELEASE_TRACKS = ("alpha", "beta")


def verdict(command: str):
    """The pair a caller of this layer acts on: gate decision, then tier."""
    return (detect_mutative_command(command).is_mutative, classify_command_tier(command))


# ---------------------------------------------------------------------------
# The corpus. Both halves are load-bearing: the gated forms detect the bypass,
# the free forms detect the over-correction. The expected verdict is recorded
# per form so that a form silently changing tier cannot be mistaken for the
# equivalence holding -- two commands agreeing on the WRONG verdict would still
# be equal to each other.
# ---------------------------------------------------------------------------

GATED_FORMS = [
    (
        "iam-project-revoke",
        "gcloud projects remove-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
    ),
    (
        "iam-project-grant",
        "gcloud projects add-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
    ),
    (
        "iam-secret-revoke",
        "gcloud secrets remove-iam-policy-binding my-secret "
        "--member=serviceAccount:x@y.iam.gserviceaccount.com "
        "--role=roles/secretmanager.secretAccessor",
    ),
    (
        "iam-secret-grant",
        "gcloud secrets add-iam-policy-binding my-secret "
        "--member=serviceAccount:x@y.iam.gserviceaccount.com "
        "--role=roles/secretmanager.secretAccessor",
    ),
    (
        "iam-bucket-revoke",
        "gcloud storage buckets remove-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
    ),
    (
        "iam-bucket-grant",
        "gcloud storage buckets add-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
    ),
    (
        "iam-sa-revoke",
        "gcloud iam service-accounts remove-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountUser",
    ),
    (
        "iam-sa-grant",
        "gcloud iam service-accounts add-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountUser",
    ),
    # Not IAM: ordinary mutations the verb scan decides, carried along so the
    # property is about the PREFIX rather than about the anchors added for IAM.
    ("compute-delete", "gcloud compute instances delete my-vm --zone=us-central1-a"),
    ("pubsub-delete", "gcloud pubsub topics delete my-topic"),
    ("sa-create", "gcloud iam service-accounts create my-sa"),
    ("secrets-destroy", "gcloud secrets versions destroy 1 --secret=my-secret"),
    ("run-deploy", "gcloud run deploy my-svc --image=gcr.io/p/i --region=us-central1"),
    # The permanent-deny floor. Its semantic rules match an ORDERED token
    # subsequence with gaps, so a track word does not break them -- this form is
    # a control that was already equal, and it stays that way.
    ("blocked-project-delete", "gcloud projects delete my-proj"),
]

FREE_FORMS = [
    ("iam-project-read", "gcloud projects get-iam-policy my-proj"),
    ("iam-bucket-read", "gcloud storage buckets get-iam-policy gs://my-bucket"),
    (
        "iam-sa-read",
        "gcloud iam service-accounts get-iam-policy sa@proj.iam.gserviceaccount.com",
    ),
    ("buckets-list", "gcloud storage buckets list"),
    ("instances-list", "gcloud compute instances list"),
    ("project-describe", "gcloud projects describe my-proj"),
    ("sa-describe", "gcloud iam service-accounts describe sa@proj.iam.gserviceaccount.com"),
    ("secrets-list", "gcloud secrets list"),
    ("topics-list", "gcloud pubsub topics list"),
]

CORPUS = [(case_id, command, True) for case_id, command in GATED_FORMS] + [
    (case_id, command, False) for case_id, command in FREE_FORMS
]


def with_track(command: str, track: str) -> str:
    """Insert *track* where gcloud accepts it: between the tool and the command."""
    tool, rest = command.split(" ", 1)
    return f"{tool} {track} {rest}"


@pytest.mark.parametrize("track", RELEASE_TRACKS)
@pytest.mark.parametrize(
    "case_id,command,expected_mutative",
    CORPUS,
    ids=[row[0] for row in CORPUS],
)
def test_release_track_prefix_preserves_the_verdict(
    case_id, command, expected_mutative, track
):
    """Prefixing a release track leaves the verdict exactly where it was."""
    plain = verdict(command)
    prefixed = verdict(with_track(command, track))

    assert plain[0] is expected_mutative, (
        f"{case_id}: the unprefixed baseline itself drifted -- expected "
        f"is_mutative={expected_mutative}, got {plain[0]} for {command!r}"
    )
    assert prefixed == plain, (
        f"{case_id}: '{track}' changed the verdict. "
        f"{command!r} -> {plain}, but "
        f"{with_track(command, track)!r} -> {prefixed}. "
        f"A gate that a single extra word walks past is not a gate; a read that "
        f"starts paying a toll for one is an over-correction."
    )


def test_the_corpus_carries_both_directions():
    """A corpus of gated forms alone cannot see the over-correction."""
    assert GATED_FORMS, "no gated form: the bypass would go unmeasured"
    assert FREE_FORMS, "no free form: the over-correction would go unmeasured"

    gated_tiers = {classify_command_tier(cmd) for _, cmd in GATED_FORMS}
    free_tiers = {classify_command_tier(cmd) for _, cmd in FREE_FORMS}
    assert gated_tiers == {T3}, f"a gated form is not T3 unprefixed: {gated_tiers}"
    assert free_tiers == {T0}, f"a free form is not T0 unprefixed: {free_tiers}"


# ---------------------------------------------------------------------------
# Over-correction: the words outside the track position
# ---------------------------------------------------------------------------

# Each entry is the same command twice, once carrying `alpha`/`beta` as a real
# argument and once with that word swapped for a neutral one. The words must be
# interchangeable -- which is exactly what fails if normalization reaches a
# position it has no business touching.
ORDINARY_WORD_PAIRS = [
    (
        "gcloud-bucket-name",
        "gcloud storage buckets remove-iam-policy-binding gs://alpha "
        "--member=allUsers --role=roles/storage.objectViewer",
        "gcloud storage buckets remove-iam-policy-binding gs://zeta "
        "--member=allUsers --role=roles/storage.objectViewer",
    ),
    (
        "gcloud-instance-name",
        "gcloud compute instances delete beta --zone=us-central1-a",
        "gcloud compute instances delete zeta --zone=us-central1-a",
    ),
    (
        "gcloud-instance-name-read",
        "gcloud compute instances describe alpha --zone=us-central1-a",
        "gcloud compute instances describe zeta --zone=us-central1-a",
    ),
    (
        "gcloud-secret-name",
        "gcloud secrets versions access latest --secret=beta",
        "gcloud secrets versions access latest --secret=zeta",
    ),
    (
        "gcloud-path-segment",
        "gcloud storage cp gs://bucket/alpha/x.txt gs://bucket/beta/x.txt",
        "gcloud storage cp gs://bucket/one/x.txt gs://bucket/two/x.txt",
    ),
    # Other CLIs, where the word sits in the position gcloud spells a track --
    # the first token after the tool. Nothing may be stripped there: the
    # normalization is declared per CLI and gcloud is the only CLI that has
    # this grammar.
    ("make-target", "make alpha", "make zeta"),
    ("git-branch", "git checkout beta", "git checkout zeta"),
    ("git-branch-delete", "git branch -D alpha", "git branch -D zeta"),
    ("kubectl-namespace", "kubectl get pods -n beta", "kubectl get pods -n zeta"),
    ("helm-release", "helm uninstall alpha", "helm uninstall zeta"),
    # This one is the pair that a broadened peel actually trips, and it is here
    # because the others do NOT: removing a trailing resource name leaves the
    # verb where it was, so those pairs stay equal even under an overreaching
    # peel and prove nothing on their own (measured -- the mutation that
    # stripped the word everywhere left them all green).
    #
    # Here the word occupies the slot gcloud spells a track, in a CLI that
    # declares none, and one position is the whole difference: with it, the
    # hyphenated verb sits at index 3 and is not split; without it, index 2,
    # where it splits onto `remove` and turns T3. So peeling for an undeclared
    # CLI is visible as a changed verdict rather than as a silent no-op.
    #
    # The pair asserts only that the two spellings AGREE. It does not endorse
    # the verdict they agree on -- `aws iam remove-user-from-group` reaching
    # this layer ungated at depth is a separate, pre-existing gap in how far
    # the hyphen split reaches, not something this normalization decides.
    (
        "undeclared-cli-track-slot",
        "aws alpha iam remove-user-from-group --user-name u --group-name g",
        "aws zeta iam remove-user-from-group --user-name u --group-name g",
    ),
]


@pytest.mark.parametrize(
    "case_id,with_word,with_neutral",
    ORDINARY_WORD_PAIRS,
    ids=[row[0] for row in ORDINARY_WORD_PAIRS],
)
def test_track_words_are_inert_outside_the_track_position(
    case_id, with_word, with_neutral
):
    """`alpha` and `beta` are ordinary words anywhere but the track slot."""
    assert verdict(with_word) == verdict(with_neutral), (
        f"{case_id}: 'alpha'/'beta' changed the verdict where it is an ordinary "
        f"argument. {with_word!r} -> {verdict(with_word)}, "
        f"{with_neutral!r} -> {verdict(with_neutral)}. Normalization reached a "
        f"position that is not the release-track slot."
    )


# ---------------------------------------------------------------------------
# The position rule itself
# ---------------------------------------------------------------------------
# Verdict equivalence is the acceptance property, but on its own it cannot see
# every over-reach: deleting a trailing resource name usually leaves the verb
# exactly where it was, so an overreaching peel produces the same verdict and
# hides. Pinning the peel's own output closes that hole -- it asserts WHICH
# word was removed, not merely that the answer came out the same.

PEELED_FORMS = [
    ("bare-track", "gcloud alpha projects describe my-proj", "gcloud projects describe my-proj"),
    ("bare-track-beta", "gcloud beta storage buckets list", "gcloud storage buckets list"),
    # The track may legally follow a global flag. Stopping at the first flag
    # would leave the same bypass available one word longer.
    (
        "track-after-global-flag",
        "gcloud --project=x alpha compute instances list",
        "gcloud --project=x compute instances list",
    ),
    ("track-alone", "gcloud alpha", "gcloud"),
]

UNTOUCHED_FORMS = [
    # gcloud, but past the track slot: an argument, a resource name, a path
    # segment, an attached flag value.
    ("resource-name", "gcloud compute instances describe alpha --zone=us-central1-a"),
    ("path-segment", "gcloud storage cp gs://alpha/x.txt gs://beta/y.txt"),
    ("attached-flag-value", "gcloud secrets versions access latest --secret=beta"),
    # A CLI that declares no track set is never entered at all.
    ("undeclared-make", "make alpha"),
    ("undeclared-git", "git checkout beta"),
    ("undeclared-kubectl", "kubectl get pods -n beta"),
    ("undeclared-aws", "aws alpha iam remove-user-from-group --user-name u"),
    # The two views disagree here: the raw walk sees `alpha` at the head, while
    # analyze_command has already spent it as the value of the single-letter
    # `-q`. Peeling on the raw view alone would feed `storage` to that flag
    # instead and drop the anchor that was matching, so the guard refuses.
    (
        "short-flag-absorbed-value",
        "gcloud -q alpha storage buckets add-iam-policy-binding gs://b "
        "--member=allUsers --role=roles/storage.objectViewer",
    ),
]


@pytest.mark.parametrize(
    "case_id,command,expected",
    PEELED_FORMS,
    ids=[row[0] for row in PEELED_FORMS],
)
def test_the_track_slot_is_peeled_to_the_exact_remainder(case_id, command, expected):
    """A track in its own slot is removed, and nothing else is."""
    remainder, peeled = _peel_release_track_prefix(command)
    assert peeled is True, f"{case_id}: {command!r} carries a track and was not peeled"
    assert remainder == expected, (
        f"{case_id}: {command!r} normalized to {remainder!r}, expected {expected!r}"
    )


@pytest.mark.parametrize(
    "case_id,command",
    UNTOUCHED_FORMS,
    ids=[row[0] for row in UNTOUCHED_FORMS],
)
def test_nothing_outside_the_track_slot_is_peeled(case_id, command):
    """Every other position keeps the word, byte for byte."""
    remainder, peeled = _peel_release_track_prefix(command)
    assert peeled is False, (
        f"{case_id}: {command!r} was normalized to {remainder!r}, but the word "
        f"here is an argument, a value, or belongs to a CLI that declares no "
        f"release track -- nothing may be removed."
    )
    assert remainder == command


def test_only_gcloud_declares_a_track_grammar():
    """The declaration is the whole scope: an absent CLI is never entered.

    Every word listed is a word this layer deletes from a command before
    reading it, so the table is the thing to keep small and deliberate.
    """
    assert set(RELEASE_TRACK_PREFIXES) == {"gcloud"}
    assert RELEASE_TRACK_PREFIXES["gcloud"] == frozenset({"alpha", "beta"})
