#!/usr/bin/env python3
"""The anchor seam can EXPRESS a command form; it declares no new ones here.

``COMMAND_PATH_MUTATIVE_UPGRADES`` is the one place where "this exact command
form does mutate" is declared, evaluated at Step 3e.5 of
``detect_mutative_command`` -- after the simulation-flag and ``--help``
overrides, and BEFORE the read-only-verb short-circuit of Step 4.

This suite measures the seam's REACH, not its contents. Every case installs a
synthetic anchor (``anchor-probe``, a command form that exists in no real CLI)
and asserts three things about it:

* it fires -- so the declaration is reachable and not dead config;
* it stops firing the moment the entry is withdrawn (the counterfactual) --
  so a passing assertion proves the ENTRY did the work, not some unrelated
  rule that happened to classify the same string;
* it never outranks the overrides above it, and never drags a sibling read
  form with it.

The probes are deliberately fictional. A probe borrowed from a real open gap
would make this suite pass for the wrong reason -- it would be measuring that
gap's closure instead of the seam's reach -- and closing gaps is separate work
with its own counterfactuals.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import tiers as tiers_module
from modules.security.mutative_verbs import (
    COMMAND_PATH_MUTATIVE_UPGRADES,
    MutativeAnchor,
    detect_mutative_command,
)
from modules.security.tiers import SecurityTier, classify_command_tier

T0 = SecurityTier.T0_READ_ONLY
T3 = SecurityTier.T3_BLOCKED

# A three-token subcommand path under a head that is itself a READ-ONLY verb
# (`config`). Depth alone would only exercise the widening; putting a read-only
# verb at the head also pins the seam's POSITION, since Step 4 would return
# READ_ONLY on `config` before ever reading the third token.
DEPTH3_ANCHOR = MutativeAnchor(path=("config", "widgets", "anchor-probe"))
DEPTH3_MUTATIVE = "gcloud config widgets anchor-probe my-widget"
DEPTH3_SIBLING_READ = "gcloud config widgets list"

# A condition carried by a FLAG rather than by a token, scoped to one CLI. The
# same flag on another family must stay inert -- that scoping is the whole
# point, and the property the prune-flag friction work depends on.
FLAG_ANCHOR = MutativeAnchor(path=("init",), flags=frozenset({"-anchor-probe"}))
FLAG_MUTATIVE = "terraform init -anchor-probe"
FLAG_SAME_PATH_WITHOUT_FLAG = "terraform init"
FLAG_OTHER_FAMILY = "kubectl get pods -anchor-probe"


def _clear_classifier_caches():
    """Drop every memoized verdict so a table edit is actually observed.

    Both entry points are ``lru_cache``d on the command string alone, so a
    verdict computed under one table state would otherwise survive the change
    and the counterfactual would silently measure nothing.
    """
    detect_mutative_command.cache_clear()
    tiers_module._classify_command_tier_cached.cache_clear()


def _install(monkeypatch, base_cmd, anchor):
    """Add one anchor to a CLI's tuple without dropping what it already has."""
    existing = tuple(COMMAND_PATH_MUTATIVE_UPGRADES.get(base_cmd, ()))
    monkeypatch.setitem(
        COMMAND_PATH_MUTATIVE_UPGRADES, base_cmd, existing + (anchor,)
    )
    _clear_classifier_caches()


@pytest.fixture(autouse=True)
def isolated_classifier_cache():
    _clear_classifier_caches()
    yield
    _clear_classifier_caches()


class TestDepthThreeSubcommandPath:
    """A three-token subcommand path can be anchored and is reached."""

    def test_depth_three_path_anchors_mutative(self, monkeypatch):
        _install(monkeypatch, "gcloud", DEPTH3_ANCHOR)
        result = detect_mutative_command(DEPTH3_MUTATIVE)
        assert result.is_mutative is True, (
            f"a depth-3 anchor must be reachable. "
            f"Got {result.category}: {result.reason}"
        )
        assert result.category == "MUTATIVE"
        assert result.verb == "anchor-probe"
        assert classify_command_tier(DEPTH3_MUTATIVE) == T3

    def test_counterfactual_without_the_entry(self):
        """Withdraw the entry and the same string returns to its old verdict."""
        result = detect_mutative_command(DEPTH3_MUTATIVE)
        assert result.is_mutative is False, (
            f"without its anchor the probe must classify exactly as it did "
            f"before -- otherwise the positive case proves nothing. "
            f"Got {result.category}: {result.reason}"
        )
        assert classify_command_tier(DEPTH3_MUTATIVE) == T0

    def test_sibling_read_form_of_the_same_group_stays_free(self, monkeypatch):
        _install(monkeypatch, "gcloud", DEPTH3_ANCHOR)
        result = detect_mutative_command(DEPTH3_SIBLING_READ)
        assert result.is_mutative is False, (
            f"the anchor is the exact path, not the group -- a read sibling "
            f"must not be dragged in. Got {result.category}: {result.reason}"
        )
        assert classify_command_tier(DEPTH3_SIBLING_READ) == T0

    def test_anchor_outranks_the_read_only_short_circuit(self, monkeypatch):
        """`config` is a READ_ONLY verb; the anchor still wins.

        This is the seam's position, not its shape: Step 4 would return
        READ_ONLY on the head token and never look further.
        """
        from modules.security.mutative_verbs import READ_ONLY_VERBS

        assert "config" in READ_ONLY_VERBS, (
            "the probe only pins the ordering while its head token is a "
            "read-only verb"
        )
        _install(monkeypatch, "gcloud", DEPTH3_ANCHOR)
        assert detect_mutative_command(DEPTH3_MUTATIVE).is_mutative is True


