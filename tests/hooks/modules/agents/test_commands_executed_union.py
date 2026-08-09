"""Tests for the commands_executed union (merge_commands_executed /
extract_commands_executed) in contract_validator.py.

The turn's own persisted dispatch-row envelope and the fenced
``agent_contract_handoff`` block in the final message text are two
independent, partial records of the same COMMANDS_RUN evidence -- a 24h
measurement found 9 turns whose row alone carried commands with no fence,
and 13 turns whose fence alone carried commands with no row. Neither source
may be preferred over the other; the union must lose neither direction,
must not duplicate the ordinary case where both sources agree, and must
never collapse a command that was genuinely run twice.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.agents.contract_validator import (
    _commands_from_contract_dict,
    extract_commands_executed,
    extract_commands_from_evidence,
    merge_commands_executed,
)


def _envelope(commands):
    return {"evidence_report": {"commands_run": commands}}


def _fence(commands):
    envelope = _envelope(commands)
    return f"Some report.\n\n```agent_contract_handoff\n{json.dumps(envelope)}\n```\n"


# ---------------------------------------------------------------------------
# merge_commands_executed: the pure list-merge, independent of parsing
# ---------------------------------------------------------------------------

class TestMergeCommandsExecuted:
    def test_fence_prefix_of_row_recovers_no_loss(self):
        """A row that is a superset (checkpointed the same prefix the fence
        later echoed, plus more) contributes its extra tail once."""
        row = ["git status", "git diff"]
        fence = ["git status"]
        assert merge_commands_executed(row, fence) == ["git status", "git diff"]

    def test_row_empty_fence_full_is_not_lost(self):
        """Direction 2: the row has nothing (never mirrored before the
        final message), the fence has everything -- nothing is dropped."""
        fence = ["kubectl get pods", "kubectl get svc"]
        assert merge_commands_executed([], fence) == fence

    def test_fence_empty_row_full_is_not_lost(self):
        """Direction 1: no fence in the final message at all, the row
        carries the full checkpointed sequence -- nothing is dropped."""
        row = ["terraform plan", "terraform apply -auto-approve"]
        assert merge_commands_executed(row, []) == row

    def test_identical_sources_do_not_duplicate(self):
        """The ordinary case: the fence echoes exactly what was mirrored
        onto the row. The union is that one sequence, not doubled."""
        commands = ["git add -A", "git commit -m x", "git push origin main"]
        result = merge_commands_executed(list(commands), list(commands))
        assert result == commands
        assert len(result) == len(commands)

    def test_intentional_repeat_within_agreeing_sources_is_preserved(self):
        """A command run twice on purpose, recorded identically by both
        sources, stays twice -- literal-equality dedup never collapses an
        aligned repeat into one entry."""
        commands = ["kubectl get pods", "kubectl get pods"]
        result = merge_commands_executed(list(commands), list(commands))
        assert result == ["kubectl get pods", "kubectl get pods"]

    def test_repeat_recorded_by_only_one_source_still_survives(self):
        """The row checkpointed a command once; the fence's later retelling
        (or vice-versa) adds a second real run of the SAME text. That
        second occurrence is a real fact -- not a duplicate to collapse."""
        row = ["echo hi"]
        fence = ["echo hi", "echo hi"]
        assert merge_commands_executed(row, fence) == ["echo hi", "echo hi"]

    def test_divergent_middle_keeps_both_versions_in_row_first_order(self):
        """Where the two sources genuinely disagree (not merely one being a
        superset), the row's version of that stretch is emitted before the
        fence's exclusive item -- the row is the in-flight record, the
        fence is composed once at the end."""
        row = ["git status", "git commit -m draft-a", "git push"]
        fence = ["git status", "git commit -m draft-b", "git push"]
        result = merge_commands_executed(row, fence)
        assert result == [
            "git status", "git commit -m draft-a", "git commit -m draft-b", "git push",
        ]

    def test_missing_middle_item_recovered_without_reordering_the_rest(self):
        row = ["A", "B", "C"]
        fence = ["A", "C"]
        assert merge_commands_executed(row, fence) == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# _commands_from_contract_dict: shape parity with the fence extractor
# ---------------------------------------------------------------------------

class TestCommandsFromContractDict:
    def test_none_contract_is_empty(self):
        assert _commands_from_contract_dict(None) == []

    def test_non_dict_contract_is_empty(self):
        assert _commands_from_contract_dict("not a dict") == []

    def test_plain_string_entries_match_fence_shape(self):
        """The row's evidence_report.commands_run, read straight off a
        finalized row's raw_handoff_json, uses the SAME plain-string shape
        the fence documents (measured against real gaia.db rows) -- so
        this dict-based extractor and extract_commands_from_evidence agree
        on identical input with no extra normalization required."""
        commands = ["kubectl get hr -n qxo -> all reconciled"]
        envelope = _envelope(commands)
        assert _commands_from_contract_dict(envelope) == commands
        assert extract_commands_from_evidence(_fence(commands)) == commands

    def test_dict_shaped_entries_are_tolerated_like_the_fence_extractor(self):
        envelope = _envelope([{"command": "kubectl get pods", "result": "3 running"}])
        assert _commands_from_contract_dict(envelope) == ["kubectl get pods"]

    def test_not_run_entries_are_excluded_like_the_fence_extractor(self):
        envelope = _envelope(["kubectl get pods", "not run", "skipped"])
        assert _commands_from_contract_dict(envelope) == ["kubectl get pods"]


# ---------------------------------------------------------------------------
# extract_commands_executed: the wired entry point, agent_output + row_envelope
# ---------------------------------------------------------------------------

class TestExtractCommandsExecuted:
    def test_missing_fence_row_has_commands_registers_them(self):
        """Direction 1 (measured: 9/94 turns): the final message carries no
        fenced block at all, but the row was checkpointed with commands --
        those commands must now reach commands_executed."""
        row_envelope = _envelope(["gaia contract fill --draft-id x --json '{}'", "pytest -q"])
        result = extract_commands_executed(agent_output="No fence in this message.", row_envelope=row_envelope)
        assert result == ["gaia contract fill --draft-id x --json '{}'", "pytest -q"]

    def test_empty_row_fence_has_commands_are_not_lost(self):
        """Direction 2 (measured: 13/94 turns): the row was never mirrored
        (agent wrote evidence only in its final message), but the fence
        carries the full sequence -- nothing is lost."""
        commands = ["git diff HEAD -> 1 file changed", "git push origin main -> BLOCKED by hook"]
        result = extract_commands_executed(
            agent_output=_fence(commands), row_envelope=None,
        )
        assert result == commands

    def test_row_missing_entirely_behaves_like_empty(self):
        """row_envelope=None (GATE_SOURCE_ROW_MISSING) must not raise and
        must fall back to the fence alone."""
        commands = ["terraform plan -> no changes"]
        result = extract_commands_executed(agent_output=_fence(commands), row_envelope=None)
        assert result == commands

    def test_row_envelope_wrong_type_is_ignored_not_raised(self):
        """A caller may pass whatever _authoritative_envelope resolved to,
        including a non-dict on a parse failure -- must degrade to [],
        never raise."""
        commands = ["echo ok"]
        result = extract_commands_executed(agent_output=_fence(commands), row_envelope="not-a-dict")
        assert result == commands

    def test_normal_case_both_sources_agree_no_duplication(self):
        """The expected steady state: the fence is the same envelope the
        row was finalized with. The union must not double the list."""
        commands = ["npm version --no-git-tag-version -> 1.4.0-rc.3"]
        row_envelope = _envelope(commands)
        result = extract_commands_executed(agent_output=_fence(commands), row_envelope=row_envelope)
        assert result == commands
        assert len(result) == 1
