#!/usr/bin/env python3
"""Changing an IAM binding is gated in both directions, on every surface.

The classifier's gate on IAM was inverted, and measuring it turned out to be
open wider than the report that prompted this suite. Two separate reasons put
most of these forms at READ_ONLY:

* ``add-iam-policy-binding`` splits on its hyphen onto ``add``, which is
  deliberately absent from ``MUTATIVE_VERBS`` (``git add`` is local-only and
  must stay free), so it matched nothing on any surface;
* the hyphen split itself only runs at ``semantic_index <= 2``, so on a
  THREE-token path (``storage buckets``, ``iam service-accounts``,
  ``resource-manager folders``) even ``remove-iam-policy-binding`` never
  reached ``remove`` -- it sits at index 3, where the token is assumed to be
  an argument slug rather than a subcommand.

So removal was gated on the two-token paths alone, and granting was gated
nowhere. Granting is at least as dangerous as removing -- it widens whoever
receives it, and nothing observable happens until someone uses it -- so both
directions belong at the same tier on every surface.

Symmetry is the property being claimed, not the repair of whichever half was
reported first.

The gate is a FORM rule (``IAM_BINDING_TOKEN_SUFFIXES``), not one anchor per
surface. Six per-surface anchors were what this suite first measured, and
they left ``organizations`` and ``resource-manager folders`` open -- along
with every gcloud group that grows the same pair later. An enumeration that
trails the CLI reads as coverage while the newest surface stays ungated;
the rule closes the class instead of its known instances.

Each form carries its counterfactual: with the suffix set emptied and the
memoization caches dropped, it must return to the verdict it had before,
while the forms that were already gated by the ``remove`` verb must stay at
T3 without the rule. That split is what makes the counterfactual a
measurement of the RULE rather than of the fixture. This repository has
shipped table entries that read as coverage and decided nothing; an entry is
closed only once its absence is observable.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import mutative_verbs as mutative_verbs_module
from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# Granting, on each of the six surfaces measured. All six were open.
GRANT_COMMANDS = [
    (
        "project",
        "gcloud projects add-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
    ),
    (
        "bucket",
        "gcloud storage buckets add-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
    ),
    (
        "secret",
        "gcloud secrets add-iam-policy-binding my-secret "
        "--member=serviceAccount:x@y.iam.gserviceaccount.com "
        "--role=roles/secretmanager.secretAccessor",
    ),
    (
        "service-account",
        "gcloud iam service-accounts add-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountTokenCreator",
    ),
    (
        "organization",
        "gcloud organizations add-iam-policy-binding 123456789012 "
        "--member=user:a@b.c --role=roles/owner",
    ),
    (
        "folder",
        "gcloud resource-manager folders add-iam-policy-binding 987654321098 "
        "--member=user:a@b.c --role=roles/resourcemanager.folderAdmin",
    ),
]

# Removal on the two-token paths. Already T3 before this work, decided by the
# hyphen split onto ``remove`` -- so these prove the rule is not what carries
# the counterfactual.
REVOKE_COMMANDS_ALREADY_GATED = [
    (
        "project",
        "gcloud projects remove-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
    ),
    (
        "secret",
        "gcloud secrets remove-iam-policy-binding my-secret "
        "--member=serviceAccount:x@y.iam.gserviceaccount.com "
        "--role=roles/secretmanager.secretAccessor",
    ),
    (
        "organization",
        "gcloud organizations remove-iam-policy-binding 123456789012 "
        "--member=user:a@b.c --role=roles/owner",
    ),
]

# Removal on the three-token paths, where the hyphen split does not reach.
# These were open too, and are closed by the same rule as the grants.
REVOKE_COMMANDS_CLOSED_HERE = [
    (
        "bucket",
        "gcloud storage buckets remove-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
    ),
    (
        "service-account",
        "gcloud iam service-accounts remove-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountTokenCreator",
    ),
    (
        "folder",
        "gcloud resource-manager folders remove-iam-policy-binding "
        "987654321098 --member=user:a@b.c "
        "--role=roles/resourcemanager.folderAdmin",
    ),
]

REVOKE_COMMANDS = REVOKE_COMMANDS_ALREADY_GATED + REVOKE_COMMANDS_CLOSED_HERE

CLOSED_BY_THE_RULE = GRANT_COMMANDS + REVOKE_COMMANDS_CLOSED_HERE

# Read forms of the same families. Closing a binding change must not tax the
# reads that share its command group -- an over-wide rule would turn these
# red, and that is the failure this half of the suite exists to catch.
READ_COMMANDS = [
    ("project-get-iam", "gcloud projects get-iam-policy my-proj"),
    ("project-list", "gcloud projects list"),
    ("bucket-describe", "gcloud storage buckets describe gs://my-bucket"),
    ("bucket-list", "gcloud storage buckets list"),
    ("secret-describe", "gcloud secrets describe my-secret"),
    ("secret-list", "gcloud secrets list"),
    (
        "service-account-describe",
        "gcloud iam service-accounts describe sa@proj.iam.gserviceaccount.com",
    ),
    ("service-account-list", "gcloud iam service-accounts list"),
    ("organization-get-iam", "gcloud organizations get-iam-policy 123456789012"),
    ("organization-list", "gcloud organizations list"),
    ("folder-describe", "gcloud resource-manager folders describe 987654321098"),
    (
        "folder-get-iam",
        "gcloud resource-manager folders get-iam-policy 987654321098",
    ),
]


def _clear_classifier_caches():
    """Drop every memoized verdict so a table edit is actually observed.

    Both entry points are ``lru_cache``d on the command string alone, so a
    verdict computed under one table state would survive the change and the
    counterfactual would silently measure nothing.
    """
    detect_mutative_command.cache_clear()
    mutative_verbs_module._detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.fixture
def without_the_binding_rule(monkeypatch):
    """Withdraw the form rule this work adds, caches cleared on both edges.

    Emptying the suffix set rather than patching the classifier keeps the
    counterfactual a measurement of the ENTRY: every code path stays live and
    only the membership it consults goes away.
    """
    monkeypatch.setattr(
        mutative_verbs_module, "IAM_BINDING_TOKEN_SUFFIXES", frozenset()
    )
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.mark.parametrize(
    "surface,command",
    GRANT_COMMANDS + REVOKE_COMMANDS,
    ids=[f"grant-{s}" for s, _ in GRANT_COMMANDS]
    + [f"revoke-{s}" for s, _ in REVOKE_COMMANDS],
)
def test_iam_policy_binding_change_is_mutative_and_t3(surface, command):
    """Both directions of an IAM binding change reach T3 on every surface."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{surface}: changing an IAM binding must be mutative -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{surface}: changing an IAM binding must require consent -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


