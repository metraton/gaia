#!/usr/bin/env python3
"""Behavior tests for ``parse_failure_report`` (the failure_report read seam).

The optional ``failure_report`` block is the failure axis: a concrete defect a
turn suffered -- what it attempted, what broke, and the observed proof --
distinct from ``open_gaps`` (the unknown, not a defect). This seam is the one
place task 2's ``episode_anomalies`` writer reads the block from, so it must
normalize consistently rather than leave that reimplemented at the call site.

Advisory means advisory: absent, explicit null, or malformed input must all
return None -- never raise, never a partial dict. Well-formedness is checked
through the SAME core (``gaia.contract.validator.validate_form``'s
FAILURE_REPORT_SHAPE code) the CLI and the SubagentStop gate validate
against -- no second, locally re-implemented shape check.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.contract_validator import parse_failure_report  # noqa: E402


def _contract(failure_report=...) -> dict:
    """A minimal-but-shape-valid IN_PROGRESS contract, optionally carrying
    failure_report. ``...`` (the default) omits the key entirely; any other
    value (including None) sets it explicitly."""
    c = {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c30f1e2d3c4b5",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    if failure_report is not ...:
        c["failure_report"] = failure_report
    return c


def _well_formed() -> dict:
    return {
        "attempted": "gaia contract finalize --plan-task-id 65",
        "symptom": "the CLI rejected the finalize and the row never landed",
        "evidence": ["Rejected: agent_id mismatch: the draft is keyed to 'a1b2'"],
    }


# ---------------------------------------------------------------------------
# Advisory "no hay" signal: absent, null, malformed all return None.
# ---------------------------------------------------------------------------
class TestNoHaySignal:
    def test_returns_none_when_key_absent(self):
        assert parse_failure_report(_contract()) is None

    def test_returns_none_when_explicit_null(self):
        assert parse_failure_report(_contract(failure_report=None)) is None

    def test_returns_none_when_not_an_object(self):
        assert parse_failure_report(_contract(failure_report="not an object")) is None

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda r: {**r, "attempted": ""},
            lambda r: {k: v for k, v in r.items() if k != "attempted"},
            lambda r: {**r, "symptom": "   "},
            lambda r: {**r, "evidence": []},
            lambda r: {**r, "evidence": ["", "  "]},
            lambda r: {**r, "evidence": "a string, not a list"},
            lambda r: {**r, "severity": "catastrophic"},
        ],
    )
    def test_returns_none_for_every_malformed_shape(self, mutate):
        """Mirrors the FAILURE_REPORT_SHAPE cases the validator rejects --
        this seam must never surface a defect the validator itself refused."""
        report = mutate(_well_formed())
        assert parse_failure_report(_contract(failure_report=report)) is None

    def test_never_raises_on_a_deeply_wrong_shape(self):
        # A list, an int, nested garbage -- none of it should raise.
        for bogus in ([1, 2, 3], 42, {"evidence": {"nested": "dict"}}):
            assert parse_failure_report(_contract(failure_report=bogus)) is None


# ---------------------------------------------------------------------------
# Well-formed extraction: the normalized shape the writer will consume.
# ---------------------------------------------------------------------------
class TestWellFormedExtraction:
    def test_extracts_required_fields(self):
        parsed = parse_failure_report(_contract(failure_report=_well_formed()))
        assert parsed is not None
        assert parsed["attempted"] == _well_formed()["attempted"]
        assert parsed["symptom"] == _well_formed()["symptom"]
        assert parsed["evidence"] == _well_formed()["evidence"]

    def test_optional_fields_default_to_none_when_absent(self):
        parsed = parse_failure_report(_contract(failure_report=_well_formed()))
        assert parsed["component"] is None
        assert parsed["severity"] is None

    def test_optional_fields_extracted_when_present(self):
        report = {**_well_formed(), "component": "bin/cli/contract.py", "severity": "WARNING"}
        parsed = parse_failure_report(_contract(failure_report=report))
        assert parsed["component"] == "bin/cli/contract.py"
        # normalized lower-case, matching VALID_FAILURE_SEVERITIES
        assert parsed["severity"] == "warning"

    def test_strips_surrounding_whitespace(self):
        report = {
            "attempted": "  did a thing  ",
            "symptom": "  it broke  ",
            "evidence": ["  saw this  "],
        }
        parsed = parse_failure_report(_contract(failure_report=report))
        assert parsed["attempted"] == "did a thing"
        assert parsed["symptom"] == "it broke"
        assert parsed["evidence"] == ["saw this"]

    def test_non_string_evidence_entries_are_dropped_not_fatal(self):
        report = {**_well_formed(), "evidence": ["real output", 42, None]}
        parsed = parse_failure_report(_contract(failure_report=report))
        assert parsed is not None
        assert parsed["evidence"] == ["real output"]

    def test_well_formed_report_on_a_complete_turn(self):
        """The axis is orthogonal to the outcome -- a COMPLETE turn can still
        report a defect it survived along the way."""
        contract = _contract(failure_report=_well_formed())
        contract["agent_status"]["agent_state"] = "COMPLETE"
        contract["agent_status"]["next_action"] = "done"
        contract["evidence_report"]["verification"] = {"method": "test", "result": "pass"}

        parsed = parse_failure_report(contract)
        assert parsed is not None
        assert parsed["attempted"] == _well_formed()["attempted"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
