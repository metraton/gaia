"""Contract coverage for versioned, proposal-only curated-memory deltas."""

import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS))

from modules.agents.response_contract import parse_memory_delta


def test_parses_supported_operations() -> None:
    contract = {
        "memory_delta": {
            "version": 1,
            "proposals": [
                {
                    "operation": "append",
                    "scope": {"workspace": "me", "project": "branchkinect"},
                    "target": {"slug": "project_branchkinect_pending"},
                    "append": {"body": "New evidence"},
                },
                {
                    "operation": "reclassify",
                    "target": {"slug": "project_branchkinect_pending"},
                    "reclassify": {"class": "thread", "status": "graduated"},
                },
                {
                    "operation": "link",
                    "target": {"slug": "project_branchkinect_pending"},
                    "link": {"to_slug": "decision_branchkinect_done", "kind": "graduated_to"},
                },
            ],
        }
    }
    block = parse_memory_delta("", parsed_contract=contract)
    assert block.version == 1
    assert [p["operation"] for p in block.proposals] == ["append", "reclassify", "link"]
    assert block.warnings == []


def test_legacy_memorialize_normalizes_to_create() -> None:
    contract = {"memorialize_suggestions": [{"description": "D", "body": "B"}]}
    block = parse_memory_delta("", parsed_contract=contract)
    assert block.proposals == [
        {"operation": "create", "create": {"description": "D", "body": "B"}}
    ]


def test_invalid_entries_are_advisory_and_skipped() -> None:
    contract = {"memory_delta": {"version": 1, "proposals": [
        {"operation": "delete", "delete": {"slug": "x"}},
        {"operation": "append"},
    ]}}
    block = parse_memory_delta("", parsed_contract=contract)
    assert block.proposals == []
    assert len(block.warnings) == 2


def test_new_delta_wins_over_legacy_without_duplication() -> None:
    contract = {
        "memory_delta": {"version": 1, "proposals": []},
        "memorialize_suggestions": [{"description": "D", "body": "B"}],
    }
    block = parse_memory_delta("", parsed_contract=contract)
    assert block.proposals == []
