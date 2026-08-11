#!/usr/bin/env python3
"""Changing an IAM binding is gated in both directions on all four surfaces.

The classifier's gate on IAM was inverted, and measuring it turned out to be
open wider than the report that prompted this suite. Two separate reasons put
six of the eight forms at READ_ONLY:

* ``add-iam-policy-binding`` splits on its hyphen onto ``add``, which is
  deliberately absent from ``MUTATIVE_VERBS`` (``git add`` is local-only and
  must stay free), so it matched nothing on any surface;
* the hyphen split itself only runs at ``semantic_index <= 2``, so on the two
  THREE-token paths (``storage buckets``, ``iam service-accounts``) even
  ``remove-iam-policy-binding`` never reached ``remove`` -- it sits at index 3,
  where the token is assumed to be an argument slug rather than a subcommand.

So removal was gated on ``projects`` and ``secrets`` alone, and granting was
gated nowhere. Granting is at least as dangerous as removing -- it widens
whoever receives it, and nothing observable happens until someone uses it -- so
both directions belong at the same tier on every surface.

The eight cases are the assertion. Symmetry is the property being claimed, not
the repair of whichever half was reported first.

Each form that this work closes carries its counterfactual: with the anchor
entry withdrawn and the memoization caches dropped, it must return to the
verdict it had before, while the two forms that were already gated by the
``remove`` verb must stay at T3 without the anchor. That split is what makes
the counterfactual a measurement of the ENTRY rather than of the fixture. This
repository has shipped table entries that read as coverage and decided nothing;
an entry is closed only once its absence is observable.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import (
    COMMAND_PATH_MUTATIVE_UPGRADES,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# Granting, on each of the four surfaces the live measurement covered. All four
# were open; all four are closed by the anchor entry.
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
]

# Removal on the two-token paths. Already T3 before this work, decided by the
# hyphen split onto ``remove`` -- so these prove the anchor is not what carries
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
]

# Removal on the three-token paths, where the hyphen split does not reach.
# These were open too, and are closed by the same anchor entry as the grants.
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
]

REVOKE_COMMANDS = REVOKE_COMMANDS_ALREADY_GATED + REVOKE_COMMANDS_CLOSED_HERE

CLOSED_BY_THE_ANCHOR = GRANT_COMMANDS + REVOKE_COMMANDS_CLOSED_HERE

# Read forms of the SAME four families. Closing a binding change must not tax
# the reads that share its command group -- an over-wide anchor would turn
# these red, and that is the failure this half of the suite exists to catch.
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
]


def _clear_classifier_caches():
    """Drop every memoized verdict so a table edit is actually observed.

    Both entry points are ``lru_cache``d on the command string alone, so a
    verdict computed under one table state would survive the change and the
    counterfactual would silently measure nothing.
    """
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.fixture
def without_the_gcloud_anchors(monkeypatch):
    """Withdraw the entry this work adds, caches cleared on both edges."""
    monkeypatch.delitem(COMMAND_PATH_MUTATIVE_UPGRADES, "gcloud", raising=False)
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
    """Both directions of an IAM binding change reach T3 on all four surfaces."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{surface}: changing an IAM binding must be mutative -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{surface}: changing an IAM binding must require consent -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


@pytest.mark.parametrize(
    "surface,command",
    CLOSED_BY_THE_ANCHOR,
    ids=[f"grant-{s}" for s, _ in GRANT_COMMANDS]
    + [f"revoke-{s}" for s, _ in REVOKE_COMMANDS_CLOSED_HERE],
)
def test_iam_policy_binding_counterfactual_without_the_anchor(
    surface, command, without_the_gcloud_anchors
):
    """Every form this work closes returns to READ_ONLY once the entry is gone."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{surface}: with the anchor withdrawn this form must classify exactly "
        f"as it did before this work, or the positive case proves nothing -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "surface,command",
    REVOKE_COMMANDS_ALREADY_GATED,
    ids=[s for s, _ in REVOKE_COMMANDS_ALREADY_GATED],
)
def test_iam_policy_binding_two_token_revoke_does_not_need_the_anchor(
    surface, command, without_the_gcloud_anchors
):
    """The two-token removals were gated already and stay so without the entry.

    Their verdict comes from the hyphen split onto ``remove``, which runs only
    at ``semantic_index <= 2``. Holding them at T3 under the same fixture that
    drops the other six to T0 is what shows the fixture is not simply
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
    """Reading the same four families keeps paying nothing."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{surface}: a read form must not start demanding consent -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


def test_iam_policy_binding_gate_is_symmetric():
    """The suite asserts both directions, on the same surfaces, in equal number."""
    assert {s for s, _ in GRANT_COMMANDS} == {s for s, _ in REVOKE_COMMANDS}
    assert len(GRANT_COMMANDS) + len(REVOKE_COMMANDS) == 8
