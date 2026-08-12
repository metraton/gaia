#!/usr/bin/env python3
"""Touching state, standing up a new remote, or writing a sensitive path
directly stops passing free through the classifier.

Three unrelated surfaces share one property: none of them carries a verb in
``MUTATIVE_VERBS``, so every one of them fell through detection to READ_ONLY
"by elimination" (or, for ``tee``, was never classified at all -- it sits in
no table anywhere in the module).

- ``terraform init`` / ``terragrunt init`` re-bootstrap a workspace that
  already has provider plugins and remote state, and bare re-init is
  idempotent -- it must stay free. Three flags turn the same subcommand into
  a state-mutating operation: ``-upgrade`` rewrites installed provider
  versions, ``-migrate-state`` moves the backend's state into a new
  configuration, and ``-reconfigure`` discards the existing backend config
  and re-initializes it. ``init`` names no lifecycle action the verb
  taxonomy tracks, on either infra CLI this repository observed in use, so
  all six forms (three flags x two CLIs) classified READ_ONLY by
  elimination alongside the harmless bare form.
- ``git remote add`` stands up a NEW remote destination the repository will
  push to and fetch from thereafter -- the same "grants a new capability"
  shape gated on the IAM surfaces, reached through the same door: ``add`` is
  deliberately absent from MUTATIVE_VERBS (``git add`` must stay free), so
  the whole command classified READ_ONLY by elimination too. Two sibling
  forms are deliberately NOT re-anchored here because they already gate
  today: ``git worktree add`` (its own family classifier) and ``git remote
  set-url`` (repointing an EXISTING destination, caught by the generic verb
  scan on "set").
- ``tee`` writes stdin verbatim onto every file argument it is given, and
  carried no classification at all -- not a verb, not a command alias, not a
  script-file lane. A direct write onto a privileged OS directory or onto
  Gaia's own live hooks tree is what makes a ``tee`` target sensitive; every
  other destination, including the Gaia scratch directory, stays free. This
  is decided by the DESTINATION, not by the tool -- prohibiting ``tee``
  outright would charge the user for writing where they already have every
  right to.

So the repair anchors the first two families in COMMAND_PATH_MUTATIVE_UPGRADES
by exact (family, subcommand)/(family, flag) -- the mechanism built in the M2
PREVIA task -- and decides the third with a discriminator, ``_check_tee_write``.
None widens a verb globally.

WHY A DISCRIMINATOR AND NOT A COMMAND-ALIAS ENTRY. ``tee`` was first anchored by
adding it to COMMAND_ALIASES -- a GLOBAL table keyed by TOOL NAME -- and
subtracting the safe destinations back out. That inverts the default: with no
file argument there is nothing to subtract, so the bare form stayed gated. But
an absent file argument is not an unanalyzable write, it is the whole of the
READ idiom: ``cmd | tee`` copies stdin to stdout and writes nothing, and since
the validator splits a pipeline on its operators and classifies each component,
the bare ``tee`` component denied entire read-only pipelines. The discriminator
stands aside by default and only ever ADDS a verdict, so the passthrough is free
in a pipeline and loose, with flags and without, while the sensitive write it
was built to catch is unchanged.

Every closed form carries its counterfactual: withdraw the anchor (or, for
``tee``, make the discriminator stand aside), drop the memoized verdicts, and
the form must return to exactly what it classified before this work. A present
entry is not a firing one -- this repository has shipped a whole table that read
as coverage and decided nothing.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))  # for the `gaia` package (resolver)

from modules.security import mutative_verbs as mutative_verbs_module
from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import (
    COMMAND_PATH_MUTATIVE_UPGRADES,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier
from modules.tools.shell_parser import ShellCommandParser

T0 = SecurityTier.T0_READ_ONLY
T2 = SecurityTier.T2_DRY_RUN
T3 = SecurityTier.T3_BLOCKED

# The path a direct tee write targets to prove the sensitive-path predicate --
# Gaia's OWN live hooks entrypoint, resolved from this test file's own
# location (portable across machines/CI, unlike a hardcoded absolute path).
_HOOKS_SENSITIVE_TARGET = str(HOOKS_DIR / "pre_tool_use.py")

# --- Face (a): the forms that now require consent ---------------------------
CLOSED = [
    ("terraform-init-upgrade", "terraform init -upgrade"),
    ("terraform-init-migrate-state", "terraform init -migrate-state"),
    ("terraform-init-reconfigure", "terraform init -reconfigure"),
    ("terragrunt-init-upgrade", "terragrunt init -upgrade"),
    ("terragrunt-init-migrate-state", "terragrunt init -migrate-state"),
    ("terragrunt-init-reconfigure", "terragrunt init -reconfigure"),
    (
        "git-remote-add",
        "git remote add upstream git@github.com:other/repo.git",
    ),
    ("tee-sensitive-write", f"tee {_HOOKS_SENSITIVE_TARGET}"),
    ("tee-privileged-path-write", "tee /etc/hosts"),
]

# The exact anchors this work adds, keyed by base command -- used only to
# withdraw precisely these entries for the counterfactual, and nothing a
# sibling task anchored under the same base command.
_ANCHORED_PATHS_BY_BASE_CMD = {
    "terraform": {("init",)},
    "terragrunt": {("init",)},
    "git": {("remote", "add")},
}

# --- Face (b): bare init, listing remotes, and ordinary writes stay free ----
FREE = [
    ("terraform-init-bare", "terraform init"),
    ("terragrunt-init-bare", "terragrunt init"),
    ("git-remote-list", "git remote -v"),
    ("tee-relative-write", "tee output.log"),
    ("tee-tmp-write", "tee /tmp/probe.txt"),
    # The passthrough that writes nothing, in the SAME table as the writes
    # that escalate -- a table holding only the escalating forms cannot tell a
    # discriminator from a ban on the tool.
    ("tee-passthrough-no-file", "tee"),
    ("tee-passthrough-append-flag", "tee -a"),
    ("tee-passthrough-end-of-options", "tee --"),
]

# Read-only pipelines whose LAST component is the bare passthrough. The
# compound path in bash_validator classifies each component on its own, so a
# component that classifies mutative denies the whole chain -- which is how
# the passthrough sank these two.
PASSTHROUGH_PIPELINES = [
    ("pipeline-ls", "ls -la | tee"),
    ("pipeline-kubectl", "kubectl get pods | tee"),
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
def without_the_state_destination_anchors(monkeypatch):
    """Withdraw exactly the anchors this work adds, caches cleared on both edges.

    Filters by the exact path this work declared, so an anchor a sibling task
    placed under the same base command survives and the counterfactual
    measures this entry, not the fixture.
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


