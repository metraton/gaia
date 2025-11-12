"""
Unit tests for approval_gate.py

Tests the Approval Gate enforcement mechanism that ensures no realization
occurs without explicit user approval.
"""

import unittest
import json
import os
import tempfile
import sys
import importlib
from datetime import datetime

# Add parent directory to path to import approval_gate
# Resolves: tests/validators/test_approval_gate.py → tools/
test_dir = os.path.dirname(os.path.abspath(__file__))
tools_path = os.path.join(test_dir, '../../tools')
sys.path.insert(0, tools_path)

# Import from reorganized tools structure
validation = importlib.import_module("4-validation")
ApprovalGate = validation.ApprovalGate
request_approval = validation.request_approval
process_approval_response = validation.process_approval_response


class TestApprovalGate(unittest.TestCase):
    """Test cases for ApprovalGate class."""

    def setUp(self):
        """Set up test fixtures."""
        # Create temporary log directory
        self.temp_dir = tempfile.mkdtemp()
        self.log_path = os.path.join(self.temp_dir, "approvals.jsonl")

        # Create ApprovalGate instance with custom log path
        self.gate = ApprovalGate()
        self.gate.approval_log_path = self.log_path

        # Sample realization package
        self.realization_package = {
            "files": [
                {"path": "releases/pg-non-prod/admin-ui/release.yaml", "action": "create"},
                {"path": "releases/pg-non-prod/query-api/release.yaml", "action": "create"},
                {"path": "releases/pg-non-prod/admin-api/release.yaml", "action": "create"}
            ],
            "git_operations": {
                "commit_message": "feat(helmrelease): add Phase 3.3 services",
                "branch": "main",
                "remote": "origin"
            },
            "resources_affected": {
                "HelmReleases": ["pg-admin-ui", "pg-query-api", "pg-admin-api"]
            }
        }

    def tearDown(self):
        """Clean up test fixtures."""
        # Remove temporary log file
        if os.path.exists(self.log_path):
            os.remove(self.log_path)
        os.rmdir(self.temp_dir)

    def test_generate_summary(self):
        """Test that summary generation includes all key information."""
        summary = self.gate.generate_summary(self.realization_package)

        # Check that summary contains key sections
        self.assertIn("REALIZATION PACKAGE", summary)
        self.assertIn("Archivos a crear/modificar:", summary)
        self.assertIn("Git Operations:", summary)
        self.assertIn("Recursos Afectados", summary)

        # Check specific details
        self.assertIn("admin-ui", summary)
        self.assertIn("feat(helmrelease)", summary)
        self.assertIn("git push", summary)

    def test_generate_approval_question(self):
        """Test that approval question is correctly structured."""
        question_config = self.gate.generate_approval_question(
            self.realization_package,
            "gitops-operator",
            "Phase 3.3"
        )

        # Check structure
        self.assertIn("questions", question_config)
        self.assertEqual(len(question_config["questions"]), 1)

        question = question_config["questions"][0]

        # Check question fields
        self.assertIn("question", question)
        self.assertIn("header", question)
        self.assertIn("multiSelect", question)
        self.assertIn("options", question)

        # Check header
        self.assertEqual(question["header"], "Approval")

        # Check multiSelect is False
        self.assertFalse(question["multiSelect"])

        # Check options (should have exactly 2: Aprobar and Rechazar)
        self.assertEqual(len(question["options"]), 2)

        labels = [opt["label"] for opt in question["options"]]
        self.assertIn("✅ Aprobar y ejecutar", labels)
        self.assertIn("❌ Rechazar", labels)

    def test_get_critical_operations_git_only(self):
        """Test extraction of critical operations (git only)."""
        ops = self.gate._get_critical_operations(self.realization_package)
        self.assertEqual(ops, "git push origin main")

    def test_get_critical_operations_multiple(self):
        """Test extraction with multiple operation types."""
        package = {
            **self.realization_package,
            "kubectl_operations": ["apply -f release.yaml"],
            "terraform_operations": {"command": "apply"}
        }

        ops = self.gate._get_critical_operations(package)

        # Should contain all operation types
        self.assertIn("git push", ops)
        self.assertIn("kubectl apply", ops)
        self.assertIn("terraform apply", ops)

    def test_get_operation_count(self):
        """Test counting of total operations."""
        count = self.gate._get_operation_count(self.realization_package)

        # 3 files + 2 git operations (commit + push) = 5
        self.assertEqual(count, 5)

    def test_validate_approval_response_approved(self):
        """Test validation of approved response."""
        validation = self.gate.validate_approval_response("✅ Aprobar y ejecutar")

        self.assertTrue(validation["approved"])
        self.assertEqual(validation["action"], "proceed_to_realization")
        self.assertIn("Procediendo", validation["message"])

    def test_validate_approval_response_rejected(self):
        """Test validation of rejected response."""
        validation = self.gate.validate_approval_response("❌ Rechazar")

        self.assertFalse(validation["approved"])
        self.assertEqual(validation["action"], "halt_workflow")
        self.assertIn("rechazada", validation["message"])

    def test_validate_approval_response_custom(self):
        """Test validation of custom (Other) response."""
        validation = self.gate.validate_approval_response("Wait, let me review first")

        self.assertFalse(validation["approved"])
        self.assertEqual(validation["action"], "clarify_with_user")
        self.assertIn("no estándar", validation["message"])
        self.assertEqual(validation["user_input"], "Wait, let me review first")

    def test_log_approval(self):
        """Test that approval decisions are logged correctly."""
        self.gate.log_approval(
            realization_package=self.realization_package,
            user_response="✅ Aprobar y ejecutar",
            approved=True,
            agent_name="gitops-operator",
            phase="Phase 3.3"
        )

        # Check that log file was created
        self.assertTrue(os.path.exists(self.log_path))

        # Read and parse log entry
        with open(self.log_path, 'r') as f:
            log_entry = json.loads(f.readline())

        # Verify log entry contents
        self.assertEqual(log_entry["agent"], "gitops-operator")
        self.assertEqual(log_entry["phase"], "Phase 3.3")
        self.assertTrue(log_entry["approved"])
        self.assertEqual(log_entry["user_response"], "✅ Aprobar y ejecutar")
        self.assertEqual(log_entry["files_count"], 3)
        self.assertIn("git push", log_entry["operations"])

        # Verify timestamp format
        datetime.fromisoformat(log_entry["timestamp"])  # Should not raise

    def test_log_multiple_approvals(self):
        """Test that multiple approvals are appended to log."""
        # First approval
        self.gate.log_approval(
            self.realization_package,
            "✅ Aprobar y ejecutar",
            True,
            "gitops-operator",
            "Phase 3.3"
        )

        # Second approval
        self.gate.log_approval(
            self.realization_package,
            "❌ Rechazar",
            False,
            "terraform-architect",
            "Phase 3.1"
        )

        # Read all log entries
        with open(self.log_path, 'r') as f:
            lines = f.readlines()

        self.assertEqual(len(lines), 2)

        # Parse entries
        entry1 = json.loads(lines[0])
        entry2 = json.loads(lines[1])

        self.assertTrue(entry1["approved"])
        self.assertFalse(entry2["approved"])
        self.assertEqual(entry1["agent"], "gitops-operator")
        self.assertEqual(entry2["agent"], "terraform-architect")


