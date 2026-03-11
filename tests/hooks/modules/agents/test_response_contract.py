#!/usr/bin/env python3
"""Tests for runtime agent response contract validation."""

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


VALID_OUTPUT = """\
## Findings

<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- compared sibling terraform modules
FILES_CHECKED:
- terraform/main.tf
COMMANDS_RUN:
- `terraform plan -no-color` -> plan succeeded
KEY_OUTPUTS:
- plan adds one bucket and no destroys
VERBATIM_OUTPUTS:
- none
CROSS_LAYER_IMPACTS:
- app_ci_tooling needs TURBO cache env vars updated
OPEN_GAPS:
- none
<!-- /EVIDENCE_REPORT -->

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Report findings to the orchestrator
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""

VALID_MULTI_SURFACE_OUTPUT = """\
## Findings

<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- compared sibling terraform modules
FILES_CHECKED:
- terraform/main.tf
COMMANDS_RUN:
- `terraform plan -no-color` -> plan succeeded
KEY_OUTPUTS:
- plan adds one bucket and no destroys
VERBATIM_OUTPUTS:
- none
CROSS_LAYER_IMPACTS:
- app_ci_tooling needs TURBO cache env vars updated
OPEN_GAPS:
- secret injection path still needs runtime validation
<!-- /EVIDENCE_REPORT -->

<!-- CONSOLIDATION_REPORT -->
OWNERSHIP_ASSESSMENT: cross_surface_dependency
CONFIRMED_FINDINGS:
- terraform module defines the bucket correctly
SUSPECTED_FINDINGS:
- runtime token source may be misconfigured
CONFLICTS:
- none
OPEN_GAPS:
- cloud-troubleshooter should validate the live secret mapping
NEXT_BEST_AGENT:
- cloud-troubleshooter
<!-- /CONSOLIDATION_REPORT -->

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Report findings to the orchestrator
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""


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
        output = """\
## Findings

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Done
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""
        result = validate_response_contract(output, task_agent_id="a12345")
        assert result.valid is False
        assert result.recommended_action == "resume_same_agent_contract_repair"
        assert "EVIDENCE_REPORT" in result.missing
        assert "PATTERNS_CHECKED" in result.missing

    def test_missing_verbatim_outputs_requires_repair(self):
        output = """\
## Findings

<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- compared sibling terraform modules
FILES_CHECKED:
- terraform/main.tf
COMMANDS_RUN:
- `terraform plan -no-color` -> plan succeeded
KEY_OUTPUTS:
- plan adds one bucket and no destroys
CROSS_LAYER_IMPACTS:
- app_ci_tooling needs TURBO cache env vars updated
OPEN_GAPS:
- none
<!-- /EVIDENCE_REPORT -->

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Report findings to the orchestrator
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""
        result = validate_response_contract(output, task_agent_id="a12345")
        assert result.valid is False
        assert "VERBATIM_OUTPUTS" in result.missing

    def test_invalid_plan_status_is_rejected(self):
        output = VALID_OUTPUT.replace("PLAN_STATUS: COMPLETE", "PLAN_STATUS: DONE")
        result = validate_response_contract(output, task_agent_id="a12345")
        assert result.valid is False
        assert "PLAN_STATUS:DONE" in result.invalid

    def test_missing_agent_id_without_task_id_escalates(self):
        output = VALID_OUTPUT.replace("AGENT_ID: a12345\n", "")
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

VALID_OUTPUT_WITH_FENCED_VERBATIM = """\
## Findings

<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- compared sibling terraform modules
FILES_CHECKED:
- terraform/main.tf
COMMANDS_RUN:
- `terraform plan -no-color` -> plan succeeded
- `kubectl get pods -n staging` -> 3 pods found
KEY_OUTPUTS:
- plan adds one bucket and no destroys
VERBATIM_OUTPUTS:
- `kubectl get pods -n staging`:
  ```
  NAME            READY   STATUS
  api-gateway     0/1     OOMKilled
  redis-cache     1/1     Running
  ```
- `terraform plan -no-color`:
  ```
  Plan: 1 to add, 0 to change, 0 to destroy.
  ```
CROSS_LAYER_IMPACTS:
- app_ci_tooling needs TURBO cache env vars updated
OPEN_GAPS:
- none
<!-- /EVIDENCE_REPORT -->

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Report findings to the orchestrator
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""


class TestVerbatimOutputsWithFencedBlocks:
    def test_fenced_code_blocks_are_preserved(self):
        """Verify VERBATIM_OUTPUTS with fenced code blocks parses correctly."""
        result = validate_response_contract(
            VALID_OUTPUT_WITH_FENCED_VERBATIM,
            task_agent_id="a12345",
        )
        assert result.valid is True
        verbatim = result.evidence_report.fields.get("VERBATIM_OUTPUTS", [])
        assert len(verbatim) == 1, "VERBATIM_OUTPUTS should have one raw text entry"
        raw_text = verbatim[0]
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