@pytest.fixture
def without_the_tee_discriminator(monkeypatch):
    """Make ``_check_tee_write`` stand aside unconditionally.

    The discriminator's whole contract is that standing aside leaves the
    verdict untouched, so neutralising it is the exact counterfactual: what it
    escalates must fall back to what the classifier said before it existed.
    """
    monkeypatch.setattr(
        mutative_verbs_module, "_check_tee_write", lambda base_cmd, tokens: None
    )
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


@pytest.mark.parametrize("case_id,command", CLOSED, ids=[c for c, _ in CLOSED])
def test_state_destination_or_direct_write_closed_forms_are_mutative_and_t3(
    case_id, command
):
    """Face (a): migrating/reconfiguring infra state, standing up a new
    remote, and writing directly onto a sensitive path require consent."""
    result = detect_mutative_command(command)
    assert result.is_mutative is True, (
        f"{case_id}: must be mutative -- got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T3, (
        f"{case_id}: must require consent -- "
        f"got {classify_command_tier(command)} for {command!r}"
    )


@pytest.mark.parametrize("case_id,command", FREE, ids=[c for c, _ in FREE])
def test_state_destination_or_direct_write_free_forms_stay_free(case_id, command):
    """Face (b): bare init, listing remotes, and ordinary writes keep costing
    nothing.

    This is the half that separates a repair from an overcorrection.
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


@pytest.mark.parametrize(
    "case_id,command",
    [(i, c) for i, c in CLOSED if not i.startswith("tee-")],
    ids=[i for i, _ in CLOSED if not i.startswith("tee-")],
)
def test_state_destination_counterfactual_without_the_anchor(
    case_id, command, without_the_state_destination_anchors
):
    """Every anchored form (terraform/terragrunt/git) returns to READ_ONLY
    once its anchor is withdrawn.

    A present entry is not a firing one -- this proves the entry is what
    moved the verdict, not some unrelated rule that happened to agree with
    it. Excludes the tee cases, which are decided a different way (see below).
    """
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: with the anchor withdrawn this form must classify "
        f"exactly as it did before this work, or the positive case proves "
        f"nothing -- got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    [(i, c) for i, c in CLOSED if i.startswith("tee-")],
    ids=[i for i, _ in CLOSED if i.startswith("tee-")],
)
def test_direct_write_counterfactual_without_the_tee_discriminator(
    case_id, command, without_the_tee_discriminator
):
    """Every tee sensitive-write case returns to READ_ONLY once the
    discriminator stands aside -- proving the classification depends on it,
    not on some unrelated rule that happened to agree."""
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"{case_id}: with the tee discriminator standing aside this form must "
        f"classify exactly as it did before this work -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    PASSTHROUGH_PIPELINES,
    ids=[c for c, _ in PASSTHROUGH_PIPELINES],
)
def test_read_only_pipeline_through_the_passthrough_stays_free(case_id, command):
    """A read-only pipeline ending in the bare passthrough costs nothing --
    measured the way the validator measures it, component by component.

    Asserting only the joined string would miss the regression entirely: the
    joined string never classified mutative. What denied these pipelines was
    the trailing ``tee`` component, judged on its own by the compound path.
    """
    for index, component in enumerate(ShellCommandParser().parse(command), 1):
        result = detect_mutative_command(component)
        assert result.is_mutative is False, (
            f"{case_id}: component {index} {component!r} makes the whole "
            f"pipeline cost consent -- got {result.category}: {result.reason}"
        )
        assert classify_command_tier(component) == T0


@pytest.mark.parametrize("case_id,command", FREE, ids=[c for c, _ in FREE])
def test_state_destination_or_direct_write_free_forms_stay_free_without_the_anchors_too(
    case_id, command, without_the_state_destination_anchors
):
    """The free forms were free before and are free after -- the anchors
    never touch them.

    The counterfactual above shows the writes moved. This shows the free
    forms did not, which is what makes the pair a measurement of the
    anchors' reach rather than of the classifier being switched off.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) == T0


