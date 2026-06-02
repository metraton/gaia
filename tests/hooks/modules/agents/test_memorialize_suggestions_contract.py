#!/usr/bin/env python3
"""Tests for the optional ``memorialize_suggestions`` contract field.

The field lets a subagent surface candidate memory entries to the orchestrator
without persisting them itself (T3 reserves curated memory writes for the
user + orchestrator). The parser must:

- Return an empty block when the field is absent.
- Return an empty block when the field is an empty array.
- Surface well-formed suggestions intact.
- Emit warnings (not failures) for malformed entries while keeping the
  contract valid.
- Surface multiple suggestions in order.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.response_contract import (  # noqa: E402
    MemorializeSuggestionsBlock,
    parse_memorialize_suggestions,
    validate_response_contract,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "contracts"
    / "memorialize_suggestions_sample.json"
)


_BASE_EVIDENCE = {
    "patterns_checked": ["read contract layer"],
    "files_checked": ["hooks/modules/agents/response_contract.py"],
    "commands_run": ["`pytest -q` -> ok"],
    "key_outputs": ["parser exposes suggestions"],
    "verbatim_outputs": ["ok"],
    "cross_layer_impacts": ["none"],
    "open_gaps": ["none"],
    "verification": {
        "method": "test",
        "checks": ["pytest -q passed for the contract parser"],
        "result": "pass",
        "details": "parser exposes suggestions as intended",
    },
}

_BASE_STATUS = {
    "plan_status": "COMPLETE",
    "pending_steps": "[]",
    "next_action": "done",
    "agent_id": "a99001",
}


def _wrap(contract_dict: dict) -> str:
    """Wrap a dict as an agent_contract_handoff fenced block inside agent prose."""
    return f"## Findings\n\n```agent_contract_handoff\n{json.dumps(contract_dict, indent=2)}\n```\n"


class TestMemorializeSuggestionsParser:
    def test_contract_without_field_yields_empty_block(self):
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
        }
        block = parse_memorialize_suggestions(_wrap(contract))
        assert isinstance(block, MemorializeSuggestionsBlock)
        assert block.marker_present is False
        assert block.suggestions == []
        assert block.warnings == []

    def test_empty_array_yields_marker_present_with_no_suggestions(self):
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
            "memorialize_suggestions": [],
        }
        block = parse_memorialize_suggestions(_wrap(contract))
        assert block.marker_present is True
        assert block.suggestions == []
        assert block.warnings == []

    def test_single_valid_suggestion_is_surfaced(self):
        suggestion = {
            "slug": "atom_test_pattern",
            "type": "atom",
            "class": "anchor",
            "description": "Pattern X anchors the test contract",
            "body": "When parsing T8 contracts, suggestion presentation is the orchestrator's responsibility.",
            "rationale": "Surfaced while implementing the parser.",
        }
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
            "memorialize_suggestions": [suggestion],
        }
        block = parse_memorialize_suggestions(_wrap(contract))
        assert block.marker_present is True
        assert len(block.suggestions) == 1
        assert block.warnings == []
        got = block.suggestions[0]
        assert got["slug"] == "atom_test_pattern"
        assert got["type"] == "atom"
        assert got["class"] == "anchor"
        assert got["description"].startswith("Pattern X")
        assert "orchestrator" in got["body"]
        assert got["rationale"].startswith("Surfaced")

    def test_malformed_entry_missing_body_emits_warning_without_failing(self):
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
            "memorialize_suggestions": [
                {
                    "slug": "decision_incomplete",
                    "type": "decision",
                    "description": "Description without a body",
                    # body deliberately missing
                },
                {
                    "description": "A valid sibling so the array is not empty",
                    "body": "This one carries enough to be presented.",
                },
            ],
        }
        output = _wrap(contract)
        block = parse_memorialize_suggestions(output)

        # Malformed entry is skipped, valid sibling is kept.
        assert block.marker_present is True
        assert len(block.suggestions) == 1
        assert block.suggestions[0]["description"].startswith("A valid sibling")
        assert any("missing required field" in w and "body" in w for w in block.warnings), (
            f"expected a warning about missing body, got: {block.warnings}"
        )

        # The malformed suggestion does NOT invalidate the surrounding contract.
        result = validate_response_contract(output, task_agent_id="a99001")
        assert result.valid is True, (
            f"memorialize_suggestions warnings must not fail contract validation; "
            f"missing={result.missing}, invalid={result.invalid}"
        )

    def test_multiple_suggestions_are_all_visible_in_order(self):
        suggestions = [
            {
                "slug": "atom_one",
                "type": "atom",
                "description": "First suggestion",
                "body": "Body one.",
            },
            {
                "slug": "decision_two",
                "type": "decision",
                "class": "thread",
                "description": "Second suggestion",
                "body": "Body two.",
                "rationale": "Why it matters.",
            },
            {
                "slug": "negative_three",
                "type": "negative",
                "description": "Third suggestion",
                "body": "Body three.",
            },
        ]
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
            "memorialize_suggestions": suggestions,
        }
        block = parse_memorialize_suggestions(_wrap(contract))
        assert block.marker_present is True
        assert len(block.suggestions) == 3
        assert [s["slug"] for s in block.suggestions] == [
            "atom_one", "decision_two", "negative_three",
        ]
        assert block.warnings == []

    def test_non_list_field_emits_warning_and_returns_empty_suggestions(self):
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
            "memorialize_suggestions": "not-an-array",
        }
        block = parse_memorialize_suggestions(_wrap(contract))
        assert block.marker_present is True
        assert block.suggestions == []
        assert any("expected array" in w for w in block.warnings)

    def test_unknown_type_and_class_pass_through_with_advisory_warning(self):
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": _BASE_EVIDENCE,
            "memorialize_suggestions": [
                {
                    "type": "musing",       # not in MEMORIALIZE_VALID_TYPES
                    "class": "spiral",      # not in MEMORIALIZE_VALID_CLASSES
                    "description": "Off-taxonomy suggestion",
                    "body": "Orchestrator can still decide.",
                },
            ],
        }
        block = parse_memorialize_suggestions(_wrap(contract))
        # The suggestion is kept (advisory) even when type/class are unknown.
        assert len(block.suggestions) == 1
        assert block.suggestions[0]["type"] == "musing"
        # Two advisory warnings, one for type and one for class.
        assert sum(1 for w in block.warnings if "type=" in w) == 1
        assert sum(1 for w in block.warnings if "class=" in w) == 1


class TestMemorializeSuggestionsFixture:
    """Sanity check that the on-disk sample fixture parses cleanly."""

    def test_sample_fixture_parses_with_two_well_formed_suggestions(self):
        contract_dict = json.loads(FIXTURE_PATH.read_text())
        block = parse_memorialize_suggestions(_wrap(contract_dict))
        assert block.marker_present is True
        assert len(block.suggestions) == 2
        assert block.warnings == []
        slugs = [s["slug"] for s in block.suggestions]
        assert "decision_memory_schema_v4_class_status_links" in slugs
        assert "negative_inline_links_in_atom_bodies" in slugs

    def test_sample_fixture_does_not_break_contract_validation(self):
        contract_dict = json.loads(FIXTURE_PATH.read_text())
        output = _wrap(contract_dict)
        result = validate_response_contract(output, task_agent_id="a7c3f9")
        assert result.valid is True, (
            f"sample fixture must be a valid contract; "
            f"missing={result.missing}, invalid={result.invalid}"
        )
