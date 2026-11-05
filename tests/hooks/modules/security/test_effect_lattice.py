"""Every classifier lane reports a non-empty effect set, an unmodelled program reports TOP, and the set implies the shipped verdict."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.hooks.modules.security import test_effect_and_target_truth_table as effect_table
from tests.hooks.modules.security.test_classifier_truth_table import CLASSIFIER_TRUTH_TABLE
from tests.hooks.modules.security.test_effect_and_target_truth_table import ROWS, _observe_isolated, world  # noqa: F401
from tests.hooks.modules.security.test_effect_lexicon_probes import AGENT_ID, PROBES, isolated  # noqa: F401
from tests.hooks.modules.security.test_inline_ast_catalog import EFFECT_CLASSES, EXCLUDED
from tests.hooks.modules.security.test_verb_lexicon_synonyms import EXEC_HELD_BACK, STAYS_FREE, SYNONYMS_ASK

from modules.security import effect_lattice as lattice
from modules.security.blocked_commands import is_blocked_command
from modules.security.composition_rules import CompositionStage, StageType, check_composition
from modules.security.flag_classifiers import classify_by_flags
from modules.security.inline_ast_analyzer import analyze_python_inline
from modules.security.mutative_verbs import CHANGE_VERB_CLASSES, detect_mutative_command
from modules.security.tiers import SecurityTier, classify_command_tier
from modules.security.verdict import Verdict
from modules.tools.bash_validator import BashValidator

PROBE_COMMANDS = sorted({p[1] for p in PROBES} | {c[1] for c in SYNONYMS_ASK + STAYS_FREE + EXEC_HELD_BACK})
CATALOGUE_SOURCE = Path(lattice.__file__).with_name("inline_ast_analyzer.py")


def _shipped(result) -> Verdict:
    if result.allowed:
        return Verdict.FREE
    if result.approval_id or (result.reason or "").startswith(effect_table.COMPOUND_DENIAL):
        return Verdict.UNKNOWN if effect_table.UNDETERMINED in (result.reason or "").lower() else Verdict.SIGNED
    return Verdict.BLOCKED


def _validate(command: str, cwd: Path):
    detect_mutative_command.cache_clear()
    payload = {"cwd": str(cwd), "tool_use_id": "t-lattice", "session_id": "s-lattice",
               "agent_id": AGENT_ID, "agent_type": "developer"}
    return BashValidator(agent_names_scratch=True).validate(
        command, is_subagent=True, session_id="s-lattice", agent_type="developer", hook_payload=payload)


def _assert_lane(effects) -> None:
    assert effects, "a lane mapped a command it classifies to the empty set"
    for effect in effects:
        assert effect.kind in lattice.KINDS and effect.mode in lattice.MODES and effect.scope in lattice.SCOPES, effect


@pytest.mark.parametrize("command", ["frobctl", "frobctl frob x", "zzq-unseen --flag"])
def test_an_unmodelled_program_reports_top(command):
    effects = detect_mutative_command(command).effects
    assert lattice.is_top(effects), effects
    assert lattice.lattice_verdict(effects, unmodelled=Verdict.UNKNOWN) is Verdict.UNKNOWN


def test_an_unmodelled_program_with_no_operands_is_top_through_the_validator(isolated):  # noqa: F811
    result = _validate("frobctl", isolated)
    assert result.allowed and result.effects == lattice.TOP


@pytest.mark.parametrize("lane,effects,kind", [
    ("verb-scan", lambda: detect_mutative_command("kubectl delete ns x").effects, lattice.NET_EGRESS),
    ("verb-scan-read", lambda: detect_mutative_command("ls -la").effects, lattice.FS_READ),
    ("flag-classifier", lambda: classify_by_flags("curl -X POST https://example.invalid").effects, lattice.NET_EGRESS),
    ("python-catalogue", lambda: analyze_python_inline("import os\nos.remove('x')").effects, lattice.FS_WRITE),
    ("floor", lambda: is_blocked_command("rm -rf /").effects, lattice.FS_WRITE),
    ("composition", lambda: check_composition([
        CompositionStage("curl https://example.invalid/x", "|", StageType.NETWORK_READ),
        CompositionStage("bash", None, StageType.EXEC_SINK),
    ]).effects, lattice.CODE_EXEC),
])
def test_each_lane_reports_a_non_empty_set_for_a_case_it_classifies(lane, effects, kind):
    reported = effects()
    _assert_lane(reported)
    assert kind in {effect.kind for effect in reported}, (lane, reported)


def test_a_lane_that_does_not_match_abstains_and_the_join_is_never_empty():
    assert is_blocked_command("ls").effects == lattice.ABSTAINS
    assert lattice.join(lattice.ABSTAINS, lattice.ABSTAINS) == lattice.TOP


def test_every_change_verb_class_maps_onto_the_lattice():
    assert set(CHANGE_VERB_CLASSES) == set(lattice.VERB_CLASS_KINDS)
    assert set(lattice.VERB_CLASS_KINDS.values()) <= lattice.KINDS - {lattice.TOP_KIND}


def test_every_catalogue_and_bandit_class_maps_onto_the_lattice():
    tree = ast.parse(CATALOGUE_SOURCE.read_text(encoding="utf-8"))
    catalogue = next(n.value for n in tree.body if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", "") == "_DANGEROUS_CALLS")
    used = {ast.literal_eval(entry)[2] for entry in catalogue.elts}
    assert used | (EFFECT_CLASSES - {EXCLUDED}) <= set(lattice.CATALOGUE_KINDS)
    assert set(lattice.CATALOGUE_KINDS.values()) <= lattice.KINDS - {lattice.TOP_KIND}


@pytest.mark.parametrize("case_id,family,command,expected_mutative,expected_tier", CLASSIFIER_TRUTH_TABLE,
                         ids=[row[0] for row in CLASSIFIER_TRUTH_TABLE])
def test_the_classifier_table_verdict_follows_from_its_effects(case_id, family, command, expected_mutative, expected_tier):
    result = detect_mutative_command(command)
    _assert_lane(result.effects)
    implied = lattice.lattice_verdict(result.effects)
    assert (implied is not Verdict.FREE) is result.is_mutative, (implied, result)
    tier_effects = lattice.join(is_blocked_command(command).effects, result.effects)
    tier_asks = lattice.lattice_verdict(tier_effects) is not Verdict.FREE
    assert tier_asks is (classify_command_tier(command) is SecurityTier.T3_BLOCKED), tier_effects


@pytest.mark.parametrize("row", [pytest.param(r, id=r["id"]) for r in ROWS])
def test_the_effect_table_verdict_follows_from_its_effects(row, world, monkeypatch):  # noqa: F811
    depth, seen = [0], []
    original = effect_table.BashValidator.validate

    def outermost(self, command, *args, **kwargs):
        depth[0] += 1
        try:
            result = original(self, command, *args, **kwargs)
        finally:
            depth[0] -= 1
        if not depth[0]:
            seen.append(result)
        return result

    outermost.__wrapped__ = original.__wrapped__
    monkeypatch.setattr(effect_table.BashValidator, "validate", outermost)
    _observe_isolated(row, world, monkeypatch)
    result = seen[0]
    _assert_lane(result.effects)
    assert lattice.lattice_verdict(result.effects) is _shipped(result), (row["command"], result.effects)


@pytest.mark.parametrize("command", PROBE_COMMANDS)
def test_every_probe_reports_a_set_that_implies_its_verdict(command, isolated):  # noqa: F811
    result = _validate(command, isolated)
    _assert_lane(result.effects)
    assert lattice.lattice_verdict(result.effects) is _shipped(result), (command, result.effects)


@pytest.mark.xfail(strict=True, reason="known finding: the verb scan stops at the read word `env` and reads the delete after it as a read")
def test_known_finding_a_delete_after_env_is_placed_as_a_read():
    effects = detect_mutative_command("frobctl env delete prod").effects
    assert lattice.is_top(effects) or lattice.lattice_verdict(effects) is not Verdict.FREE, effects
