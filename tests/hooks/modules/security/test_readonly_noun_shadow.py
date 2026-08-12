#!/usr/bin/env python3
"""A read-only noun stops deciding alone when the verb behind it writes.

``config`` sits in ``READ_ONLY_VERBS``, and the Step 4 scan returns on it. So
``gcloud config set project other-project`` -- which redirects every later
command onto a different project -- returned READ_ONLY without the scan ever
reaching ``set``. Measured directly: withdraw ``config`` from the read-only
table and the same command comes back MUTATIVE on ``set``. Fifteen forms across
four CLIs were held open that way, all of them writes to the CLI's own
configuration: which project and identity gcloud acts as, which cluster and
namespace kubectl reaches, which registry npm downloads from.

``git config`` is the sibling by EFFECT and not by mechanism, and the
distinction is the reason it takes a different repair. It carries no verb at
all -- withdrawing the noun leaves it at T0 just the same -- so it arrives at
READ_ONLY by elimination rather than by the shadow. It also cannot be an anchor:
the anchor seam matches a subcommand path plus the PRESENCE of a flag, while
git's write form is the ABSENCE of a read flag together with a key AND a value
(``git config user.email`` reads, ``git config user.email x`` writes, same
path). It gets a discriminator, exactly as ``git tag`` did.

The two faces are one table on purpose. ``config`` heads the read forms this
user runs dozens of times a day, so the failure this work is most likely to
produce is not leaving a write open -- it is charging for a read. A table with
only the writes cannot tell a repair from an overcorrection.

Every gated form carries its counterfactual: withdraw the entry, drop the
memoized verdicts, and it must return to what it classified before. This
repository has shipped a whole table that read as coverage and decided nothing;
a present entry is not a firing one.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import mutative_verbs as mutative_verbs_module
from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import (
    COMMAND_PATH_MUTATIVE_UPGRADES,
    READ_ONLY_VERBS,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T2 = SecurityTier.T2_DRY_RUN
T3 = SecurityTier.T3_BLOCKED

# The noun whose short-circuit this work defeats. Named here because the repair
# must NOT be its removal from the global table -- see the invariant below.
SHADOWING_NOUN = "config"

# --- Face (a): configuration writes the shadow was covering -----------------
# Carried by an anchor in COMMAND_PATH_MUTATIVE_UPGRADES, per CLI.
ANCHORED_WRITES = [
    ("gcloud-redirect-project", "gcloud config set project other-project"),
    ("gcloud-redirect-account", "gcloud config set account someone@else.com"),
    ("gcloud-set-region", "gcloud config set compute/region us-central1"),
    ("gcloud-configurations-create", "gcloud config configurations create foo"),
    ("gcloud-configurations-delete", "gcloud config configurations delete foo"),
    ("kubectl-set", "kubectl config set clusters.foo.server https://1.2.3.4"),
    ("kubectl-set-cluster", "kubectl config set-cluster prod --server=https://1.2.3.4"),
    ("kubectl-set-context", "kubectl config set-context prod --namespace=prod"),
    ("kubectl-set-credentials", "kubectl config set-credentials admin --token=abc"),
    ("kubectl-delete-cluster", "kubectl config delete-cluster prod"),
    ("kubectl-delete-context", "kubectl config delete-context prod"),
    ("kubectl-delete-user", "kubectl config delete-user admin"),
    ("kubectl-rename-context", "kubectl config rename-context old new"),
    ("gh-set", "gh config set editor vim"),
    ("npm-set-registry", "npm config set registry https://example.invalid"),
    ("npm-delete", "npm config delete registry"),
    ("npm-edit", "npm config edit"),
]

# --- Face (a), git half: carried by the discriminator, not by an anchor -----
GIT_WRITES = [
    ("git-assign", "git config user.email someone@else.com"),
    ("git-assign-global", "git config --global user.email someone@else.com"),
    ("git-add", "git config --add remote.origin.fetch +refs/heads/x"),
    ("git-unset", "git config --unset user.email"),
    ("git-unset-all", "git config --unset-all user.email"),
    ("git-replace-all", "git config --replace-all user.email a@b.c"),
    ("git-remove-section", "git config --remove-section remote.old"),
    ("git-rename-section", "git config --rename-section remote.old remote.new"),
    ("git-edit", "git config --edit"),
]

ALL_WRITES = ANCHORED_WRITES + GIT_WRITES

# --- Face (b): reads of the SAME noun, which must keep costing nothing ------
# Not optional. Without it the suite cannot tell this work from an
# overcorrection that starts charging a toll on the most common read there is.
READS = [
    ("gcloud-list", "gcloud config list"),
    ("gcloud-get-value", "gcloud config get-value project"),
    ("gcloud-get", "gcloud config get project"),
    ("gcloud-describe", "gcloud config describe"),
    ("gcloud-configurations-list", "gcloud config configurations list"),
    ("gcloud-configurations-describe", "gcloud config configurations describe default"),
    ("kubectl-view", "kubectl config view"),
    ("kubectl-current-context", "kubectl config current-context"),
    ("kubectl-get-contexts", "kubectl config get-contexts"),
    ("kubectl-get-clusters", "kubectl config get-clusters"),
    ("kubectl-get-users", "kubectl config get-users"),
    ("gh-get", "gh config get editor"),
    ("gh-list", "gh config list"),
    ("npm-get", "npm config get registry"),
    ("npm-list", "npm config list"),
    ("git-bare", "git config"),
    ("git-list", "git config --list"),
    ("git-list-global", "git config --global --list"),
    ("git-get", "git config --get user.email"),
    ("git-get-all", "git config --get-all remote.origin.fetch"),
    ("git-get-regexp", "git config --get-regexp ^remote"),
    ("git-read-key", "git config user.email"),
]

# --- Declared and left open: the shadow covers no mutative verb here --------
# ``use`` and ``activate`` are absent from the verb taxonomy, so withdrawing the
# noun does not move these -- the shadow is not what holds them at T0. They
# redirect as hard as anything in face (a), which is why they are recorded here
# rather than left unmentioned, and they need a decision of their own.
STILL_OPEN = [
    ("gcloud-configurations-activate", "gcloud config configurations activate other"),
    ("kubectl-use-context", "kubectl config use-context prod"),
    ("gcloud-unset", "gcloud config unset project"),
    ("kubectl-unset", "kubectl config unset current-context"),
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
def without_the_config_anchors(monkeypatch):
    """Withdraw exactly the anchors this work adds, caches cleared on both edges.

    Anchors whose path starts with another token are left in place, so a CLI
    that was already anchored for unrelated reasons keeps those verdicts and
    the counterfactual measures this entry rather than the fixture.
    """
    for base_cmd, anchors in list(COMMAND_PATH_MUTATIVE_UPGRADES.items()):
        survivors = tuple(a for a in anchors if a.path[0] != SHADOWING_NOUN)
        if len(survivors) == len(anchors):
            continue
        if survivors:
            monkeypatch.setitem(COMMAND_PATH_MUTATIVE_UPGRADES, base_cmd, survivors)
        else:
            monkeypatch.delitem(COMMAND_PATH_MUTATIVE_UPGRADES, base_cmd)
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.fixture
def without_the_git_config_lane(monkeypatch):
    """Neutralize the git discriminator by making it stand aside on every form."""
    monkeypatch.setattr(
        mutative_verbs_module,
        "_check_git_config",
        lambda semantics, tokens, family: None,
    )
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.mark.parametrize(
    "case_id,command", ALL_WRITES, ids=[c for c, _ in ALL_WRITES]
)
def test_readonly_noun_shadow_write_forms_are_mutative_and_t3(case_id, command):
    """Face (a): writing a CLI's configuration requires consent."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{case_id}: a configuration write must be mutative -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{case_id}: a configuration write must require consent -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


