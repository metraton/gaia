"""Tests for contract_validator context-usage anomaly detection."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.agents.contract_validator import check_context_usage


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
        }
        result = check_context_usage(project_knowledge, evidence_report)
        assert result["context_ignored"] is False
        assert result["anchors_found"] == 0
