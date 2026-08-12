#!/usr/bin/env python3
"""Provoking a remote execution, or bringing a live workload to life, stops
passing free through the classifier.

Four forms across two CLIs carried no verb in ``MUTATIVE_VERBS`` and so fell
through Step 4 of ``detect_mutative_command`` to READ_ONLY "by elimination":

- ``gh workflow run`` dispatches a ``workflow_dispatch`` run against whatever
  ref is given -- a remote trigger with the same effect as pushing the commit
  that would have triggered it.
- ``gh run rerun`` re-dispatches a run that already completed -- the same
  remote-execution effect, reached from a different entry point.
- ``gh run cancel`` was not in the original brief. It surfaced while closing
  the two forms above as the SAME property observed from the other
  direction: reaching INTO an execution that is currently running and
  changing its outcome, rather than starting one. The property this gate
  holds is "provoke or reach into a remote execution," and cancellation is
  an instance of reaching into one, not a separate concern.
- ``kubectl run --image`` schedules a live, running workload against
  whatever cluster the current context points at.

``run`` is DELIBERATELY absent from ``MUTATIVE_VERBS`` -- the module's own
comment reads "safe by elimination" -- because a global entry would gate
every ``docker run`` and similar routine dev-workflow invocation. ``rerun``
and ``cancel`` were never in the taxonomy either. So the repair anchors each
form in ``COMMAND_PATH_MUTATIVE_UPGRADES`` by exact (family, subcommand) --
or, for ``kubectl run``, by (family, flag), since the bare subcommand alone
names no form that actually starts a workload -- never by widening a verb
globally.

Both faces are one table on purpose. Listing and viewing workflows, viewing
a run, and listing what is already running on a cluster are the read
surface of the exact same CLIs and must keep costing nothing; a suite that
only closed the writes could not tell a repair from a toll on every
read.

Every closed form carries its counterfactual: withdraw the anchor, drop the
memoized verdicts, and the form must return to exactly what it classified
before. A present entry is not a firing one -- this repository has shipped a
whole table that read as coverage and decided nothing.
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
T2 = SecurityTier.T2_DRY_RUN
T3 = SecurityTier.T3_BLOCKED

# --- Face (a): the forms that now require consent ---------------------------
CLOSED = [
    ("gh-workflow-run", "gh workflow run deploy.yml --ref main"),
    ("gh-run-rerun", "gh run rerun 123456"),
    ("gh-run-cancel", "gh run cancel 123456"),
    (
        "kubectl-run-image",
        "kubectl run debug-pod --image=alpine:3.20 -- sleep 3600",
    ),
]

# The exact anchors this work adds, keyed by base command -- used only to
# withdraw precisely these entries for the counterfactual, and nothing a
# sibling task anchored under the same base command.
_ANCHORED_PATHS_BY_BASE_CMD = {
    "gh": {("workflow", "run"), ("run", "rerun"), ("run", "cancel")},
    "kubectl": {("run",)},
}

# --- Face (b): reads of the same flows, runs, and cluster stay free ---------
READS = [
    ("gh-workflow-list", "gh workflow list"),
    ("gh-workflow-view", "gh workflow view deploy.yml"),
    ("gh-run-view", "gh run view 123456"),
    ("gh-run-list", "gh run list --limit 5"),
    ("kubectl-get-pods", "kubectl get pods -o json"),
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
def without_the_trigger_anchors(monkeypatch):
    """Withdraw exactly the anchors this work adds, caches cleared on both edges.

    Filters by the exact path this work declared, so an anchor a sibling task
    placed under the same base command (e.g. gcloud/kubectl/gh `config`)
    survives and the counterfactual measures this entry, not the fixture.
    """
    for base_cmd, paths in _ANCHORED_PATHS_BY_BASE_CMD.items():
        anchors = COMMAND_PATH_MUTATIVE_UPGRADES.get(base_cmd, ())
        survivors = tuple(a for a in anchors if a.path not in paths)
        if len(survivors) == len(anchors):
            continue
        if survivors:
            monkeypatch.setitem(COMMAND_PATH_MUTATIVE_UPGRADES, base_cmd, survivors)
        else:
            monkeypatch.delitem(COMMAND_PATH_MUTATIVE_UPGRADES, base_cmd)
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.mark.parametrize("case_id,command", CLOSED, ids=[c for c, _ in CLOSED])
def test_indirect_trigger_or_live_workload_closed_forms_are_mutative_and_t3(
    case_id, command
):
    """Face (a): triggering, re-triggering, or cancelling a remote execution,
    and bringing a live workload to life, require consent."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{case_id}: must be mutative -- got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{case_id}: must require consent -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


