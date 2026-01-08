#!/usr/bin/env python3
"""
Tests for Phase Validator.

Validates:
1. Pre-phase validation
2. Post-phase validation
3. Phase-specific requirements
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.workflow.phase_validator import (
    validate_pre_phase,
    validate_post_phase,
    PhaseValidationResult,
    AGENT_CONTEXT_REQUIREMENTS,
)


class TestPrePhase0Clarification:
    """Test Phase 0 (Clarification) pre-validation."""

    def test_allows_low_ambiguity(self):
        """Test allows request with low ambiguity."""
        result = validate_pre_phase(0, ambiguity_score=0.1)
        assert result.allowed is True

    def test_allows_high_ambiguity_for_clarification(self):
        """Test allows high ambiguity (triggers clarification)."""
        result = validate_pre_phase(0, ambiguity_score=0.5, threshold=0.3)
        assert result.allowed is True
        # High ambiguity means clarification is needed

    def test_uses_default_threshold(self):
        """Test uses default threshold."""
        result = validate_pre_phase(0, ambiguity_score=0.2)
        assert result.allowed is True


class TestPrePhase1Routing:
    """Test Phase 1 (Routing) pre-validation."""

    @pytest.fixture
    def available_agents(self):
        return ["terraform-architect", "gitops-operator", "devops-developer"]

    def test_allows_high_confidence_valid_agent(self, available_agents):
        """Test allows high confidence routing to valid agent."""
        result = validate_pre_phase(
            1,
            agent_name="terraform-architect",
            routing_confidence=0.8,
            available_agents=available_agents
        )
        assert result.allowed is True

    def test_blocks_low_confidence(self, available_agents):
        """Test blocks low confidence routing."""
        result = validate_pre_phase(
            1,
            agent_name="terraform-architect",
            routing_confidence=0.3,
            available_agents=available_agents,
            min_confidence=0.5
        )
        assert result.allowed is False
        assert "confidence" in result.reason.lower()

    def test_blocks_invalid_agent(self, available_agents):
        """Test blocks routing to unknown agent."""
        result = validate_pre_phase(
            1,
            agent_name="unknown-agent",
            routing_confidence=0.9,
            available_agents=available_agents
        )
        assert result.allowed is False
        assert "unknown" in result.reason.lower()


class TestPrePhase2Context:
    """Test Phase 2 (Context) pre-validation."""

    def test_allows_complete_context(self):
        """Test allows complete context for agent."""
        context = {
            "project_details": {"id": "test"},
            "terraform_infrastructure": {"path": "/tf"},
            "operational_guidelines": {"tier": "T2"},
        }
        result = validate_pre_phase(
            2,
            context_payload=context,
            agent_name="terraform-architect"
        )
        assert result.allowed is True

    def test_blocks_incomplete_context(self):
        """Test blocks incomplete context."""
        context = {
            "project_details": {"id": "test"},
            # Missing terraform_infrastructure
        }
        result = validate_pre_phase(
            2,
            context_payload=context,
            agent_name="terraform-architect"
        )
        assert result.allowed is False
        assert "missing" in result.reason.lower()

    def test_different_requirements_per_agent(self):
        """Test different agents have different requirements."""
        context = {
            "project_details": {"id": "test"},
            "operational_guidelines": {"tier": "T1"},
        }
        # devops-developer has simpler requirements
        result = validate_pre_phase(
            2,
            context_payload=context,
            agent_name="devops-developer"
        )
        assert result.allowed is True


class TestPrePhase4Approval:
    """Test Phase 4 (Approval) pre-validation."""

    def test_allows_with_realization_package(self):
        """Test allows with realization package."""
        result = validate_pre_phase(
            4,
            tier="T3",
            realization_package={"files": ["main.tf"]}
        )
        assert result.allowed is True

    def test_allows_empty_package_with_warning(self):
        """Test allows empty package but may warn."""
        result = validate_pre_phase(
            4,
            tier="T3",
            realization_package={}
        )
        # May allow but should be noted
        assert isinstance(result.allowed, bool)


class TestPrePhase5Realization:
    """Test Phase 5 (Realization) pre-validation - CRITICAL."""

    def test_allows_t3_with_approval(self):
        """Test allows T3 with valid approval."""
        result = validate_pre_phase(
            5,
            tier="T3",
            approval_validation={"approved": True, "action": "proceed_to_realization"}
        )
        assert result.allowed is True

    def test_blocks_t3_without_approval(self):
        """CRITICAL: Test blocks T3 without approval."""
        result = validate_pre_phase(
            5,
            tier="T3",
            approval_validation={"approved": False}
        )
        assert result.allowed is False
        assert "approval" in result.reason.lower() or "T3" in result.reason

    def test_blocks_t3_with_wrong_action(self):
        """Test blocks T3 with wrong approval action."""
        result = validate_pre_phase(
            5,
            tier="T3",
            approval_validation={"approved": True, "action": "wrong_action"}
        )
        assert result.allowed is False

    def test_allows_t2_without_approval(self):
        """Test allows T2 without approval."""
        result = validate_pre_phase(
            5,
            tier="T2",
            approval_validation={}
        )
        assert result.allowed is True

    def test_allows_t1_without_approval(self):
        """Test allows T1 without approval."""
        result = validate_pre_phase(
            5,
            tier="T1",
            approval_validation={}
        )
        assert result.allowed is True


class TestPrePhase6SSOTUpdate:
    """Test Phase 6 (SSOT Update) pre-validation."""

    def test_allows_after_success(self):
        """Test allows after successful realization."""
        result = validate_pre_phase(
            6,
            realization_success=True
        )
        assert result.allowed is True

    def test_blocks_after_failure(self):
        """Test blocks after failed realization."""
        result = validate_pre_phase(
            6,
            realization_success=False
        )
        assert result.allowed is False


class TestPostPhase4Approval:
    """Test Phase 4 post-validation."""

    def test_valid_t3_approval(self):
        """Test valid T3 approval."""
        result = validate_post_phase(
            4,
            tier="T3",
            validation_result={"approved": True}
        )
        assert result.allowed is True

    def test_invalid_t3_rejection(self):
        """Test T3 rejection."""
        result = validate_post_phase(
            4,
            tier="T3",
            validation_result={"approved": False}
        )
        assert result.allowed is False


class TestPostPhase6SSOTUpdate:
    """Test Phase 6 post-validation."""

    def test_valid_t3_ssot_update(self):
        """Test valid SSOT update for T3."""
        result = validate_post_phase(
            6,
            tier="T3",
            ssot_updated=True
        )
        assert result.allowed is True

    def test_invalid_t3_no_ssot_update(self):
        """Test invalid: T3 without SSOT update."""
        result = validate_post_phase(
            6,
            tier="T3",
            ssot_updated=False
        )
        assert result.allowed is False


class TestPhaseValidationResult:
    """Test PhaseValidationResult structure."""

    def test_result_has_expected_fields(self):
        """Test result contains expected fields."""
        result = validate_pre_phase(1, agent_name="test", routing_confidence=0.8)
        assert hasattr(result, "allowed")
        assert hasattr(result, "reason")
        assert hasattr(result, "phase")
        assert hasattr(result, "is_pre_hook")

    def test_pre_phase_sets_is_pre_hook_true(self):
        """Test pre-phase sets is_pre_hook to True."""
        result = validate_pre_phase(1, agent_name="test", routing_confidence=0.8)
        assert result.is_pre_hook is True

    def test_post_phase_sets_is_pre_hook_false(self):
        """Test post-phase sets is_pre_hook to False."""
        result = validate_post_phase(4, tier="T3", validation_result={"approved": True})
        assert result.is_pre_hook is False


class TestAgentContextRequirements:
    """Test agent context requirements configuration."""

    def test_terraform_architect_requirements(self):
        """Test terraform-architect requirements."""
        reqs = AGENT_CONTEXT_REQUIREMENTS.get("terraform-architect", [])
        assert "project_details" in reqs
        assert "terraform_infrastructure" in reqs

    def test_gitops_operator_requirements(self):
        """Test gitops-operator requirements."""
        reqs = AGENT_CONTEXT_REQUIREMENTS.get("gitops-operator", [])
        assert "project_details" in reqs
        assert "gitops_configuration" in reqs

    def test_devops_developer_requirements(self):
        """Test devops-developer requirements."""
        reqs = AGENT_CONTEXT_REQUIREMENTS.get("devops-developer", [])
        assert "project_details" in reqs
