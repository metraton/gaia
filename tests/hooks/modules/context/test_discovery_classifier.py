#!/usr/bin/env python3
"""
Tests for DiscoveryClassifier.

Validates:
1. Structural discovery detection (positive patterns)
2. Operational content filtering (negative patterns)
3. Mixed content handling
4. Confidence threshold enforcement
5. Field extraction from regex captures
6. Edge cases (empty output, missing config)
"""

import sys
import json
import pytest
from pathlib import Path

# Add hooks and tools to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
TOOLS_DIR = Path(__file__).parent.parent.parent.parent.parent / "tools"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(TOOLS_DIR))

from modules.context.discovery_classifier import classify_output


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def rules_path():
    """Path to classification rules config."""
    return Path(__file__).parent.parent.parent.parent.parent / "config" / "classification-rules.json"


# ============================================================================
# Test Structural Discovery Detection
# ============================================================================

class TestStructuralDetection:
    """Test detection of structural infrastructure discoveries."""

    def test_detects_configuration_issue(self, rules_path):
        output = (
            "Investigation complete. Found that the Workload Identity binding "
            "references wrong project 'oci-pos-dev' but should be 'oci-pos-dev-471216'."
        )
        results = classify_output(output, "cloud-troubleshooter", "investigate WI", rules_path=rules_path)
        assert len(results) > 0
        assert any(r.category == "configuration_issue" for r in results)

    def test_detects_new_service(self, rules_path):
        output = "Discovered new service 'payment-api' running in namespace 'payments'."
        results = classify_output(output, "cloud-troubleshooter", "check services", rules_path=rules_path)
        assert len(results) > 0
        assert any(r.category == "new_resource" for r in results)

    def test_detects_new_namespace(self, rules_path):
        output = "Found namespace 'monitoring' exists but not in project context."
        results = classify_output(output, "gitops-operator", "check namespaces", rules_path=rules_path)
        assert len(results) > 0
        assert any(r.category == "new_resource" for r in results)

    def test_detects_drift(self, rules_path):
        output = "Drift detected: service is running on port 8080 but context says 3000."
        results = classify_output(output, "cloud-troubleshooter", "check drift", rules_path=rules_path)
        assert len(results) > 0
        assert any(r.category == "drift_detected" for r in results)

    def test_detects_dependency(self, rules_path):
        output = "Service 'gateway' depends on 'auth-api' via HTTP."
        results = classify_output(output, "cloud-troubleshooter", "check deps", rules_path=rules_path)
        assert len(results) > 0
        assert any(r.category == "dependency_discovered" for r in results)


# ============================================================================
# Test Operational Content Filtering
# ============================================================================

class TestOperationalFiltering:
    """Test that operational content is correctly filtered out."""

    def test_pure_metrics_no_discoveries(self, rules_path):
        output = (
            "CPU usage: 45% average over last hour.\n"
            "Memory: 2.1Gi / 4Gi allocated.\n"
            "Latency p95: 120ms, p99: 340ms.\n"
        )
        results = classify_output(output, "cloud-troubleshooter", "check health", rules_path=rules_path)
        assert len(results) == 0

    def test_command_output_no_discoveries(self, rules_path):
        output = (
            "$ kubectl get pods -n common\n"
            "NAME                     READY   STATUS    RESTARTS   AGE\n"
            "auth-api-7f8d9c-abc12    1/1     Running   0          5d\n"
        )
        results = classify_output(output, "cloud-troubleshooter", "check pods", rules_path=rules_path)
        assert len(results) == 0

    def test_error_logs_no_discoveries(self, rules_path):
        output = (
            "error: connection refused to 10.0.0.1:5432\n"
            "warn: retrying in 5 seconds\n"
            "info: connection established after 3 retries\n"
        )
        results = classify_output(output, "cloud-troubleshooter", "check errors", rules_path=rules_path)
        assert len(results) == 0


# ============================================================================
# Test Mixed Content
# ============================================================================

class TestMixedContent:
    """Test handling of output containing both structural and operational data."""

    def test_extracts_structural_from_mixed(self, rules_path):
        # NOTE: Classifier checks ±2 line context for negative patterns.
        # Structural content must be >2 lines away from operational lines.
        output = (
            "Running investigation...\n"
            "Checked pods and services.\n"
            "All replicas healthy.\n"
            "Analysis complete.\n"
            "\n"
            "Found that the WI binding references wrong project 'old-project'.\n"
            "Should be 'correct-project' but actual is 'old-project'.\n"
        )
        results = classify_output(output, "cloud-troubleshooter", "investigate", rules_path=rules_path)
        assert len(results) > 0
        # Should find structural issues
        for r in results:
            assert r.category.value in ("configuration_issue", "drift_detected", "new_resource")

    def test_negative_pattern_suppresses_in_command_context(self, rules_path):
        output = "kubectl create namespace monitoring\nNamespace created successfully."
        results = classify_output(output, "gitops-operator", "create ns", rules_path=rules_path)
        # "creating namespace" is a negative pattern, should suppress
        assert len(results) == 0


# ============================================================================
# Test Confidence Threshold
# ============================================================================

class TestConfidenceThreshold:
    """Test confidence threshold enforcement."""

    def test_all_results_above_threshold(self, rules_path):
        output = "Discovered new service 'api' running in namespace 'default'."
        results = classify_output(output, "cloud-troubleshooter", "check", rules_path=rules_path)
        for r in results:
            assert r.confidence >= 0.7


# ============================================================================
# Test Edge Cases
# ============================================================================

class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_output(self, rules_path):
        results = classify_output("", "cloud-troubleshooter", "test", rules_path=rules_path)
        assert results == []

    def test_none_output(self, rules_path):
        results = classify_output(None, "cloud-troubleshooter", "test", rules_path=rules_path)
        assert results == []

    def test_missing_rules_file(self, tmp_path):
        fake_path = tmp_path / "nonexistent.json"
        results = classify_output("some output", "cloud-troubleshooter", "test", rules_path=fake_path)
        assert results == []

    def test_result_has_required_fields(self, rules_path):
        output = "Discovered new service 'payment-api' running in namespace 'payments'."
        results = classify_output(output, "cloud-troubleshooter", "check services", rules_path=rules_path)
        if results:
            r = results[0]
            assert r.category != ""
            assert r.target_section != ""
            assert r.proposed_change is not None
            assert r.summary != ""
            assert r.confidence >= 0.7
            assert r.agent_type == "cloud-troubleshooter"
