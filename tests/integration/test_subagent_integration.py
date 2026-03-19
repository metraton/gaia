#!/usr/bin/env python3
"""
T019: SubagentStop Adapter Integration Tests.

Full flow integration: adapter parse SubagentStop -> extract AgentCompletion
-> validate response contract -> verify contract validation result.

Tests that the ClaudeCodeAdapter correctly parses SubagentStop events and
that the response contract validator detects valid/invalid agent outputs.

All fixtures use the ``json:contract`` fenced-block format.

Modules under test:
  - hooks/adapters/claude_code.py (ClaudeCodeAdapter.parse_agent_completion, format_completion_response)
  - hooks/modules/agents/response_contract.py (validate_response_contract)
  - hooks/adapters/types.py (AgentCompletion, CompletionResult)
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import (
    AgentCompletion,
    CompletionResult,
    HookEventType,
    HookResponse,
)
from modules.agents.response_contract import (
    validate_response_contract,
    parse_agent_status,
    parse_evidence_report,
    EVIDENCE_FIELDS,
    VALID_PLAN_STATUSES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contract_output(contract_dict: dict) -> str:
    """Wrap a dict as a json:contract fenced block inside agent prose."""
    block = json.dumps(contract_dict, indent=2)
    return f"## Findings\n\n```json:contract\n{block}\n```\n"


# ---------------------------------------------------------------------------
# Test data constants
# ---------------------------------------------------------------------------

_VALID_EVIDENCE = {
    "patterns_checked": ["Existing pod health check patterns in cluster_details"],
    "files_checked": ["/home/user/project/.claude/project-context/project-context.json"],
    "commands_run": ["`kubectl get pods -n default` -> 3 pods found, all Running"],
    "key_outputs": ["All 3 pods are Running with 0 restarts"],
    "verbatim_outputs": [
        "`kubectl get pods -n default`:\n"
        "  NAME       READY   STATUS    RESTARTS   AGE\n"
        "  nginx-1    1/1     Running   0          2h\n"
        "  nginx-2    1/1     Running   0          2h\n"
        "  nginx-3    1/1     Running   0          2h"
    ],
    "cross_layer_impacts": ["none"],
    "open_gaps": ["none"],
}

_VALID_STATUS = {
    "plan_status": "COMPLETE",
    "pending_steps": "none",
    "next_action": "Report findings to orchestrator",
    "agent_id": "a1b2c3d4e5",
}

VALID_AGENT_OUTPUT = _make_contract_output({
    "agent_status": _VALID_STATUS,
    "evidence_report": _VALID_EVIDENCE,
})

MISSING_EVIDENCE_AGENT_OUTPUT = _make_contract_output({
    "agent_status": _VALID_STATUS,
})

_VERBATIM_EVIDENCE = {
    "patterns_checked": ["Helm release naming conventions"],
    "files_checked": ["/home/user/project/charts/values.yaml"],
    "commands_run": [
        "`helm list -A` -> 5 releases found",
        "`kubectl get pods -n app` -> 2 pods running",
    ],
    "key_outputs": ["5 helm releases across 3 namespaces"],
    "verbatim_outputs": [
        "`helm list -A`:\n"
        "  NAME            NAMESPACE  REVISION  STATUS    CHART\n"
        "  orders-svc      app        3         deployed  orders-0.53.0\n"
        "  payments-api    app        1         deployed  payments-1.2.0",
        "`kubectl get pods -n app`:\n"
        "  NAME                          READY   STATUS\n"
        "  orders-svc-abc123             1/1     Running\n"
        "  payments-api-def456           1/1     Running",
    ],
    "cross_layer_impacts": ["GitOps desired state matches live runtime"],
    "open_gaps": ["none"],
}

AGENT_OUTPUT_WITH_VERBATIM = _make_contract_output({
    "agent_status": {
        "plan_status": "COMPLETE",
        "pending_steps": "none",
        "next_action": "Report findings",
        "agent_id": "af097c4abc",
    },
    "evidence_report": _VERBATIM_EVIDENCE,
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_subagent_stdin(
    agent_type: str = "cloud-troubleshooter",
    agent_id: str = "agent-integration-001",
    last_message: str = "",
    transcript_path: str = "",
) -> str:
    """Build a Claude Code SubagentStop stdin JSON payload."""
    return json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": "subagent-test-session",
        "agent_type": agent_type,
        "agent_id": agent_id,
        "agent_transcript_path": transcript_path,
        "last_assistant_message": last_message,
        "cwd": "/tmp/test-project",
    })


# ============================================================================
# Test Suite: Valid Agent Response with AGENT_STATUS
# ============================================================================

class TestValidAgentResponse:
    """SubagentStop: valid agent response -> parse -> validate contract -> valid."""

    def test_valid_response_contract(self):
        """Full valid response with EVIDENCE_REPORT and AGENT_STATUS -> valid."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_subagent_stdin(
            last_message=VALID_AGENT_OUTPUT,
        )

        event = adapter.parse_event(stdin_json)
        assert event.event_type == HookEventType.SUBAGENT_STOP

        completion = adapter.parse_agent_completion(event.payload)
        assert isinstance(completion, AgentCompletion)
        assert completion.agent_type == "cloud-troubleshooter"
        assert completion.session_id == "subagent-test-session"

        # Validate the response contract
        validation = validate_response_contract(
            VALID_AGENT_OUTPUT,
            task_agent_id="a1b2c3d4e5",
        )

        assert validation.valid is True
        assert len(validation.missing) == 0
        assert len(validation.invalid) == 0
        assert validation.agent_status.plan_status == "COMPLETE"
        assert validation.agent_status.agent_id == "a1b2c3d4e5"

    def test_valid_response_formatted_as_completion(self):
        """Valid response -> format CompletionResult -> HookResponse."""
        adapter = ClaudeCodeAdapter()

        result = CompletionResult(
            contract_valid=True,
            anomalies=[],
            repair_needed=False,
        )

        response = adapter.format_completion_response(result)

        assert isinstance(response, HookResponse)
        assert response.exit_code == 0
        assert response.output["contract_valid"] is True
        assert response.output["anomalies_detected"] == 0

    def test_agent_status_fields_extracted(self):
        """AGENT_STATUS fields are correctly extracted."""
        status = parse_agent_status(VALID_AGENT_OUTPUT)

        assert status.marker_present is True
        assert status.plan_status == "COMPLETE"
        assert status.agent_id == "a1b2c3d4e5"
        assert "none" in status.pending_steps.lower()


