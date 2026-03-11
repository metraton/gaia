#!/usr/bin/env python3
"""Tests for runtime agent response contract validation.

All fixtures use the ``json:contract`` fenced-block format parsed by
``contract_validator.parse_contract()``.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.response_contract import (
    _get_contract_dir,
    clear_contract_dir_cache,
    load_last_validation,
    parse_agent_status,
    parse_evidence_report,
    save_validation_result,
    validate_response_contract,
)


def _make_contract_output(contract_dict: dict) -> str:
    """Wrap a dict as a json:contract fenced block inside agent prose."""
    block = json.dumps(contract_dict, indent=2)
    return f"## Findings\n\n```json:contract\n{block}\n```\n"


_BASE_EVIDENCE = {
    "patterns_checked": ["compared sibling terraform modules"],
    "files_checked": ["terraform/main.tf"],
    "commands_run": ["`terraform plan -no-color` -> plan succeeded"],
    "key_outputs": ["plan adds one bucket and no destroys"],
    "verbatim_outputs": ["none"],
    "cross_layer_impacts": ["app_ci_tooling needs TURBO cache env vars updated"],
    "open_gaps": ["none"],
}

_BASE_STATUS = {
    "plan_status": "COMPLETE",
    "pending_steps": "[]",
    "next_action": "Report findings to the orchestrator",
    "agent_id": "a12345",
}

_VALID_CONTRACT = {
    "agent_status": _BASE_STATUS,
    "evidence_report": _BASE_EVIDENCE,
}

VALID_OUTPUT = _make_contract_output(_VALID_CONTRACT)

_VALID_MULTI_SURFACE_CONTRACT = {
    "agent_status": _BASE_STATUS,
    "evidence_report": {
        **_BASE_EVIDENCE,
        "open_gaps": ["secret injection path still needs runtime validation"],
    },
    "consolidation_report": {
        "ownership_assessment": "cross_surface_dependency",
        "confirmed_findings": ["terraform module defines the bucket correctly"],
        "suspected_findings": ["runtime token source may be misconfigured"],
        "conflicts": ["none"],
        "open_gaps": ["cloud-troubleshooter should validate the live secret mapping"],
        "next_best_agent": ["cloud-troubleshooter"],
    },
}

VALID_MULTI_SURFACE_OUTPUT = _make_contract_output(_VALID_MULTI_SURFACE_CONTRACT)


class TestParseResponseBlocks:
    def test_parse_agent_status(self):
        status = parse_agent_status(VALID_OUTPUT)
        assert status.marker_present is True
        assert status.plan_status == "COMPLETE"
        assert status.next_action == "Report findings to the orchestrator"
        assert status.agent_id == "a12345"

    def test_parse_evidence_report(self):
        evidence = parse_evidence_report(VALID_OUTPUT)
        assert evidence.marker_present is True
        assert "terraform/main.tf" in evidence.fields["FILES_CHECKED"][0]
        assert "none" in evidence.fields["OPEN_GAPS"][0]


class TestValidateResponseContract:
    def test_valid_output_passes(self):
        result = validate_response_contract(VALID_OUTPUT, task_agent_id="a12345")
        assert result.valid is True
        assert result.recommended_action == "none"
        assert result.missing == []
        assert result.invalid == []

    def test_missing_evidence_report_requires_repair(self):
        contract = {
            "agent_status": _BASE_STATUS,
        }
        output = _make_contract_output(contract)
        result = validate_response_contract(output, task_agent_id="a12345")
        assert result.valid is False
        assert result.recommended_action == "resume_same_agent_contract_repair"
        assert "EVIDENCE_REPORT" in result.missing
        assert "PATTERNS_CHECKED" in result.missing

    def test_missing_verbatim_outputs_requires_repair(self):
        evidence_no_verbatim = {k: v for k, v in _BASE_EVIDENCE.items() if k != "verbatim_outputs"}
        contract = {
            "agent_status": _BASE_STATUS,
            "evidence_report": evidence_no_verbatim,
        }
        output = _make_contract_output(contract)
        result = validate_response_contract(output, task_agent_id="a12345")
        assert result.valid is False
        assert "VERBATIM_OUTPUTS" in result.missing

    def test_invalid_plan_status_is_rejected(self):
        contract = {
            "agent_status": {**_BASE_STATUS, "plan_status": "DONE"},
            "evidence_report": _BASE_EVIDENCE,
        }
        output = _make_contract_output(contract)
        result = validate_response_contract(output, task_agent_id="a12345")
        assert result.valid is False
        assert "PLAN_STATUS:DONE" in result.invalid

    def test_missing_agent_id_without_task_id_escalates(self):
        contract = {
            "agent_status": {k: v for k, v in _BASE_STATUS.items() if k != "agent_id"},
            "evidence_report": _BASE_EVIDENCE,
        }
        output = _make_contract_output(contract)
        result = validate_response_contract(output, task_agent_id="")
        assert result.valid is False
        assert result.recommended_action == "escalate_contract_repair"

    def test_multi_surface_output_requires_consolidation_report(self):
        result = validate_response_contract(
            VALID_OUTPUT,
            task_agent_id="a12345",
            consolidation_required=True,
        )
        assert result.valid is False
        assert "CONSOLIDATION_REPORT" in result.missing
        assert "OWNERSHIP_ASSESSMENT" in result.missing

    def test_multi_surface_output_with_consolidation_report_passes(self):
        result = validate_response_contract(
            VALID_MULTI_SURFACE_OUTPUT,
            task_agent_id="a12345",
            consolidation_required=True,
        )
        assert result.valid is True
        assert result.consolidation_required is True
        assert result.consolidation_report.ownership_assessment == "cross_surface_dependency"


_VERBATIM_EVIDENCE = {
    **_BASE_EVIDENCE,
    "commands_run": [
        "`terraform plan -no-color` -> plan succeeded",
        "`kubectl get pods -n staging` -> 3 pods found",
    ],
    "verbatim_outputs": [
        "`kubectl get pods -n staging`:\n  NAME            READY   STATUS\n  api-gateway     0/1     OOMKilled\n  redis-cache     1/1     Running",
        "`terraform plan -no-color`:\n  Plan: 1 to add, 0 to change, 0 to destroy.",
    ],
}

_VERBATIM_CONTRACT = {
    "agent_status": _BASE_STATUS,
    "evidence_report": _VERBATIM_EVIDENCE,
}

VALID_OUTPUT_WITH_FENCED_VERBATIM = _make_contract_output(_VERBATIM_CONTRACT)


class TestVerbatimOutputsWithFencedBlocks:
    def test_fenced_code_blocks_are_preserved(self):
        """Verify VERBATIM_OUTPUTS with fenced code blocks parses correctly."""
        result = validate_response_contract(
            VALID_OUTPUT_WITH_FENCED_VERBATIM,
            task_agent_id="a12345",
        )
        assert result.valid is True
        verbatim = result.evidence_report.fields.get("VERBATIM_OUTPUTS", [])
        assert len(verbatim) >= 1, "VERBATIM_OUTPUTS should have entries"
        raw_text = " ".join(verbatim)
        assert "OOMKilled" in raw_text
        assert "redis-cache" in raw_text
        assert "Plan: 1 to add" in raw_text

    def test_fenced_blocks_do_not_bleed_into_next_field(self):
        """Fenced code blocks should not leak content into CROSS_LAYER_IMPACTS."""
        evidence = parse_evidence_report(VALID_OUTPUT_WITH_FENCED_VERBATIM)
        cross_layer = evidence.fields.get("CROSS_LAYER_IMPACTS", [])
        assert len(cross_layer) == 1
        assert "OOMKilled" not in cross_layer[0]
        assert "TURBO" in cross_layer[0]

    def test_simple_none_verbatim_still_works(self):
        """The original '- none' format should still parse correctly."""
        evidence = parse_evidence_report(VALID_OUTPUT)
        verbatim = evidence.fields.get("VERBATIM_OUTPUTS", [])
        assert len(verbatim) == 1
        assert "none" in verbatim[0]



class TestContractDirCache:
    def test_contract_dir_cache_is_scoped_per_session(self, tmp_path, monkeypatch):
        from modules.core.paths import clear_path_cache
        clear_path_cache()
        clear_contract_dir_cache()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "session-a")
        (tmp_path / ".claude").mkdir()

        path_a = _get_contract_dir()

        monkeypatch.setenv("CLAUDE_SESSION_ID", "session-b")
        path_b = _get_contract_dir()

        assert path_a != path_b
        assert path_a.name == "session-a"
        assert path_b.name == "session-b"
        clear_path_cache()
        clear_contract_dir_cache()
