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
    _validated_anchor_table,
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
    """Add one anchor to a CLI's tuple without dropping what it already has.

    The composed tuple goes through the same declaration guard the shipped
    table does, so a probe that could never fire is refused here too instead
    of quietly measuring nothing.
    """
    existing = tuple(COMMAND_PATH_MUTATIVE_UPGRADES.get(base_cmd, ()))
    composed = _validated_anchor_table({base_cmd: existing + (anchor,)})
    monkeypatch.setitem(
        COMMAND_PATH_MUTATIVE_UPGRADES, base_cmd, composed[base_cmd]
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


class TestShippedTableDeclaresExactlyWhatWasReviewed:
    """The shipped anchors, enumerated, so a new one cannot arrive unnoticed.

    An anchor moves a command form to T3 for every agent and every session, so
    the set of them is reviewed as data rather than inferred from whichever
    tests happen to exercise it. Each entry's own justification lives beside
    its declaration in ``COMMAND_PATH_MUTATIVE_UPGRADES``; this guard only
    pins the inventory, and is expected to be edited by the work that adds to
    it -- the edit is the review.
    """

    def test_the_anchored_clis_are_the_reviewed_ones(self):
        assert set(COMMAND_PATH_MUTATIVE_UPGRADES) == {
            "gaia",
            "gcloud",
            "kubectl",
            "gh",
            "npm",
            "git",
            "terraform",
            "terragrunt",
        }

    def test_project_cli_paths_are_the_previously_declared_ones(self):
        paths = {a.path for a in COMMAND_PATH_MUTATIVE_UPGRADES["gaia"]}
        assert paths == {
            ("dev",),
            ("context", "prune-workspaces"),
            ("scan",),
        }

    def test_cloud_cli_paths_are_the_iam_binding_forms(self):
        """Both directions of an IAM binding change, on the surfaces that were open.

        The two-token removals (`projects`, `secrets`) are absent because the
        verb scan already decides them; the three-token ones are here because
        the hyphen split never reaches that depth.
        """
        paths = {
            a.path
            for a in COMMAND_PATH_MUTATIVE_UPGRADES["gcloud"]
            if a.path[0] != "config"
        }
        assert paths == {
            ("projects", "add-iam-policy-binding"),
            ("secrets", "add-iam-policy-binding"),
            ("storage", "buckets", "add-iam-policy-binding"),
            ("storage", "buckets", "remove-iam-policy-binding"),
            ("iam", "service-accounts", "add-iam-policy-binding"),
            ("iam", "service-accounts", "remove-iam-policy-binding"),
        }

    def test_configuration_write_paths_are_the_reviewed_ones(self):
        """Writes to a CLI's own configuration, which a read-only noun shadowed.

        `config` is a READ_ONLY_VERBS entry and the verb scan returns on it, so
        the verb behind it was never read. These paths are the write forms that
        held open; the read forms of the same noun carry no anchor and are what
        the surrounding suite checks stayed free.
        """
        paths = {
            (base_cmd,) + anchor.path
            for base_cmd, anchors in COMMAND_PATH_MUTATIVE_UPGRADES.items()
            for anchor in anchors
            if anchor.path[0] == "config"
        }
        assert paths == {
            ("gcloud", "config", "set"),
            ("gcloud", "config", "configurations", "create"),
            ("gcloud", "config", "configurations", "delete"),
            ("kubectl", "config", "set"),
            ("kubectl", "config", "set-cluster"),
            ("kubectl", "config", "set-context"),
            ("kubectl", "config", "set-credentials"),
            ("kubectl", "config", "delete-cluster"),
            ("kubectl", "config", "delete-context"),
            ("kubectl", "config", "delete-user"),
            ("kubectl", "config", "rename-context"),
            ("gh", "config", "set"),
            ("npm", "config", "set"),
            ("npm", "config", "delete"),
            ("npm", "config", "edit"),
        }

    def test_indirect_trigger_and_live_workload_paths_are_the_reviewed_ones(self):
        """Remote triggers, a re-trigger, a cancel, and a live workload create.

        None of these carries a verb in MUTATIVE_VERBS -- ``run`` is
        deliberately excluded ("safe by elimination"), and ``rerun``/``cancel``
        were never in the taxonomy -- so all four fell through to Step 4 and
        classified READ_ONLY by elimination.
        """
        paths = {
            (base_cmd,) + anchor.path
            for base_cmd, anchors in COMMAND_PATH_MUTATIVE_UPGRADES.items()
            for anchor in anchors
            if base_cmd == "gh" and anchor.path[0] in ("workflow", "run")
            or base_cmd == "kubectl" and anchor.path[0] == "run"
        }
        assert paths == {
            ("gh", "workflow", "run"),
            ("gh", "run", "rerun"),
            ("gh", "run", "cancel"),
            ("kubectl", "run"),
        }

    def test_state_destination_paths_are_the_reviewed_ones(self):
        """A new remote destination, and infra init that migrates/reconfigures state.

        ``git remote add`` carries no verb in MUTATIVE_VERBS (``add`` is
        deliberately excluded) so it fell through to Step 4 and classified
        READ_ONLY by elimination -- the two forms already gated (worktree
        creation, and repointing an EXISTING destination via ``set-url``) are
        deliberately NOT re-anchored here. ``terraform``/``terragrunt init``
        carry no verb either; the condition is carried by the flags that
        actually mutate state (module upgrade, state migration,
        reconfiguration), not the bare subcommand, so a plain ``init`` stays
        free on both CLIs this repository observed.
        """
        paths = {
            (base_cmd,) + anchor.path
            for base_cmd, anchors in COMMAND_PATH_MUTATIVE_UPGRADES.items()
            for anchor in anchors
            if base_cmd in ("git", "terraform", "terragrunt")
        }
        assert paths == {
            ("git", "remote", "add"),
            ("terraform", "init"),
            ("terragrunt", "init"),
        }

    def test_only_the_reviewed_shipped_anchors_carry_a_flag_condition(self):
        """The flag half of the seam has three shipped users.

        ``kubectl run`` -- a bare invocation names no form that actually
        starts a workload, so the condition is carried by the flag that does:
        an explicit image. ``terraform``/``terragrunt init`` -- a bare
        invocation is idempotent bootstrapping, so the condition is carried
        by the three flags that actually mutate state. Every other shipped
        anchor still decides by path alone. A new flagged entry arriving here
        is a review point same as any other addition.
        """
        flagged = {
            (base_cmd,) + anchor.path: anchor.flags
            for base_cmd, anchors in COMMAND_PATH_MUTATIVE_UPGRADES.items()
            for anchor in anchors
            if anchor.flags
        }
        state_flags = frozenset({"-upgrade", "-migrate-state", "-reconfigure"})
        assert flagged == {
            ("kubectl", "run"): frozenset({"--image"}),
            ("terraform", "init"): state_flags,
            ("terragrunt", "init"): state_flags,
        }


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


class TestNoDeclarableTableHoldsAnUnreachableAnchor:
    """A table is refused where it is WRITTEN if one anchor could never fire.

    One CLI's anchors are tried in declaration order and the first match wins,
    so an anchor whose path is already covered by an earlier one is dead
    config: it reads as coverage and classifies nothing. The verdict never
    changes (both entries say T3), but the user is told the BROAD form when
    the real one was the narrow one -- and the verb and reason are the
    product of the consent layer.

    Order is what carries the meaning here, so the guard rejects only the
    order that is genuinely dead. Declaring the specific anchor first is the
    fix, not a second thing to forbid.
    """

    BROAD = MutativeAnchor(path=("config",))
    SPECIFIC = MutativeAnchor(path=("config", "widgets", "anchor-probe"))

    def test_broad_path_declared_first_is_refused(self):
        with pytest.raises(ValueError) as excinfo:
            _validated_anchor_table({"gcloud": (self.BROAD, self.SPECIFIC)})
        message = str(excinfo.value)
        assert "gcloud" in message
        assert "anchor-probe" in message, (
            f"the refusal must name the anchor that could never fire, or the "
            f"author cannot act on it. Got: {message}"
        )

    def test_specific_path_declared_first_is_accepted(self):
        """The fix for the refused order, and proof it is not over-rejected."""
        table = {"gcloud": (self.SPECIFIC, self.BROAD)}
        assert _validated_anchor_table(table) is table

    def test_identical_unflagged_paths_are_refused(self):
        with pytest.raises(ValueError):
            _validated_anchor_table(
                {"gcloud": (MutativeAnchor(path=("dev",)),
                            MutativeAnchor(path=("dev",)))}
            )

    def test_same_path_differing_only_in_flags_is_accepted(self):
        """The legitimate case: one path, two flag conditions.

        Neither anchor covers the other, because the flag IS the condition --
        a command carrying one flag does not carry the other.
        """
        table = {
            "terraform": (
                MutativeAnchor(path=("init",),
                               flags=frozenset({"-anchor-probe"})),
                MutativeAnchor(path=("init",),
                               flags=frozenset({"-anchor-probe-two"})),
            )
        }
        assert _validated_anchor_table(table) is table

    def test_flagged_then_unflagged_same_path_is_accepted(self):
        """`terraform init` with a flag, then the same path without one.

        The unflagged anchor still fires on every command that lacks the flag,
        so it is reachable -- the flagged one only short-circuits the commands
        that actually carry its flag.
        """
        table = {
            "terraform": (
                MutativeAnchor(path=("init",),
                               flags=frozenset({"-anchor-probe"})),
                MutativeAnchor(path=("init",)),
            )
        }
        assert _validated_anchor_table(table) is table

    def test_unflagged_then_flagged_same_path_is_refused(self):
        """The same two anchors in the order that kills the second one."""
        with pytest.raises(ValueError):
            _validated_anchor_table(
                {"terraform": (
                    MutativeAnchor(path=("init",)),
                    MutativeAnchor(path=("init",),
                                   flags=frozenset({"-anchor-probe"})),
                )}
            )

    def test_narrower_flag_set_under_a_wider_one_is_refused(self):
        """Every flag that could fire the second already fires the first."""
        with pytest.raises(ValueError):
            _validated_anchor_table(
                {"terraform": (
                    MutativeAnchor(
                        path=("init",),
                        flags=frozenset({"-anchor-probe", "-anchor-probe-two"}),
                    ),
                    MutativeAnchor(path=("init",),
                                   flags=frozenset({"-anchor-probe"})),
                )}
            )

    def test_a_prefix_pair_across_two_clis_is_accepted(self):
        """Anchors are keyed by base command; one CLI cannot shadow another."""
        table = {
            "gcloud": (self.BROAD,),
            "terraform": (self.SPECIFIC,),
        }
        assert _validated_anchor_table(table) is table

    def test_both_flag_conditions_stay_reachable_at_runtime(self, monkeypatch):
        """Accepting the legitimate pair is not enough -- both must fire.

        The guard exists to keep declarations reachable, so the pair it
        permits is measured against the live classifier, not just against the
        guard's own verdict.
        """
        _install(monkeypatch, "terraform",
                 MutativeAnchor(path=("init",),
                                flags=frozenset({"-anchor-probe"})))
        _install(monkeypatch, "terraform",
                 MutativeAnchor(path=("init",),
                                flags=frozenset({"-anchor-probe-two"})))

        first = detect_mutative_command("terraform init -anchor-probe")
        second = detect_mutative_command("terraform init -anchor-probe-two")
        assert first.is_mutative is True
        assert second.is_mutative is True
        assert first.dangerous_flags == ("-anchor-probe",)
        assert second.dangerous_flags == ("-anchor-probe-two",), (
            f"the second flag condition must be reached on its own flag, not "
            f"answered by the first anchor. Got {second.dangerous_flags}"
        )

    def test_the_shipped_table_holds_the_property(self):
        assert (
            _validated_anchor_table(COMMAND_PATH_MUTATIVE_UPGRADES)
            is COMMAND_PATH_MUTATIVE_UPGRADES
        )
