"""
Test suite for workflow_enforcer.py guards
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "tools" / "0-guards"))

from workflow_enforcer import WorkflowEnforcer, GuardViolation


class TestPhase4ApprovalGuards:
    """Test critical Phase 4 approval guards"""

    @pytest.fixture
    def enforcer(self):
        return WorkflowEnforcer()

    def test_t3_without_approval_blocks(self, enforcer):
        """T3 operation without approval MUST be blocked"""
        with pytest.raises(GuardViolation):
            enforcer.enforce(
                "guard_phase_4_approval_mandatory",
                tier="T3",
                approval_received=False
            )

    def test_t3_with_approval_passes(self, enforcer):
        """T3 operation with approval MUST pass"""
        passed, reason = enforcer.enforce(
            "guard_phase_4_approval_mandatory",
            tier="T3",
            approval_received=True
        )
        assert passed, f"T3 with approval should pass: {reason}"

    def test_t0_without_approval_passes(self, enforcer):
        """T0 operation without approval MUST pass"""
        passed, reason = enforcer.enforce(
            "guard_phase_4_approval_mandatory",
            tier="T0",
            approval_received=False
        )
        assert passed, f"T0 without approval should pass: {reason}"


class TestRoutingGuards:
    """Test Phase 1 routing guards"""

    @pytest.fixture
    def enforcer(self):
        return WorkflowEnforcer()

    def test_low_confidence_blocks(self, enforcer):
        """Low routing confidence MUST be blocked"""
        with pytest.raises(GuardViolation):
            enforcer.enforce(
                "guard_phase_1_routing_confidence",
                routing_confidence=0.3,
                min_confidence=0.5
            )

    def test_high_confidence_passes(self, enforcer):
        """High routing confidence MUST pass"""
        passed, reason = enforcer.enforce(
            "guard_phase_1_routing_confidence",
            routing_confidence=0.85,
            min_confidence=0.5
        )
        assert passed, f"High confidence should pass: {reason}"

    def test_nonexistent_agent_blocks(self, enforcer):
        """Routing to nonexistent agent MUST be blocked"""
        with pytest.raises(GuardViolation):
            enforcer.enforce(
                "guard_phase_1_agent_exists",
                agent_name="fake-agent",
                available_agents=["gitops-operator", "terraform-architect"]
            )


class TestContextGuards:
    """Test Phase 2 context guards"""

    @pytest.fixture
    def enforcer(self):
        return WorkflowEnforcer()

    def test_missing_context_sections_blocks(self, enforcer):
        """Missing required context sections MUST be blocked"""
        context_payload = {
            "contract": {
                "project_details": {}
                # Missing operational_guidelines
            }
        }

        with pytest.raises(GuardViolation):
            enforcer.enforce(
                "guard_phase_2_context_completeness",
                context_payload=context_payload,
                required_sections=["project_details", "operational_guidelines"]
            )

    def test_complete_context_passes(self, enforcer):
        """Complete context payload MUST pass"""
        context_payload = {
            "contract": {
                "project_details": {},
                "operational_guidelines": {}
            }
        }

        passed, reason = enforcer.enforce(
            "guard_phase_2_context_completeness",
            context_payload=context_payload,
            required_sections=["project_details", "operational_guidelines"]
        )
        assert passed, f"Complete context should pass: {reason}"


class TestSSOTGuards:
    """Test Phase 6 SSOT update guards"""

    @pytest.fixture
    def enforcer(self):
        return WorkflowEnforcer()

    def test_t3_without_ssot_update_blocks(self, enforcer):
        """T3 operation without SSOT update MUST be blocked"""
        with pytest.raises(GuardViolation):
            enforcer.enforce(
                "guard_phase_6_ssot_update_after_t3",
                tier="T3",
                ssot_updated=False
            )

    def test_t3_with_ssot_update_passes(self, enforcer):
        """T3 operation with SSOT update MUST pass"""
        passed, reason = enforcer.enforce(
            "guard_phase_6_ssot_update_after_t3",
            tier="T3",
            ssot_updated=True
        )
        assert passed, f"T3 with SSOT update should pass: {reason}"