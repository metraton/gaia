#!/usr/bin/env python3
"""`--prune` scales its escalation to the CLI where it actually destroys.

``--prune`` sat in ``DANGEROUS_FLAGS`` as an ``ALWAYS`` entry: it escalated a
read-only verb to T3 on every CLI that carried it, with no regard for what the
flag actually does there. Measured friction: ``git fetch --prune`` -- which
only deletes LOCAL remote-tracking refs whose branch is already gone on the
remote, bookkeeping that mirrors reality the caller has already lost -- cost
15 approvals for an operation that destroys nothing the caller owns.

The risk is inverted from a false negative: freeing the flag globally would
also free ``kubectl apply --prune`` (deletes live cluster resources not in the
applied set) and any read-only verb carrying ``--prune`` on the infrastructure
CLIs this repository observes (``terraform``/``terragrunt``) -- real
destruction of state that is not the caller's own working copy. So this suite
asserts BOTH halves in the same table: the one path that is released, and
every path -- named by the gate or found while auditing the flag's reach --
that must keep costing consent. A change that only lowered the first half
without pinning the second would look like a fix and would not be one.

The mechanism is the SAME family-scoped-by-flag anchor
(``MutativeAnchor``/``_validated_anchor_table``) built for
``COMMAND_PATH_MUTATIVE_UPGRADES`` in the M2 PREVIA task, applied here in the
opposite direction: ``COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS`` subtracts one flag
from the ALWAYS-escalation set for one exact (CLI, path), rather than adding a
T3 verdict. No new mechanism, no CLI outside the one exact anchored path is
touched, and a SECOND always-dangerous flag on the exempted path is
unaffected -- ``git fetch --prune --force`` still escalates on ``--force``.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import (
    COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# The exact friction case, plus one variant that still names a remote --
# neither depends on any other property of the command than the (git, fetch,
# --prune) anchor.
RELEASED_COMMANDS = [
    ("bare", "git fetch --prune"),
    ("named-remote", "git fetch --prune origin"),
]

# Forms where the identical flag keeps costing consent, because the family
# (and, for the infra CLIs, a read-only verb relying on the SAME ALWAYS
# mechanism) actually destroys real state. `cluster` and `infra` are the two
# families the gate names; the compound git form is not a family boundary at
# all -- it proves the exemption is scoped to the FLAG, not to the CLI or the
# whole command, by keeping the exempted path gated the instant a second,
# non-exempted ALWAYS flag joins it.
STILL_ESCALATES = [
    (
        "cluster-kubectl-apply-prune",
        "kubectl apply -f k8s/ --prune -l app=guestbook",
    ),
    ("infra-terraform-state-list-prune", "terraform state list --prune"),
    ("infra-terragrunt-state-list-prune", "terragrunt state list --prune"),
    ("git-fetch-prune-plus-force", "git fetch --prune --force"),
]

# Reads of the same command group must not start costing consent as a side
# effect of narrowing the flag's reach.
READ_FORMS_STAY_FREE = [
    ("fetch-with-remote", "git fetch origin"),
    ("fetch-bare", "git fetch"),
    ("fetch-unrelated-flag", "git fetch --all"),
]


def _clear_classifier_caches():
    """Drop every memoized verdict so a table edit is actually observed.

    Both entry points are ``lru_cache``d on the command string alone, so a
    verdict computed under one table state would otherwise survive the change
    and the counterfactual would silently measure nothing.
    """
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.fixture
def without_the_prune_exemption(monkeypatch):
    """Withdraw the entry this work adds, caches cleared on both edges."""
    monkeypatch.delitem(COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS, "git", raising=False)
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.mark.parametrize(
    "case_id,command",
    RELEASED_COMMANDS,
    ids=[c for c, _ in RELEASED_COMMANDS],
)
def test_prune_flag_family_scope_git_fetch_is_released(case_id, command):
    """`--prune` on `git fetch` no longer escalates: it only removes local
    remote-tracking refs already gone on the remote."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: {command!r} must classify read-only -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    STILL_ESCALATES,
    ids=[c for c, _ in STILL_ESCALATES],
)
def test_prune_flag_family_scope_destructive_forms_stay_gated(case_id, command):
    """The same flag keeps costing consent everywhere it can destroy real
    state -- the cluster CLI, the infrastructure CLIs, and a second
    ALWAYS flag riding along the exempted path."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{case_id}: {command!r} must stay mutative -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{case_id}: {command!r} must require consent -- "
        f"got {classify_command_tier(command)}"
    )


@pytest.mark.parametrize(
    "case_id,command",
    READ_FORMS_STAY_FREE,
    ids=[c for c, _ in READ_FORMS_STAY_FREE],
)
def test_prune_flag_family_scope_unrelated_fetch_forms_stay_free(case_id, command):
    """Narrowing the flag's reach must not tax the fetch forms that never
    carried it in the first place."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    RELEASED_COMMANDS,
    ids=[c for c, _ in RELEASED_COMMANDS],
)
def test_prune_flag_family_scope_counterfactual_without_the_anchor(
    case_id, command, without_the_prune_exemption
):
    """With the anchor withdrawn, the released form returns to exactly the
    verdict it had before this work -- proving the ENTRY does the work, not
    some unrelated rule that happened to classify the same string."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{case_id}: without the exemption, {command!r} must escalate on the "
        f"ALWAYS --prune flag again -- got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3
    assert "--prune" in result.dangerous_flags


@pytest.mark.parametrize(
    "case_id,command",
    STILL_ESCALATES,
    ids=[c for c, _ in STILL_ESCALATES],
)
def test_prune_flag_family_scope_controls_unaffected_by_the_counterfactual(
    case_id, command, without_the_prune_exemption
):
    """Withdrawing the git exemption must not be what is holding the other
    families gated -- they were never anchored by it."""
    assert detect_mutative_command(command).is_mutative is True
    assert classify_command_tier(command) == T3


def test_prune_flag_family_scope_anchor_is_scoped_to_fetch_only():
    """The anchor names `fetch` explicitly, not the whole `git` CLI -- so a
    destructive git form sharing no path with it is never a candidate for
    exemption by construction."""
    fetch_paths = {
        anchor.path for anchor in COMMAND_PATH_ALWAYS_FLAG_EXEMPTIONS["git"]
    }
    assert fetch_paths == {("fetch",)}, (
        "the prune exemption must anchor `fetch` alone, not widen to any "
        "other git subcommand"
    )