def test_iam_policy_binding_grant_never_classifies_below_revoke():
    """Granting a capability cannot cost less consent than revoking it.

    Asserted as a pairing rather than as two independent lists: the defect
    was not that a form was open, it was that the two directions of the same
    act on the same resource disagreed.
    """
    grants = dict(GRANT_COMMANDS)
    revokes = dict(REVOKE_COMMANDS)
    assert set(grants) == set(revokes)
    for surface in grants:
        assert classify_command_tier(grants[surface]) == classify_command_tier(
            revokes[surface]
        ), f"{surface}: grant and revoke classify differently"


@pytest.mark.parametrize(
    "surface,command",
    CLOSED_BY_THE_RULE,
    ids=[f"grant-{s}" for s, _ in GRANT_COMMANDS]
    + [f"revoke-{s}" for s, _ in REVOKE_COMMANDS_CLOSED_HERE],
)
def test_iam_policy_binding_counterfactual_without_the_rule(
    surface, command, without_the_binding_rule
):
    """Every form this work closes returns to READ_ONLY once the rule is gone."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{surface}: with the rule withdrawn this form must classify exactly "
        f"as it did before this work, or the positive case proves nothing -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "surface,command",
    REVOKE_COMMANDS_ALREADY_GATED,
    ids=[s for s, _ in REVOKE_COMMANDS_ALREADY_GATED],
)
def test_iam_policy_binding_two_token_revoke_does_not_need_the_rule(
    surface, command, without_the_binding_rule
):
    """The two-token removals were gated already and stay so without the rule.

    Their verdict comes from the hyphen split onto ``remove``, which runs only
    at ``semantic_index <= 2``. Holding them at T3 under the same fixture that
    drops the other nine to T0 is what shows the fixture is not simply
    disabling the classifier.
    """
    assert detect_mutative_command(command).is_mutative is True
    assert classify_command_tier(command) == T3


@pytest.mark.parametrize(
    "surface,command",
    READ_COMMANDS,
    ids=[s for s, _ in READ_COMMANDS],
)
def test_iam_policy_binding_read_forms_stay_free(surface, command):
    """Reading the same families keeps paying nothing."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{surface}: a read form must not start demanding consent -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


def test_iam_policy_binding_gate_is_symmetric():
    """The suite asserts both directions, on the same surfaces, in equal number."""
    assert {s for s, _ in GRANT_COMMANDS} == {s for s, _ in REVOKE_COMMANDS}
    assert len(GRANT_COMMANDS) == len(REVOKE_COMMANDS) == 6