@pytest.mark.parametrize("case_id,command", READS, ids=[c for c, _ in READS])
def test_indirect_trigger_or_live_workload_read_forms_stay_free(case_id, command):
    """Face (b): listing/viewing flows and runs, and listing cluster
    workloads, keep costing nothing.

    This is the half that separates a repair from an overcorrection.
    """
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: reading must not start demanding consent -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0, (
        f"{case_id}: must stay T0 -- got {classify_command_tier(command)} "
        f"for {command!r}"
    )


@pytest.mark.parametrize("case_id,command", CLOSED, ids=[c for c, _ in CLOSED])
def test_indirect_trigger_or_live_workload_counterfactual_without_the_anchor(
    case_id, command, without_the_trigger_anchors
):
    """Every closed form returns to READ_ONLY once its anchor is withdrawn.

    A present entry is not a firing one -- this proves the entry is what
    moved the verdict, not some unrelated rule that happened to agree with it.
    """
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: with the anchor withdrawn this form must classify "
        f"exactly as it did before this work, or the positive case proves "
        f"nothing -- got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize("case_id,command", READS, ids=[c for c, _ in READS])
def test_indirect_trigger_or_live_workload_reads_are_free_without_the_anchors_too(
    case_id, command, without_the_trigger_anchors
):
    """The reads were free before and are free after -- the anchors never touch them.

    The counterfactual above shows the writes moved. This shows the reads did
    not, which is what makes the pair a measurement of the anchors' reach
    rather than of the classifier being switched off.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    [
        # gh's --help exemption only applies within the generic <=2
        # non-flag-token boundary (Step 3.5) -- `gh` is not in
        # HELP_ANY_POSITION_BASE_CMDS, so these omit the extra positional
        # (the workflow file / run id) that a real `--help` invocation to
        # learn a subcommand's usage would typically omit too.
        ("gh-workflow-run-help", "gh workflow run --help"),
        ("gh-run-rerun-help", "gh run rerun --help"),
        ("gh-run-cancel-help", "gh run cancel --help"),
        ("kubectl-run-help", "kubectl run debug-pod --image=alpine:3.20 --help"),
        ("kubectl-run-dry-run", "kubectl run debug-pod --image=alpine:3.20 --dry-run=client"),
    ],
    ids=[
        "gh-workflow-run-help",
        "gh-run-rerun-help",
        "gh-run-cancel-help",
        "kubectl-run-help",
        "kubectl-run-dry-run",
    ],
)
def test_indirect_trigger_or_live_workload_keeps_help_and_simulation_ahead(
    case_id, command
):
    """Help and simulation still outrank the anchor, as they did before.

    The anchor sits after both overrides on purpose; an entry that outranked
    them would make asking what a command does cost as much as running it.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) in (T0, T2)


def test_indirect_trigger_or_live_workload_carries_both_faces():
    """Both faces are present, and no command appears twice.

    A run of only face (a) passes while charging for every read; a run of
    only face (b) passes while leaving every trigger, retrigger, cancel, and
    live-workload creation open.
    """
    assert CLOSED and READS

    commands = [c for _, c in CLOSED + READS]
    assert len(commands) == len(set(commands)), "duplicate command in the table"

    ids = [i for i, _ in CLOSED + READS]
    assert len(ids) == len(set(ids)), "duplicate case id in the table"

    assert any(i.startswith("gh-") for i, _ in CLOSED)
    assert any(i.startswith("kubectl-") for i, _ in CLOSED)
    assert any(i.startswith("gh-") for i, _ in READS)
    assert any(i.startswith("kubectl-") for i, _ in READS)