# ============================================================================
# Test Suite: Agent Response Missing EVIDENCE_REPORT
# ============================================================================

class TestMissingEvidenceReport:
    """SubagentStop: response missing EVIDENCE_REPORT -> needs repair."""

    def test_missing_evidence_report_detected(self):
        """Response without EVIDENCE_REPORT -> validation fails."""
        validation = validate_response_contract(
            MISSING_EVIDENCE_AGENT_OUTPUT,
            task_agent_id="a1b2c3d4e5",
        )

        assert validation.valid is False
        assert "EVIDENCE_REPORT" in validation.missing
        assert validation.evidence_required is True
        assert validation.recommended_action == "resume_same_agent_contract_repair"

    def test_missing_evidence_formatted_as_repair_needed(self):
        """Missing evidence -> CompletionResult with repair_needed=True."""
        adapter = ClaudeCodeAdapter()

        validation = validate_response_contract(
            MISSING_EVIDENCE_AGENT_OUTPUT,
            task_agent_id="a1b2c3d4e5",
        )

        result = CompletionResult(
            contract_valid=validation.valid,
            anomalies=[{"type": "missing_evidence_report", "missing": validation.missing}],
            repair_needed=True,
        )

        response = adapter.format_completion_response(result)

        assert response.exit_code == 0  # SubagentStop never blocks
        assert response.output["contract_valid"] is False
        assert response.output["repair_needed"] is True
        assert response.output["anomalies_detected"] == 1


# ============================================================================
# Test Suite: Agent Response with VERBATIM_OUTPUTS
# ============================================================================

class TestVerbatimOutputsExtraction:
    """SubagentStop: response with VERBATIM_OUTPUTS -> commands extracted."""

    def test_verbatim_outputs_extracted(self):
        """VERBATIM_OUTPUTS field is present and populated."""
        evidence = parse_evidence_report(AGENT_OUTPUT_WITH_VERBATIM)

        assert evidence.marker_present is True
        verbatim = evidence.fields.get("VERBATIM_OUTPUTS", [])
        assert len(verbatim) > 0
        # The raw block should contain helm and kubectl commands
        raw_text = " ".join(verbatim)
        assert "helm list -A" in raw_text
        assert "kubectl get pods -n app" in raw_text

    def test_commands_run_extracted(self):
        """COMMANDS_RUN field should list the commands executed."""
        evidence = parse_evidence_report(AGENT_OUTPUT_WITH_VERBATIM)

        commands_run = evidence.fields.get("COMMANDS_RUN", [])
        assert len(commands_run) > 0
        raw_text = commands_run[0] if commands_run else ""
        assert "helm list" in raw_text

    def test_full_validation_with_verbatim_passes(self):
        """Full contract validation passes when all fields are present."""
        validation = validate_response_contract(
            AGENT_OUTPUT_WITH_VERBATIM,
            task_agent_id="af097c4abc",
        )

        assert validation.valid is True
        assert len(validation.missing) == 0

    def test_evidence_report_all_fields_present(self):
        """All EVIDENCE_FIELDS are present in the valid output."""
        evidence = parse_evidence_report(AGENT_OUTPUT_WITH_VERBATIM)

        for field in EVIDENCE_FIELDS:
            field_content = evidence.fields.get(field, [])
            assert len(field_content) > 0, (
                f"EVIDENCE_REPORT field '{field}' should be populated"
            )


