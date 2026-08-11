#!/usr/bin/env python3
"""A boolean short flag does not change what a command is classified as.

``analyze_command`` spends the token after a single-letter short flag as that
flag's value whenever the flag precedes the first positional. That is right for
``git -C <path>`` and ``kubectl -n <ns>``; it is wrong for every short flag that
takes no value at all. ``gcloud -q storage buckets add-iam-policy-binding`` had
``storage`` eaten by ``-q``, so ``non_flag_tokens`` started one token late and
every anchor path declared for that CLI missed at position 0.

Measured live before the fix, on two independent contracts: the plain form was
T3, the ``-q`` form was T0, and the long form ``--quiet`` was T3. The long form
is the oracle -- it is the same flag, it changes nothing about the operation,
and the classifier already gets it right. What ``--quiet`` does is what ``-q``
must do.

The property asserted here is an EQUIVALENCE, and it is deliberately not a list
of repaired cases:

* inserting a boolean short flag leaves the verdict exactly where it was -- a
  gated form that goes free with the flag is the bypass, and a free form that
  starts paying a toll for one is the over-correction; and
* the short and long spellings of one flag agree with each other, which is the
  same property stated against the in-system oracle rather than against the
  unflagged form.

Stated that way the equivalence is also the guard on the table itself. A flag
wrongly declared boolean leaves its value standing as a positional, which shifts
the head by one exactly as the old absorption did -- so a wrong entry fails this
test on any CLI whose corpus carries a form whose verdict is derived from token
position. ``test_every_declared_cli_carries_a_gated_form`` is what keeps that
condition true as the table grows.

The monotonicity half of the acceptance criterion lives in
``test_boolean_short_flag_monotonicity.py``: this file measures that the verdict
did not move, that one measures that it can only ever move upward.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.command_semantics import (
    BOOLEAN_SHORT_FLAGS,
    analyze_command,
    tokenize_command,
)
from modules.security.mutative_verbs import (
    _git_worktree_positionals,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED


def verdict(command: str):
    """The pair a caller of this layer acts on: gate decision, then tier."""
    return (detect_mutative_command(command).is_mutative, classify_command_tier(command))


# ---------------------------------------------------------------------------
# The corpus. Both halves are load-bearing: the gated forms detect the bypass,
# the free forms detect the over-correction. Every expected verdict below was
# measured against the unflagged form rather than assumed, so a form that
# silently changes tier cannot be mistaken for the equivalence holding -- two
# commands agreeing on the WRONG verdict would still be equal to each other.
# ---------------------------------------------------------------------------

GATED_FORMS = [
    # gcloud: the reported defect, plus the sibling operations that share the
    # anchor table it walked past.
    ("gcloud-bucket-iam-grant",
     "gcloud storage buckets add-iam-policy-binding gs://b "
     "--member=allUsers --role=roles/storage.objectViewer"),
    ("gcloud-bucket-iam-revoke",
     "gcloud storage buckets remove-iam-policy-binding gs://b "
     "--member=allUsers --role=roles/storage.objectViewer"),
    ("gcloud-project-iam-grant",
     "gcloud projects add-iam-policy-binding p --member=user:a@b.c --role=roles/owner"),
    ("gcloud-sa-create", "gcloud iam service-accounts create my-sa"),
    ("gcloud-instance-delete", "gcloud compute instances delete my-vm --zone=us-central1-a"),
    ("gcloud-run-deploy",
     "gcloud run deploy my-svc --image=gcr.io/p/i --region=us-central1"),
    ("gcloud-topic-delete", "gcloud pubsub topics delete my-topic"),
    ("gcloud-secret-destroy", "gcloud secrets versions destroy 1 --secret=my-secret"),
    # gsutil: `-m` is the flag people actually type, and it is boolean.
    ("gsutil-rm", "gsutil rm gs://b/o.txt"),
    ("gsutil-cp", "gsutil cp local.txt gs://b/o.txt"),
    # git: `-p` is --paginate. A push behind it was free.
    ("git-push", "git push origin main"),
    ("git-worktree-remove", "git worktree remove /tmp/wt"),
    ("kubectl-delete", "kubectl delete pod my-pod"),
    ("kubectl-apply", "kubectl apply -f manifest.yaml"),
    ("helm-uninstall", "helm uninstall my-release"),
    ("docker-rm", "docker rm my-container"),
    ("npm-install", "npm install left-pad"),
    ("npm-publish", "npm publish"),
    ("aptget-install", "apt-get install nginx"),
    ("aptget-remove", "apt-get remove nginx"),
    ("apt-install", "apt install nginx"),
    ("yum-install", "yum install nginx"),
    ("dnf-install", "dnf install nginx"),
    ("pip-install", "pip install requests"),
    ("pip3-install", "pip3 install requests"),
    ("flux-delete", "flux delete kustomization app"),
]

FREE_FORMS = [
    ("gcloud-buckets-list", "gcloud storage buckets list"),
    ("gcloud-bucket-iam-read", "gcloud storage buckets get-iam-policy gs://b"),
    ("gcloud-instances-list", "gcloud compute instances list"),
    ("gcloud-project-describe", "gcloud projects describe my-proj"),
    ("gsutil-ls", "gsutil ls gs://b"),
    ("git-status", "git status"),
    ("git-log", "git log --oneline"),
    ("kubectl-get", "kubectl get pods"),
    ("kubectl-describe", "kubectl describe pod my-pod"),
    ("helm-list", "helm list"),
    ("docker-ps", "docker ps"),
    ("npm-ls", "npm ls"),
    ("pip-list", "pip list"),
    ("flux-get", "flux get kustomizations"),
]

CORPUS = [(case_id, command, True) for case_id, command in GATED_FORMS] + [
    (case_id, command, False) for case_id, command in FREE_FORMS
]


def base_cmd_of(command: str) -> str:
    """The table key a command is looked up under."""
    return analyze_command(command).base_cmd


def with_flag(command: str, flag: str) -> str:
    """Insert *flag* where a global flag belongs: between the tool and the command."""
    tool, rest = command.split(" ", 1)
    return f"{tool} {flag} {rest}"


# A help flag is boolean, and it is the one boolean flag that is NOT verdict-
# neutral: it replaces the operation with printing usage, so the classifier
# downgrades it on purpose (the ``HELP_FLAGS`` lane in mutative_verbs).
# Measured, so this is an observation rather than a convenient exemption:
# ``gcloud --help pubsub topics delete`` and ``gcloud -h pubsub topics delete``
# BOTH come back non-mutative, and ``git --help push`` and ``git -h push`` BOTH
# stay mutative. The insertion property below asks whether a flag that changes
# nothing changed the verdict, which is not a question that applies to a flag
# that changes the command; the oracle property does apply, and it is the
# stronger claim -- it holds ``-h`` to whatever ``--help`` does, downgrade
# included.
VERDICT_CHANGING_SHORT_FLAGS = frozenset({"-h"})

# Every (command, flag) pair the corpus and the table produce together. Driving
# the parametrization from BOOLEAN_SHORT_FLAGS rather than from a second hand-
# written list is what makes a future table entry arrive already measured
# instead of arriving unmeasured and green.
INSERTIONS = [
    (f"{case_id}+{flag}", command, flag, expected_mutative)
    for case_id, command, expected_mutative in CORPUS
    for flag in sorted(BOOLEAN_SHORT_FLAGS.get(base_cmd_of(command), ()))
    if flag not in VERDICT_CHANGING_SHORT_FLAGS
]


@pytest.mark.parametrize(
    "case_id,command,flag,expected_mutative",
    INSERTIONS,
    ids=[row[0] for row in INSERTIONS],
)
def test_a_boolean_short_flag_preserves_the_verdict(
    case_id, command, flag, expected_mutative
):
    """Inserting a boolean short flag leaves the verdict exactly where it was."""
    plain = verdict(command)
    flagged = verdict(with_flag(command, flag))

    assert plain[0] is expected_mutative, (
        f"{case_id}: the unflagged baseline itself drifted -- expected "
        f"is_mutative={expected_mutative}, got {plain[0]} for {command!r}"
    )
    assert flagged == plain, (
        f"{case_id}: {flag!r} changed the verdict. "
        f"{command!r} -> {plain}, but {with_flag(command, flag)!r} -> {flagged}. "
        f"A gate that one valueless flag walks past is not a gate; a read that "
        f"starts paying a toll for one is an over-correction."
    )


# ---------------------------------------------------------------------------
# The oracle: the same flag, spelled long
# ---------------------------------------------------------------------------
# The long form is not a second opinion, it is the SAME flag. The classifier
# already reads it correctly, which is what makes it usable as the standard the
# short form is held to -- and it holds even where the unflagged baseline is
# not available to compare against, because it fixes the flag and varies only
# the spelling.

SHORT_LONG_WITNESSES = [
    ("gcloud", "-q", "--quiet"),
    ("gcloud", "-h", "--help"),
    ("git", "-p", "--paginate"),
    ("git", "-P", "--no-pager"),
    ("git", "-h", "--help"),
    ("kubectl", "-h", "--help"),
    ("helm", "-h", "--help"),
    ("flux", "-h", "--help"),
    ("docker", "-D", "--debug"),
    ("docker", "-h", "--help"),
    ("npm", "-g", "--global"),
    ("apt", "-y", "--assume-yes"),
    ("apt-get", "-y", "--assume-yes"),
    ("apt-get", "-h", "--help"),
    ("yum", "-y", "--assumeyes"),
    ("dnf", "-y", "--assumeyes"),
    ("pip", "-q", "--quiet"),
    ("pip", "-h", "--help"),
    ("pip3", "-h", "--help"),
]

WITNESS_PAIRS = [
    (f"{case_id}+{short}", command, short, long_form)
    for case_id, command, _ in CORPUS
    for cli, short, long_form in SHORT_LONG_WITNESSES
    if base_cmd_of(command) == cli
]


@pytest.mark.parametrize(
    "case_id,command,short,long_form",
    WITNESS_PAIRS,
    ids=[row[0] for row in WITNESS_PAIRS],
)
def test_short_and_long_spellings_of_one_flag_agree(case_id, command, short, long_form):
    """``-q`` classifies as ``--quiet`` does. The long form is the oracle."""
    short_verdict = verdict(with_flag(command, short))
    long_verdict = verdict(with_flag(command, long_form))

    assert short_verdict == long_verdict, (
        f"{case_id}: {short!r} and {long_form!r} are the same flag and must "
        f"classify the same. {with_flag(command, short)!r} -> {short_verdict}, "
        f"{with_flag(command, long_form)!r} -> {long_verdict}."
    )


# ---------------------------------------------------------------------------
# The table's own preconditions
# ---------------------------------------------------------------------------


def test_the_corpus_carries_both_directions():
    """A corpus of gated forms alone cannot see the over-correction."""
    assert GATED_FORMS, "no gated form: the bypass would go unmeasured"
    assert FREE_FORMS, "no free form: the over-correction would go unmeasured"

    gated_tiers = {classify_command_tier(cmd) for _, cmd in GATED_FORMS}
    free_tiers = {classify_command_tier(cmd) for _, cmd in FREE_FORMS}
    assert gated_tiers == {T3}, f"a gated form is not T3 unflagged: {gated_tiers}"
    assert free_tiers == {T0}, f"a free form is not T0 unflagged: {free_tiers}"


def test_every_declared_cli_carries_a_gated_form():
    """A CLI in the table without a gated form declares a flag nothing measures.

    The equivalence above is what converts a wrong table entry -- a flag that
    really does take a value -- from a runtime gate that opens into a test that
    fails. It can only do that on a CLI whose corpus carries a form whose
    verdict comes from token position, which a gated form is and a read is not.
    """
    corpus_clis = {base_cmd_of(cmd) for _, cmd in GATED_FORMS}
    undeclared = sorted(set(BOOLEAN_SHORT_FLAGS) - corpus_clis)
    assert not undeclared, (
        f"these CLIs declare boolean short flags but carry no gated corpus form, "
        f"so a wrong entry for them would ship unmeasured: {undeclared}"
    )


def test_a_value_taking_short_flag_still_absorbs_its_value():
    """The narrowing is a narrowing: an undeclared short flag is untouched.

    ``-C``, ``-n`` and ``-m`` really do take a value on these CLIs, and the
    value must stay out of the semantic tokens -- putting it in would shift the
    head by one and drop the very anchors this change exists to restore.
    """
    assert analyze_command("git -C /repo push origin main").non_flag_tokens == (
        "push", "origin", "main",
    )
    assert analyze_command("kubectl -n prod delete pod x").non_flag_tokens == (
        "delete", "pod", "x",
    )
    assert analyze_command("python3 -m pip install requests").non_flag_tokens == (
        "install", "requests",
    )


def test_the_table_is_matched_case_sensitively():
    """``-C`` and ``-c`` are different flags, and one of them takes a value.

    yum and dnf spell ``--cacheonly`` ``-C`` and ``--config <file>`` ``-c``. Only
    the first is declared. Folding case when looking the table up would hand the
    second one's behaviour to the first, leaving the config PATH standing as the
    head positional. The verdict happens to survive that on these commands --
    ``install`` is still found, one position later -- which is exactly why this
    is asserted on the tokens instead of on the verdict.
    """
    assert analyze_command("yum -c /etc/yum.conf install nginx").non_flag_tokens == (
        "install", "nginx",
    )
    assert analyze_command("dnf -c /etc/dnf.conf install nginx").non_flag_tokens == (
        "install", "nginx",
    )
    # The declared spelling, for contrast: `-C` takes nothing, so nothing moves.
    assert analyze_command("yum -C install nginx").non_flag_tokens == (
        "install", "nginx",
    )


def test_the_worktree_walk_reads_the_same_grammar():
    """The one original-case token walk in the tree agrees with this one.

    ``_git_worktree_positionals`` cannot use ``non_flag_tokens`` -- it needs
    original case to resolve a path -- so it walks the tokens itself. It asks
    ``absorbs_next_token`` rather than restating the rule, and this is what
    holds the two together: a second copy would still absorb ``worktree`` after
    git's valueless ``-p`` and disagree with the semantic view about where the
    subcommand starts. The verdict does not expose that (the generic verb scan
    finds ``remove`` either way), so it is asserted on the positionals.
    """
    assert _git_worktree_positionals(
        tokenize_command("git -p worktree remove /tmp/wt")
    ) == ["worktree", "remove", "/tmp/wt"]
    # `-C` really does take a path, so the path is still absorbed and the walk
    # still starts at the subcommand.
    assert _git_worktree_positionals(
        tokenize_command("git -C /repo worktree remove /tmp/wt")
    ) == ["worktree", "remove", "/tmp/wt"]


def test_the_reported_defect_is_closed():
    """The three spellings measured live, pinned as the regression they were."""
    plain = "gcloud storage buckets add-iam-policy-binding gs://b --member=allUsers"
    short = "gcloud -q storage buckets add-iam-policy-binding gs://b --member=allUsers"
    long_form = (
        "gcloud --quiet storage buckets add-iam-policy-binding gs://b --member=allUsers"
    )

    assert verdict(plain) == (True, T3)
    assert verdict(short) == (True, T3)
    assert verdict(long_form) == (True, T3)