@pytest.mark.parametrize(
    "case_id,command",
    [
        ("terraform-upgrade-help", "terraform init -upgrade --help"),
        ("terraform-upgrade-dry-run", "terraform init -upgrade --dry-run"),
        ("terragrunt-upgrade-help", "terragrunt init -upgrade --help"),
        ("terragrunt-upgrade-dry-run", "terragrunt init -upgrade --dry-run"),
        (
            "git-remote-add-dry-run",
            "git remote add upstream git@github.com:other/repo.git --dry-run",
        ),
    ],
    ids=[
        "terraform-upgrade-help",
        "terraform-upgrade-dry-run",
        "terragrunt-upgrade-help",
        "terragrunt-upgrade-dry-run",
        "git-remote-add-dry-run",
    ],
)
def test_state_destination_keeps_help_and_simulation_ahead(case_id, command):
    """Help and simulation still outrank the anchor, as they did before.

    The anchor sits after both overrides on purpose; an entry that outranked
    them would make asking what a command does cost as much as running it.
    ``git remote add ... --help`` is not tested here: with four positional
    tokens it already exceeds the generic <=2 --help boundary, a pre-existing
    property of git's own help exemption unrelated to this anchor.
    """
    assert detect_mutative_command(command).is_mutative is False
    assert classify_command_tier(command) in (T0, T2)


def test_direct_write_into_gaia_scratch_stays_free(monkeypatch, tmp_path):
    """A tee write confined to the Gaia scratch directory stays T0.

    Isolated via GAIA_DATA_DIR (mirrors test_rm_scratch_exception.py) rather
    than a literal path to the real ``~/.gaia/scratch``, so the case does not
    depend on that directory existing or being writable in CI. This is the
    explicit exception the plan calls out by name: writing in scratch stays
    free, following the precedent of the existing rm-scratch and
    mkdir-sensitive-path exceptions.
    """
    data = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    from gaia.paths import ensure_layout, scratch_dir
    ensure_layout()
    target = scratch_dir() / "probe.txt"
    _clear_classifier_caches()

    command = f"tee {target}"
    result = detect_mutative_command(command)
    assert result.is_mutative is False, (
        f"a tee write confined to Gaia scratch must stay free -- "
        f"got {result.category}: {result.reason}"
    )
    assert classify_command_tier(command) == T0


def test_state_destination_or_direct_write_carries_both_faces():
    """Both faces are present, and no command appears twice.

    A run of only face (a) passes while charging for every ordinary write; a
    run of only face (b) passes while leaving every state-mutating init, new
    remote, and sensitive write open.
    """
    assert CLOSED and FREE

    commands = [c for _, c in CLOSED + FREE]
    assert len(commands) == len(set(commands)), "duplicate command in the table"

    ids = [i for i, _ in CLOSED + FREE]
    assert len(ids) == len(set(ids)), "duplicate case id in the table"

    assert any(i.startswith("terraform-") for i, _ in CLOSED)
    assert any(i.startswith("terragrunt-") for i, _ in CLOSED)
    assert any(i.startswith("git-") for i, _ in CLOSED)
    assert any(i.startswith("tee-") for i, _ in CLOSED)
    assert any(i.startswith("terraform-") for i, _ in FREE)
    assert any(i.startswith("terragrunt-") for i, _ in FREE)
    assert any(i.startswith("git-") for i, _ in FREE)
    assert any(i.startswith("tee-") for i, _ in FREE)