# ============================================================================
# Test Suite: Consolidation Required
# ============================================================================

class TestConsolidationRequired:
    """SubagentStop: multi-surface task requires CONSOLIDATION_REPORT."""

    VALID_WITH_CONSOLIDATION = _make_contract_output({
        "agent_status": {
            "plan_status": "COMPLETE",
            "pending_steps": "none",
            "next_action": "Report to orchestrator",
            "agent_id": "ab1234cdef",
        },
        "evidence_report": {
            "patterns_checked": ["Existing deployment patterns"],
            "files_checked": ["/home/user/project/terraform/main.tf"],
            "commands_run": ["`terraform plan` -> no changes"],
            "key_outputs": ["Infrastructure matches desired state"],
            "verbatim_outputs": ["none"],
            "cross_layer_impacts": ["GitOps manifests are aligned with Terraform state"],
            "open_gaps": ["none"],
        },
        "consolidation_report": {
            "ownership_assessment": "owned_here",
            "confirmed_findings": ["Infrastructure matches desired state"],
            "suspected_findings": ["none"],
            "conflicts": ["none"],
            "open_gaps": ["none"],
            "next_best_agent": ["none"],
        },
    })

    def test_consolidation_report_validated_when_required(self):
        """When consolidation_required=True, CONSOLIDATION_REPORT must be present."""
        validation = validate_response_contract(
            self.VALID_WITH_CONSOLIDATION,
            task_agent_id="ab1234cdef",
            consolidation_required=True,
        )

        assert validation.valid is True
        assert validation.consolidation_required is True

    def test_missing_consolidation_report_detected(self):
        """Missing CONSOLIDATION_REPORT when required -> validation fails."""
        validation = validate_response_contract(
            VALID_AGENT_OUTPUT,  # Has evidence but no consolidation
            task_agent_id="a1b2c3d4e5",
            consolidation_required=True,
        )

        assert validation.valid is False
        assert "CONSOLIDATION_REPORT" in validation.missing

    def test_consolidation_not_required_by_default(self):
        """When consolidation_required=False (default), CONSOLIDATION_REPORT is not checked."""
        validation = validate_response_contract(
            VALID_AGENT_OUTPUT,
            task_agent_id="a1b2c3d4e5",
            consolidation_required=False,
        )

        assert validation.valid is True
        assert validation.consolidation_required is False


# ============================================================================
# Test Suite: Edge Cases
# ============================================================================

class TestSubagentEdgeCases:
    """SubagentStop: edge cases in parsing and validation."""

    def test_empty_last_message(self):
        """Empty last_assistant_message is parsed without error."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_subagent_stdin(last_message="")

        event = adapter.parse_event(stdin_json)
        completion = adapter.parse_agent_completion(event.payload)

        assert completion.last_message == ""
        assert completion.agent_type == "cloud-troubleshooter"

    def test_missing_agent_id_in_payload(self):
        """Missing agent_id defaults to empty string."""
        adapter = ClaudeCodeAdapter()
        stdin_json = json.dumps({
            "hook_event_name": "SubagentStop",
            "session_id": "test",
            "agent_type": "devops-developer",
        })

        event = adapter.parse_event(stdin_json)
        completion = adapter.parse_agent_completion(event.payload)

        assert completion.agent_id == ""
        assert completion.agent_type == "devops-developer"

    def test_invalid_plan_status_detected(self):
        """Invalid PLAN_STATUS value -> invalid field reported."""
        bad_output = _make_contract_output({
            "agent_status": {
                "plan_status": "INVALID_STATUS",
                "pending_steps": "none",
                "next_action": "none",
                "agent_id": "a12345abcde",
            },
            "evidence_report": {
                "patterns_checked": ["test"],
                "files_checked": ["test"],
                "commands_run": ["test"],
                "key_outputs": ["test"],
                "verbatim_outputs": ["none"],
                "cross_layer_impacts": ["none"],
                "open_gaps": ["none"],
            },
        })
        validation = validate_response_contract(bad_output, task_agent_id="a12345abcde")

        assert validation.valid is False
        assert any("PLAN_STATUS" in inv for inv in validation.invalid)
