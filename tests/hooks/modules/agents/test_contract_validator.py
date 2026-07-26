"""Tests for contract_validator: evidence field validation."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.agents.contract_validator import (
    _EVIDENCE_REQUIRED_FIELDS,
    validate,
)


class TestEvidenceRequiredFields:
    """Tests that _EVIDENCE_REQUIRED_FIELDS aligns with response_contract.py."""

    def test_all_seven_fields_present(self):
        """_EVIDENCE_REQUIRED_FIELDS must contain all 7 evidence fields."""
        expected = [
            "PATTERNS_CHECKED", "FILES_CHECKED", "COMMANDS_RUN", "KEY_OUTPUTS",
            "VERBATIM_OUTPUTS", "CROSS_LAYER_IMPACTS", "OPEN_GAPS",
        ]
        assert _EVIDENCE_REQUIRED_FIELDS == expected

    def test_validate_reports_missing_new_fields(self):
        """validate() flags missing VERBATIM_OUTPUTS, CROSS_LAYER_IMPACTS, OPEN_GAPS."""
        # Contract with only the original 4 fields -- missing the 3 new ones
        contract = {
            "agent_status": {
                "agent_state": "COMPLETE",
                "agent_id": "a1f2c3d4",
                "pending_steps": [],
                "next_action": "done",
            },
            "evidence_report": {
                "patterns_checked": ["some pattern"],
                "files_checked": ["some/file.py"],
                "commands_run": ["ls -> ok"],
                "key_outputs": ["all good"],
            },
            "consolidation_report": None,
        }
        output = f"Some analysis.\n\n```agent_contract_handoff\n{json.dumps(contract)}\n```"
        # Non-empty injected_context prevents fallback to filesystem cache
        task_info = {"injected_context": {"investigation_brief": {}}}
        result = validate(output, task_info)
        assert not result.is_valid
        assert "VERBATIM_OUTPUTS" in result.missing
        assert "CROSS_LAYER_IMPACTS" in result.missing
        assert "OPEN_GAPS" in result.missing

    def test_validate_passes_with_all_seven_fields(self):
        """validate() passes when all 7 evidence fields plus verification are provided."""
        contract = {
            "agent_status": {
                "agent_state": "COMPLETE",
                "agent_id": "a1f2c3d4",
                "pending_steps": [],
                "next_action": "done",
            },
            "evidence_report": {
                "patterns_checked": ["some pattern"],
                "files_checked": ["some/file.py"],
                "commands_run": ["ls -> ok"],
                "key_outputs": ["all good"],
                "verbatim_outputs": ["output here"],
                "cross_layer_impacts": ["none"],
                "open_gaps": ["none"],
                "verification": {
                    "method": "test",
                    "checks": ["all tests pass"],
                    "result": "pass",
                    "details": "confirmed working"
                }
            },
            "consolidation_report": None,
        }
        output = f"Some analysis.\n\n```agent_contract_handoff\n{json.dumps(contract)}\n```"
        # Non-empty injected_context prevents fallback to filesystem cache
        task_info = {"injected_context": {"investigation_brief": {}}}
        result = validate(output, task_info)
        assert result.is_valid
        assert result.missing == []