@pytest.mark.parametrize("case_id,command", READS, ids=[c for c, _ in READS])
def test_readonly_noun_shadow_read_forms_stay_free(case_id, command):
    """Face (b): reading the same noun keeps costing nothing.

    This is the half that separates a repair from an overcorrection. A rule
    widened past the write forms turns these red, and that is the point.
    """
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: reading configuration must not start demanding consent -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0, (
        f"{case_id}: reading configuration must stay T0 -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


@pytest.mark.parametrize(
    "case_id,command", ANCHORED_WRITES, ids=[c for c, _ in ANCHORED_WRITES]
)
def test_readonly_noun_shadow_counterfactual_without_the_anchor(
    case_id, command, without_the_config_anchors
):
    """Every anchored form returns to READ_ONLY once its entry is withdrawn."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: with the anchor withdrawn this form must classify exactly "
        f"as it did before this work, or the positive case proves nothing -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize("case_id,command", GIT_WRITES, ids=[c for c, _ in GIT_WRITES])
def test_readonly_noun_shadow_counterfactual_without_the_git_lane(
    case_id, command, without_the_git_config_lane
):
    """Every git form returns to READ_ONLY once the discriminator stands aside."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: with the discriminator neutralized this form must classify "
        f"exactly as it did before this work -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize("case_id,command", READS, ids=[c for c, _ in READS])
def test_readonly_noun_shadow_reads_are_free_without_the_new_entries_too(
    case_id, command, without_the_config_anchors, without_the_git_config_lane
):
    """The reads were free before and are free after -- the entries never touch them.

    The counterfactual above shows the writes MOVED. This shows the reads did
    not, which is what makes the pair a measurement of the entry's reach rather
    than of the classifier being switched off.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) == T0


def test_readonly_noun_shadow_leaves_the_global_read_only_table_alone():
    """The repair is anchored per CLI, not bought by widening a global table.

    Dropping ``config`` from ``READ_ONLY_VERBS`` would gate these writes too --
    and would charge every other CLI that spells a read with the same noun.
    """
    assert SHADOWING_NOUN in READ_ONLY_VERBS


@pytest.mark.parametrize(
    "case_id,command", STILL_OPEN, ids=[c for c, _ in STILL_OPEN]
)
def test_readonly_noun_shadow_declared_open_forms_are_recorded_not_closed(
    case_id, command
):
    """Forms this work deliberately did not close, recorded with today's verdict.

    Each redirects or clears real configuration, and none is reached by the
    shadow: the token behind the noun is not a mutative verb, so removing the
    short-circuit would not move them. Recorded so closing one later is a
    deliberate edit here rather than an invisible change.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    [
        ("gcloud-help", "gcloud config set project other-project --help"),
        ("gcloud-dry-run", "gcloud config set project other-project --dry-run"),
    ],
    ids=["help", "dry-run"],
)
def test_readonly_noun_shadow_keeps_help_and_simulation_ahead(case_id, command):
    """Help and simulation still outrank the anchor, as they did before.

    The anchor sits after both overrides on purpose; an entry that outranked
    them would make asking what a command does cost as much as running it.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) in (T0, T2)


def test_readonly_noun_shadow_carries_both_faces():
    """Both faces are present, and no command appears twice.

    A run of only face (a) passes while charging for every read; a run of only
    face (b) passes while leaving every write open.
    """
    assert ALL_WRITES and READS

    commands = [c for _, c in ALL_WRITES + READS + STILL_OPEN]
    assert len(commands) == len(set(commands)), "duplicate command in the table"

    ids = [i for i, _ in ALL_WRITES + READS + STILL_OPEN]
    assert len(ids) == len(set(ids)), "duplicate case id in the table"

    # Both CLIs the gate names carry both faces, not one face each.
    for prefix in ("gcloud", "git"):
        assert any(i.startswith(prefix) for i, _ in ALL_WRITES)
        assert any(i.startswith(prefix) for i, _ in READS)
