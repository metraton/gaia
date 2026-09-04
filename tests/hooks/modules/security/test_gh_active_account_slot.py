#!/usr/bin/env python3
"""Disturbing gh's one active-account slot stops passing free through the
classifier.

``gh`` keeps ONE active account per host. ``gh auth switch`` rewrites that slot
and ``gh auth logout`` empties it, so both reach outside their own invocation
and change what every other concurrent session and agent on the machine
resolves to. Neither ``switch`` nor ``logout`` carries a verb in
``MUTATIVE_VERBS``, so both fell through Step 4 of ``detect_mutative_command``
to READ_ONLY "by elimination" -- the audit log shows ``gh auth switch``
running with no friction at all.

The read surface of the same subcommand group must keep costing nothing, and
one WRITE form is deliberately left free with it: ``gh auth login`` ADDS an
account without displacing the active one, and it is the only way out when the
account a command needs is absent. Gating it would leave an agent blocked on
``switch`` with no route to the account it was told to use.

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
    ("gh-auth-switch-named", "gh auth switch -u metraton"),
    ("gh-auth-switch-bare", "gh auth switch"),
    ("gh-auth-logout-hostname", "gh auth logout --hostname github.com"),
    ("gh-auth-logout-bare", "gh auth logout"),
]

# The exact anchors this work adds. Used only to withdraw precisely these for
# the counterfactual, leaving anything a sibling task anchored under `gh`.
_ANCHORED_PATHS_BY_BASE_CMD = {
    "gh": {("auth", "switch"), ("auth", "logout")},
}

# --- Face (b): reading the slot, and ADDING to it, stay free ----------------
FREE = [
    ("gh-auth-status", "gh auth status"),
    ("gh-auth-token-named", "gh auth token --user metraton"),
    ("gh-auth-login", "gh auth login --hostname github.com"),
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
def without_the_account_slot_anchors(monkeypatch):
    """Withdraw exactly the anchors this work adds, caches cleared on both edges."""
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
def test_disturbing_the_active_account_slot_is_mutative_and_t3(case_id, command):
    """Face (a): rewriting or emptying the shared account slot requires consent."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{case_id}: must be mutative -- got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{case_id}: must require consent -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


@pytest.mark.parametrize("case_id,command", FREE, ids=[c for c, _ in FREE])
def test_reading_the_slot_and_adding_an_account_stay_free(case_id, command):
    """Face (b): status, token, and login keep costing nothing.

    This is the half that separates a repair from an overcorrection. `login`
    is the deliberate one: it is a write that does not displace the active
    account, and it is the escape hatch when the needed account is absent.
    """
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: must not start demanding consent -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0, (
        f"{case_id}: must stay T0 -- got {classify_command_tier(command)} "
        f"for {command!r}"
    )


@pytest.mark.parametrize("case_id,command", CLOSED, ids=[c for c, _ in CLOSED])
def test_account_slot_counterfactual_without_the_anchor(
    case_id, command, without_the_account_slot_anchors
):
    """Every closed form returns to READ_ONLY once its anchor is withdrawn.

    A present entry is not a firing one -- this proves the entry is what moved
    the verdict, not some unrelated rule that happened to agree with it.
    """
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: with the anchor withdrawn this form must classify exactly "
        f"as it did before this work, or the positive case proves nothing -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize("case_id,command", FREE, ids=[c for c, _ in FREE])
def test_free_forms_are_free_without_the_anchors_too(
    case_id, command, without_the_account_slot_anchors
):
    """The free forms were free before and are free after.

    The counterfactual above shows the writes moved. This shows the reads did
    not, which is what makes the pair a measurement of the anchors' reach
    rather than of the classifier being switched off.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize("case_id,command", CLOSED, ids=[c for c, _ in CLOSED])
def test_denial_names_the_per_process_alternative(case_id, command):
    """The denial carries the way to reach the same outcome without the mutation.

    A gate that only says "no" to a command with a safe equivalent leaves the
    agent hunting for a spelling that passes, which is the behaviour the
    no-elusion rule exists to prevent.
    """
    result = detect_mutative_command(command)
    assert 'GH_TOKEN="$(gh auth token --user <account>)"' in result.guidance, (
        f"{case_id}: guidance must name the per-process form -- "
        f"got {result.guidance!r}"
    )
    assert "ghx" in result.guidance
    assert result.guidance in result.reason, (
        f"{case_id}: the reason the classifier reports must carry the guidance, "
        f"or callers that surface only the reason drop it -- got {result.reason!r}"
    )


def test_account_slot_gate_carries_both_faces():
    """Both faces are present, and no command appears twice.

    A run of only face (a) passes while charging for every read; a run of only
    face (b) passes while leaving the shared slot open to every agent.
    """
    assert CLOSED and FREE

    commands = [c for _, c in CLOSED + FREE]
    assert len(commands) == len(set(commands)), "duplicate command in the table"

    ids = [i for i, _ in CLOSED + FREE]
    assert len(ids) == len(set(ids)), "duplicate case id in the table"

    assert any("switch" in c for _, c in CLOSED)
    assert any("logout" in c for _, c in CLOSED)
    assert any("login" in c for _, c in FREE)