class TestConvenienceFunctions(unittest.TestCase):
    """Test cases for module-level convenience functions."""

    def setUp(self):
        """Set up test fixtures."""
        self.realization_package = {
            "files": [
                {"path": "infrastructure/main.tf", "action": "modify"}
            ],
            "terraform_operations": {
                "command": "apply",
                "path": "infrastructure/"
            }
        }

    def test_request_approval(self):
        """Test request_approval convenience function."""
        result = request_approval(
            realization_package=self.realization_package,
            agent_name="terraform-architect",
            phase="Phase 3.1"
        )

        # Check returned structure
        self.assertIn("summary", result)
        self.assertIn("question_config", result)
        self.assertIn("gate_instance", result)

        # Check that summary is a string
        self.assertIsInstance(result["summary"], str)

        # Check that question_config has correct structure
        self.assertIn("questions", result["question_config"])

        # Check that gate_instance is an ApprovalGate
        self.assertIsInstance(result["gate_instance"], ApprovalGate)

    def test_process_approval_response_approved(self):
        """Test process_approval_response with approved response."""
        # First request approval
        approval_data = request_approval(
            realization_package=self.realization_package,
            agent_name="terraform-architect",
            phase="Phase 3.1"
        )

        # Override log path to temp file
        temp_log = tempfile.NamedTemporaryFile(delete=False, suffix='.jsonl')
        temp_log.close()
        approval_data["gate_instance"].approval_log_path = temp_log.name

        try:
            # Process approval
            validation = process_approval_response(
                gate_instance=approval_data["gate_instance"],
                user_response="✅ Aprobar y ejecutar",
                realization_package=self.realization_package,
                agent_name="terraform-architect",
                phase="Phase 3.1"
            )

            # Check validation result
            self.assertTrue(validation["approved"])
            self.assertEqual(validation["action"], "proceed_to_realization")

            # Verify log was created
            with open(temp_log.name, 'r') as f:
                log_entry = json.loads(f.readline())

            self.assertEqual(log_entry["agent"], "terraform-architect")
            self.assertTrue(log_entry["approved"])

        finally:
            # Clean up temp file
            os.remove(temp_log.name)

    def test_process_approval_response_rejected(self):
        """Test process_approval_response with rejected response."""
        approval_data = request_approval(
            realization_package=self.realization_package,
            agent_name="terraform-architect",
            phase="Phase 3.1"
        )

        # Override log path
        temp_log = tempfile.NamedTemporaryFile(delete=False, suffix='.jsonl')
        temp_log.close()
        approval_data["gate_instance"].approval_log_path = temp_log.name

        try:
            validation = process_approval_response(
                gate_instance=approval_data["gate_instance"],
                user_response="❌ Rechazar",
                realization_package=self.realization_package,
                agent_name="terraform-architect",
                phase="Phase 3.1"
            )

            # Check validation result
            self.assertFalse(validation["approved"])
            self.assertEqual(validation["action"], "halt_workflow")

            # Verify log was created with rejected status
            with open(temp_log.name, 'r') as f:
                log_entry = json.loads(f.readline())

            self.assertFalse(log_entry["approved"])

        finally:
            os.remove(temp_log.name)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions."""

    def test_empty_realization_package(self):
        """Test handling of empty realization package."""
        gate = ApprovalGate()
        empty_package = {}

        # Should not crash
        summary = gate.generate_summary(empty_package)
        self.assertIsInstance(summary, str)

        ops = gate._get_critical_operations(empty_package)
        self.assertEqual(ops, "cambios al repositorio")

        count = gate._get_operation_count(empty_package)
        self.assertEqual(count, 0)

    def test_realization_package_with_validation_warnings(self):
        """Test summary includes validation warnings if present."""
        gate = ApprovalGate()
        package = {
            "files": [{"path": "test.yaml", "action": "create"}],
            "validation_results": {
                "status": "passed_with_warnings",
                "warnings": [
                    "Image tag 'latest' is not recommended",
                    "Resource limits not set"
                ]
            }
        }

        summary = gate.generate_summary(package)

        # Should include validation section
        self.assertIn("Pre-Deployment Validation", summary)
        self.assertIn("Warnings:", summary)
        self.assertIn("Image tag", summary)

    def test_realization_package_with_estimated_impact(self):
        """Test summary includes estimated impact if present."""
        gate = ApprovalGate()
        package = {
            "files": [{"path": "test.yaml", "action": "create"}],
            "estimated_impact": {
                "downtime": "~5 minutes",
                "risk_level": "Medium"
            }
        }

        summary = gate.generate_summary(package)

        # Should include impact section
        self.assertIn("Estimated Impact", summary)
        self.assertIn("Downtime:", summary)
        self.assertIn("Risk Level:", summary)

    def test_many_files_truncated_in_summary(self):
        """Test that summary truncates long file lists."""
        gate = ApprovalGate()

        # Create package with 15 files
        files = [{"path": f"file{i}.yaml", "action": "create"} for i in range(15)]
        package = {"files": files}

        summary = gate.generate_summary(package)

        # Should show "... y X archivos más"
        self.assertIn("y 5 archivos más", summary)
        self.assertIn("15", summary)  # Total count


if __name__ == '__main__':
    unittest.main()