class TestFlagScopedCondition:
    """A flag can carry the condition, and its reach stops at its own CLI."""

    def test_flag_condition_anchors_within_its_family(self, monkeypatch):
        _install(monkeypatch, "terraform", FLAG_ANCHOR)
        result = detect_mutative_command(FLAG_MUTATIVE)
        assert result.is_mutative is True, (
            f"a flag-conditioned anchor must be reachable. "
            f"Got {result.category}: {result.reason}"
        )
        assert result.category == "MUTATIVE"
        assert "-anchor-probe" in result.dangerous_flags
        assert classify_command_tier(FLAG_MUTATIVE) == T3

    def test_same_path_without_the_flag_stays_free(self, monkeypatch):
        _install(monkeypatch, "terraform", FLAG_ANCHOR)
        result = detect_mutative_command(FLAG_SAME_PATH_WITHOUT_FLAG)
        assert result.is_mutative is False, (
            f"the flag IS the condition -- without it the same path must keep "
            f"its old verdict. Got {result.category}: {result.reason}"
        )
        assert classify_command_tier(FLAG_SAME_PATH_WITHOUT_FLAG) == T0

    def test_same_flag_on_another_family_is_inert(self, monkeypatch):
        """The inverse of a global ALWAYS flag: reach stops at the CLI."""
        _install(monkeypatch, "terraform", FLAG_ANCHOR)
        result = detect_mutative_command(FLAG_OTHER_FAMILY)
        assert result.is_mutative is False, (
            f"a family-scoped flag must not escalate another CLI -- that is "
            f"the global behaviour this seam exists to avoid. "
            f"Got {result.category}: {result.reason}"
        )
        assert classify_command_tier(FLAG_OTHER_FAMILY) == T0

    def test_counterfactual_without_the_entry(self):
        result = detect_mutative_command(FLAG_MUTATIVE)
        assert result.is_mutative is False, (
            f"without its anchor the flag probe must classify exactly as it "
            f"did before. Got {result.category}: {result.reason}"
        )
        assert classify_command_tier(FLAG_MUTATIVE) == T0


class TestPrecedenceIsPreserved:
    """Simulation and help are decided above the seam and still win."""

    @pytest.mark.parametrize(
        "base_cmd,anchor,command",
        [
            ("gcloud", DEPTH3_ANCHOR, DEPTH3_MUTATIVE + " --dry-run"),
            ("gcloud", DEPTH3_ANCHOR, DEPTH3_MUTATIVE + " --help"),
            ("terraform", FLAG_ANCHOR, FLAG_MUTATIVE + " --dry-run"),
            ("terraform", FLAG_ANCHOR, FLAG_MUTATIVE + " --help"),
        ],
        ids=["depth3-dry-run", "depth3-help", "flag-dry-run", "flag-help"],
    )
    def test_simulation_and_help_outrank_the_anchor(
        self, monkeypatch, base_cmd, anchor, command
    ):
        _install(monkeypatch, base_cmd, anchor)
        result = detect_mutative_command(command)
        assert result.is_mutative is False, (
            f"{command!r}: a simulation or help flag is resolved above the "
            f"anchor and must keep winning. "
            f"Got {result.category}: {result.reason}"
        )


class TestShippedTableDeclaresNoNewForms:
    """The seam widened; the data did not.

    Widening the seam and using it are separate pieces of work on purpose --
    this guard is what lets the widening be judged without its first use mixed
    in. It is expected to be edited (not deleted) by the work that closes the
    first real gap.
    """

    def test_only_the_project_cli_is_anchored(self):
        assert set(COMMAND_PATH_MUTATIVE_UPGRADES) == {"gaia"}

    def test_anchored_paths_are_the_previously_declared_ones(self):
        paths = {a.path for a in COMMAND_PATH_MUTATIVE_UPGRADES["gaia"]}
        assert paths == {
            ("dev",),
            ("context", "prune-workspaces"),
            ("scan",),
        }

    def test_no_shipped_anchor_carries_a_flag_condition(self):
        for anchors in COMMAND_PATH_MUTATIVE_UPGRADES.values():
            for anchor in anchors:
                assert anchor.flags == frozenset(), (
                    f"{anchor} declares a flag condition -- the widening "
                    f"ships the capability empty"
                )


class TestAnchorDeclarationIsValidated:
    """A malformed anchor is rejected at construction, not silently ignored.

    A dead entry is the failure mode this whole seam has to avoid: it reads as
    coverage and classifies nothing.
    """

    def test_empty_path_is_rejected(self):
        with pytest.raises(ValueError):
            MutativeAnchor(path=())

    def test_uppercase_path_token_is_rejected(self):
        with pytest.raises(ValueError):
            MutativeAnchor(path=("Dev",))

    def test_uppercase_flag_is_rejected(self):
        with pytest.raises(ValueError):
            MutativeAnchor(path=("init",), flags=frozenset({"-Upgrade"}))
