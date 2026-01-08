#!/usr/bin/env python3
"""
Test the complete WorkflowEnforcer integration with pre_tool_use.py
Tests all 6 phases of the workflow enforcement.
"""

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools" / "0-guards"))


class TestWorkflowEnforcerIntegration:
    """Test complete workflow enforcer integration with modular hooks."""

    def test_phase_1_invalid_agent_blocked(self):
        """Test case 1: Phase 1 - Agent must exist"""
        from modules.tools.task_validator import TaskValidator

        validator = TaskValidator()

        result = validator.validate({
            "subagent_type": "invalid-agent",
            "prompt": "Do something",
            "description": "Test task"
        })

        assert not result.allowed, "Invalid agent should be blocked"
        assert "Unknown agent" in result.reason or "unknown" in result.reason.lower(), \
            f"Invalid agent not blocked properly. Result: allowed={result.allowed}, reason={result.reason}"

    def test_phase_2_missing_context_warning(self):
        """Test case 2: Phase 2 - Context provisioning warning"""
        from modules.tools.task_validator import TaskValidator

        validator = TaskValidator()

        result = validator.validate({
            "subagent_type": "terraform-architect",
            "prompt": "Run terraform plan",  # No context
            "description": "Plan infrastructure"
        })

        # Should be allowed but without context flag
        assert result.allowed, f"Should not block for missing context: {result.reason}"
        # Check that context was not detected
        assert not result.has_context, "Should detect missing context"

    def test_phase_4_t3_without_approval_blocked(self):
        """Test case 3: Phase 4 - T3 operations require approval"""
        from modules.tools.task_validator import TaskValidator

        validator = TaskValidator()

        # T3 without approval - should be blocked
        result = validator.validate({
            "subagent_type": "terraform-architect",
            "prompt": "# Project Context\n\nRun terraform apply to production",
            "description": "Apply terraform changes"
        })

        assert not result.allowed, "T3 operation should be blocked without approval"
        assert "Phase 4" in result.reason or "approval" in result.reason.lower(), \
            f"T3 should be blocked without approval: {result.reason}"

    def test_phase_4_t3_with_approval_allowed(self):
        """Test case 3b: T3 with approval - should be allowed"""
        from modules.tools.task_validator import TaskValidator

        validator = TaskValidator()

        result = validator.validate({
            "subagent_type": "terraform-architect",
            "prompt": "# Project Context\n\nUser approval received. Run terraform apply to production",
            "description": "Apply terraform changes"
        })

        assert result.allowed, f"T3 should be allowed with approval: {result.reason}"

    def test_phase_5_realization_with_plan_allowed(self):
        """Test case 4: Phase 5 - Realization checks"""
        from modules.tools.task_validator import TaskValidator

        validator = TaskValidator()

        result = validator.validate({
            "subagent_type": "gitops-operator",
            "prompt": "# Project Context\n\nPhase 5: Realization\n\nPlan: Deploy application\nSteps: 1. Update manifests",
            "description": "Execute deployment"
        })

        assert result.allowed, f"Realization should be allowed with plan: {result.reason}"

    def test_phase_6_ssot_tracking(self):
        """Test case 5: Phase 6 - SSOT tracking through Task validator"""
        from modules.tools.task_validator import TaskValidator

        validator = TaskValidator()

        # Execute T3 with approval
        result = validator.validate({
            "subagent_type": "terraform-architect",
            "prompt": "User approval received. Apply terraform to create GKE cluster",
            "description": "Create production cluster"
        })

        # T3 operation should be marked in result
        assert result.is_t3_operation, "Should detect T3 operation"
        assert result.has_approval, "Should detect approval"
        assert result.allowed, "T3 with approval should be allowed"


class TestAllGuardsAvailable:
    """Test that all phase guards are available"""

    def test_all_guards_exist_and_work(self):
        """Test that all guard methods exist and return correct format"""
        try:
            from workflow_enforcer import WorkflowEnforcer

            enforcer = WorkflowEnforcer()

            # Test each guard method exists
            guards_to_test = [
                ("guard_phase_0_ambiguity_threshold", [0.5]),
                ("guard_phase_1_agent_exists", ["terraform-architect", ["terraform-architect"]]),
                ("guard_phase_1_routing_confidence", [0.8]),
                ("guard_phase_2_context_completeness", [
                    {"contract": {"project_details": {}}},  # context_payload
                    ["project_details"]  # required_sections
                ]),
                ("guard_phase_4_approval_mandatory", ["T3", True]),
                ("guard_phase_5_planning_complete", [{"agent": "test", "plan": "Plan output"}]),  # realization_package
                ("guard_phase_6_ssot_update_after_t3", ["T3", True])  # tier, ssot_updated
            ]

            for method_name, test_args in guards_to_test:
                assert hasattr(enforcer, method_name), f"{method_name}: Not found"
                method = getattr(enforcer, method_name)
                result = method(*test_args)
                assert isinstance(result, tuple) and len(result) == 2, \
                    f"{method_name}: Invalid return format"

        except ImportError as e:
            pytest.fail(f"Could not import WorkflowEnforcer: {e}")


class TestGuardViolations:
    """Test that guard violations properly block operations"""

    def test_high_ambiguity_blocked(self):
        """Test 1: High ambiguity should fail"""
        from workflow_enforcer import WorkflowEnforcer

        enforcer = WorkflowEnforcer()

        passed, reason = enforcer.guard_phase_0_ambiguity_threshold(0.8, threshold=0.3)
        assert not passed, "High ambiguity should be blocked"
        assert "Guard Violation" in reason, "Should contain Guard Violation message"

    def test_low_routing_confidence_blocked(self):
        """Test 2: Low routing confidence should fail"""
        from workflow_enforcer import WorkflowEnforcer

        enforcer = WorkflowEnforcer()

        passed, reason = enforcer.guard_phase_1_routing_confidence(0.2, min_confidence=0.5)
        assert not passed, "Low routing confidence should be blocked"
        assert "below minimum" in reason, "Should mention below minimum"

    def test_t3_without_approval_blocked(self):
        """Test 3: T3 without approval should fail"""
        from workflow_enforcer import WorkflowEnforcer

        enforcer = WorkflowEnforcer()

        passed, reason = enforcer.guard_phase_4_approval_mandatory("T3", approval_received=False)
        assert not passed, "T3 without approval should be blocked"
        assert "MANDATORY" in reason, "Should mention MANDATORY"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
