#!/usr/bin/env python3
"""Executable truth table for the live command classifier.

Every case is a LITERAL command string fed to the live classifier
(``detect_mutative_command`` + ``classify_command_tier``), asserting the PAIR
``(is_mutative, tier)`` rather than either half alone: the two are decided by
different code paths, and a change that flips one without the other is exactly
the kind of drift a single-value assertion hides.

Two families share ONE table on purpose:

- ``OPEN`` -- forms that the classifier does NOT gate today. They are recorded
  with their CURRENT verdict, not the desired one. The table is a baseline, so
  closing one of these gaps must show up here as a deliberate edit to the
  expected verdict; a gap that closes silently is indistinguishable from a
  regression.
- ``GATED`` / ``FREE`` -- controls. Commands that already resolve to T3, and
  read-only commands that already resolve free. They live in the same table so
  that OVERCORRECTING breaks it just as loudly as undercorrecting: widening a
  rule until a read form starts demanding consent turns a control red.

The table is the shared harness: work that changes a verdict extends this
table instead of standing up its own assertions somewhere else.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.mutative_verbs import detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier

OPEN = "open"
GATED = "gated"
FREE = "free"

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# (case_id, family, command, expected_is_mutative, expected_tier)
CLASSIFIER_TRUTH_TABLE = [
    # ---- OPEN: granting capability is not gated the way removing it is ----
    (
        "iam-grant-project",
        OPEN,
        "gcloud projects add-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
        False,
        T0,
    ),
    (
        "iam-grant-bucket",
        OPEN,
        "gcloud storage buckets add-iam-policy-binding gs://my-bucket "
        "--member=allUsers --role=roles/storage.objectViewer",
        False,
        T0,
    ),
    (
        "iam-grant-secret",
        OPEN,
        "gcloud secrets add-iam-policy-binding my-secret "
        "--member=serviceAccount:x@y.iam.gserviceaccount.com "
        "--role=roles/secretmanager.secretAccessor",
        False,
        T0,
    ),
    (
        "iam-grant-service-account",
        OPEN,
        "gcloud iam service-accounts add-iam-policy-binding "
        "sa@proj.iam.gserviceaccount.com --member=user:a@b.c "
        "--role=roles/iam.serviceAccountTokenCreator",
        False,
        T0,
    ),
    # ---- OPEN: a read-only noun decides before the mutating verb is read ----
    ("redirect-project", OPEN, "gcloud config set project other-project", False, T0),
    ("redirect-account", OPEN, "gcloud config set account someone@else.com", False, T0),
    ("git-config-write", OPEN, "git config user.email someone@else.com", False, T0),
    # ---- OPEN: indirect trigger and live workload ----
    ("workflow-trigger", OPEN, "gh workflow run deploy.yml --ref main", False, T0),
    ("workflow-retrigger", OPEN, "gh run rerun 123456", False, T0),
    ("workflow-cancel", OPEN, "gh run cancel 123456", False, T0),
    (
        "workload-create",
        OPEN,
        "kubectl run debug-pod --image=alpine:3.20 -- sleep 3600",
        False,
        T0,
    ),
    # ---- OPEN: state, destination and direct write ----
    ("state-upgrade", OPEN, "terraform init -upgrade", False, T0),
    ("state-migrate", OPEN, "terraform init -migrate-state", False, T0),
    (
        "remote-add",
        OPEN,
        "git remote add upstream git@github.com:other/repo.git",
        False,
        T0,
    ),
    (
        "sensitive-write",
        OPEN,
        "tee /home/jorge/ws/me/gaia/hooks/pre_tool_use.py",
        False,
        T0,
    ),
    # ---- GATED controls: already T3, must stay T3 ----
    ("control-pr-merge", GATED, "gh pr merge 42 --squash", True, T3),
    (
        "control-api-write",
        GATED,
        "gh api -X POST /repos/o/r/issues -f title=x",
        True,
        T3,
    ),
    ("control-release-create", GATED, "gh release create v1.2.3", True, T3),
    (
        "control-secret-create",
        GATED,
        "gcloud secrets create my-secret --data-file=-",
        True,
        T3,
    ),
    (
        "control-rollout-restart",
        GATED,
        "kubectl rollout restart deployment/api",
        True,
        T3,
    ),
    (
        "control-iam-revoke",
        GATED,
        "gcloud projects remove-iam-policy-binding my-proj "
        "--member=user:a@b.c --role=roles/owner",
        True,
        T3,
    ),
    ("control-push-force", GATED, "git push --force origin main", True, T3),
    ("control-kubectl-delete", GATED, "kubectl delete pod my-pod", True, T3),
    ("control-terraform-apply", GATED, "terraform apply -auto-approve", True, T3),
    ("control-rm-recursive", GATED, "rm -rf /home/jorge/ws/me/gaia/hooks", True, T3),
    # ---- FREE controls: reads that must not start paying a toll ----
    ("read-gcloud-config", FREE, "gcloud config get-value project", False, T0),
    ("read-gh-run-list", FREE, "gh run list --limit 5", False, T0),
    ("read-kubectl-get", FREE, "kubectl get pods -o json", False, T0),
    ("read-terraform-init", FREE, "terraform init", False, T0),
    ("read-git-remote", FREE, "git remote -v", False, T0),
]

_MINIMUM_MEASURED_CASES = 26


@pytest.mark.parametrize(
    "case_id,family,command,expected_mutative,expected_tier",
    CLASSIFIER_TRUTH_TABLE,
    ids=[row[0] for row in CLASSIFIER_TRUTH_TABLE],
)
def test_classifier_truth_table_verdict(
    case_id, family, command, expected_mutative, expected_tier
):
    """The live classifier returns the recorded (is_mutative, tier) pair."""
    result = detect_mutative_command(command)
    tier = classify_command_tier(command)

    assert result.is_mutative is expected_mutative, (
        f"[{family}] {case_id}: is_mutative drifted for {command!r} -- "
        f"expected {expected_mutative}, got {result.is_mutative} "
        f"(verb={result.verb!r}, category={result.category!r})"
    )
    assert tier == expected_tier, (
        f"[{family}] {case_id}: tier drifted for {command!r} -- "
        f"expected {expected_tier}, got {tier}"
    )


def test_classifier_truth_table_covers_the_measured_corpus():
    """The table keeps at least the measured corpus, with unique ids and commands."""
    assert len(CLASSIFIER_TRUTH_TABLE) >= _MINIMUM_MEASURED_CASES

    ids = [row[0] for row in CLASSIFIER_TRUTH_TABLE]
    assert len(ids) == len(set(ids)), "duplicate case id in the truth table"

    commands = [row[2] for row in CLASSIFIER_TRUTH_TABLE]
    assert len(commands) == len(set(commands)), "duplicate command in the truth table"


def test_classifier_truth_table_carries_both_directions():
    """Both an open form and a gated control are present.

    A table of only-open or only-gated cases cannot detect the failure mode it
    exists for: undercorrecting is invisible without controls, overcorrecting is
    invisible without open forms.
    """
    families = {row[1] for row in CLASSIFIER_TRUTH_TABLE}
    assert {OPEN, GATED, FREE} <= families
