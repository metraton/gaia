"""Tests for contract_validator: context-usage anomaly detection and evidence field validation."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.agents.contract_validator import (
    _EVIDENCE_REQUIRED_FIELDS,
    check_context_usage,
    validate,
)


class TestCheckContextUsage:
    """Tests for check_context_usage() soft anomaly detection."""

    def test_context_used_flag_false(self):
        """When evidence references injected anchors, context_ignored is False."""
        project_knowledge = {
            "terraform_infrastructure": {
                "layout": {"base_path": "./qxo-monorepo/terraform"},
            },
            "cluster_details": {
                "cluster_name": "oci-pos-dev-cluster",
            },
        }
        evidence_report = {
            "files_checked": [
                "qxo-monorepo/terraform/main.tf",
                "qxo-monorepo/terraform/variables.tf",
            ],
            "patterns_checked": ["terraform module structure"],
            "commands_run": [],
            "key_outputs": ["Module uses standard layout"],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is False
        assert result["anchors_found"] > 0
        assert result["anchors_in_evidence"] > 0
        assert "qxo-monorepo/terraform" in result["overlap"]

    def test_context_ignored_flag_true(self):
        """When evidence has zero overlap with injected anchors, context_ignored is True."""
        project_knowledge = {
            "terraform_infrastructure": {
                "layout": {"base_path": "./qxo-monorepo/terraform"},
            },
            "cluster_details": {
                "cluster_name": "oci-pos-dev-cluster",
                "namespace": "cart-service-ns",
            },
        }
        evidence_report = {
            "files_checked": [
                "/etc/hosts",
                "/tmp/random-file.txt",
            ],
            "patterns_checked": ["generic pattern"],
            "commands_run": ["ls /tmp"],
            "key_outputs": ["Nothing found"],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is True
        assert result["anchors_found"] > 0
        assert result["anchors_in_evidence"] == 0
        assert result["overlap"] == []

    def test_partial_overlap(self):
        """Some anchors used, some not -- context_ignored should be False."""
        project_knowledge = {
            "terraform_infrastructure": {
                "layout": {"base_path": "./qxo-monorepo/terraform"},
            },
            "gitops_config": {
                "config_path": "./qxo-monorepo/gitops",
            },
        }
        evidence_report = {
            "files_checked": ["qxo-monorepo/terraform/main.tf"],
            "patterns_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is False
        assert result["anchors_in_evidence"] >= 1

    def test_empty_project_knowledge(self):
        """Empty project_knowledge should not flag as ignored."""
        result = check_context_usage({}, {"files_checked": ["/some/file"]})
        assert result["context_ignored"] is False
        assert result["anchors_found"] == 0

    def test_empty_evidence(self):
        """Empty evidence with non-empty project_knowledge should not flag."""
        result = check_context_usage(
            {"section": {"path": "./some/path"}},
            {},
        )
        assert result["context_ignored"] is False
        assert result["anchors_found"] == 0

    def test_none_inputs(self):
        """None inputs should not raise and should not flag."""
        result = check_context_usage(None, None)
        assert result["context_ignored"] is False

    def test_commands_run_as_dicts(self):
        """commands_run entries can be dicts with 'command' key."""
        project_knowledge = {
            "cluster_details": {
                "cluster_name": "oci-pos-dev-cluster",
            },
        }
        evidence_report = {
            "files_checked": [],
            "patterns_checked": [],
            "commands_run": [
                {"command": "kubectl get pods --context oci-pos-dev-cluster"},
            ],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is False
        assert "oci-pos-dev-cluster" in result["overlap"]

    def test_commands_run_as_strings(self):
        """commands_run entries can be plain strings."""
        project_knowledge = {
            "cluster_details": {
                "namespace": "cart-service-ns",
            },
        }
        evidence_report = {
            "files_checked": [],
            "patterns_checked": [],
            "commands_run": ["kubectl get pods -n cart-service-ns -> 3 pods running"],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is False
        assert "cart-service-ns" in result["overlap"]

    def test_no_anchor_worthy_fields(self):
        """project_knowledge with no anchor-worthy field names yields no anchors."""
        project_knowledge = {
            "description": {"summary": "This is a description"},
            "notes": {"text": "Some notes"},
        }
        evidence_report = {
            "files_checked": ["/some/file"],
            "patterns_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is False
        assert result["anchors_found"] == 0


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
                "plan_status": "COMPLETE",
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
        output = f"Some analysis.\n\n```json:contract\n{json.dumps(contract)}\n```"
        result = validate(output, {})
        assert not result.is_valid
        assert "VERBATIM_OUTPUTS" in result.missing
        assert "CROSS_LAYER_IMPACTS" in result.missing
        assert "OPEN_GAPS" in result.missing

    def test_validate_passes_with_all_seven_fields(self):
        """validate() passes when all 7 evidence fields are provided."""
        contract = {
            "agent_status": {
                "plan_status": "COMPLETE",
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
            },
            "consolidation_report": None,
        }
        output = f"Some analysis.\n\n```json:contract\n{json.dumps(contract)}\n```"
        result = validate(output, {})
        assert result.is_valid
        assert result.missing == []
