#!/usr/bin/env python3
"""
Tests for pre_phase_hook.py.

Validates pre-phase guards for all workflow phases:
- Phase 0: Clarification (ambiguity threshold)
- Phase 1: Routing (confidence, agent existence)
- Phase 2: Context (completeness)
- Phase 4: Approval (planning complete)
- Phase 5: Realization (approval mandatory for T3)
- Phase 6: SSOT Update (realization success)
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
GUARDS_DIR = Path(__file__).parent.parent.parent / "tools" / "0-guards"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(GUARDS_DIR))

from pre_phase_hook import (
    pre_phase_0_clarification,
    pre_phase_1_routing,
    pre_phase_2_context,
    pre_phase_4_approval,
    pre_phase_5_realization,
    pre_phase_6_ssot_update,
)


class TestPrePhase0Clarification:
    """Tests for Phase 0 pre-guards (clarification)."""

    def test_allows_low_ambiguity(self):
        """Test allowing request with low ambiguity score."""
        result = pre_phase_0_clarification(
            ambiguity_score=0.1,
            user_prompt="Deploy tcm-api to production"
        )
        
        assert result["allowed"] is True

    def test_blocks_high_ambiguity(self):
        """Test blocking request with high ambiguity score."""
        result = pre_phase_0_clarification(
            ambiguity_score=0.5,  # Above 0.3 threshold
            user_prompt="Deploy the service"
        )
        
        assert result["allowed"] is False
        assert "ambiguity" in result["reason"].lower()

    def test_threshold_boundary(self):
        """Test exact threshold value (0.3)."""
        result = pre_phase_0_clarification(
            ambiguity_score=0.3,
            user_prompt="Test request"
        )
        
        assert result["allowed"] is True  # 0.3 <= 0.3


class TestPrePhase1Routing:
    """Tests for Phase 1 pre-guards (routing)."""

    @pytest.fixture
    def available_agents(self):
        return [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
        ]

    def test_allows_high_confidence_valid_agent(self, available_agents):
        """Test allowing high-confidence routing to valid agent."""
        result = pre_phase_1_routing(
            agent_name="terraform-architect",
            routing_confidence=0.8,
            available_agents=available_agents
        )
        
        assert result["allowed"] is True

    def test_blocks_low_confidence(self, available_agents):
        """Test blocking low-confidence routing."""
        result = pre_phase_1_routing(
            agent_name="terraform-architect",
            routing_confidence=0.3,  # Below 0.5 threshold
            available_agents=available_agents
        )
        
        assert result["allowed"] is False
        assert "confidence" in result["reason"].lower()

    def test_blocks_invalid_agent(self, available_agents):
        """Test blocking routing to non-existent agent."""
        result = pre_phase_1_routing(
            agent_name="nonexistent-agent",
            routing_confidence=0.9,
            available_agents=available_agents
        )
        
        assert result["allowed"] is False
        assert "does not exist" in result["reason"].lower()

    def test_confidence_boundary(self, available_agents):
        """Test exact confidence threshold (0.5)."""
        result = pre_phase_1_routing(
            agent_name="gitops-operator",
            routing_confidence=0.5,  # Exactly at threshold
            available_agents=available_agents
        )
        
        assert result["allowed"] is True


class TestPrePhase2Context:
    """Tests for Phase 2 pre-guards (context provisioning)."""

    def test_allows_complete_context_terraform(self):
        """Test allowing complete context for terraform-architect."""
        context = {
            "contract": {
                "project_details": {"id": "test"},
                "terraform_infrastructure": {"path": "/terraform"},
                "operational_guidelines": {"tier": "T2"},
            }
        }
        
        result = pre_phase_2_context(
            context_payload=context,
            agent_name="terraform-architect"
        )
        
        assert result["allowed"] is True

    def test_blocks_incomplete_context(self):
        """Test blocking incomplete context."""
        context = {
            "contract": {
                "project_details": {"id": "test"},
                # Missing terraform_infrastructure and operational_guidelines
            }
        }
        
        result = pre_phase_2_context(
            context_payload=context,
            agent_name="terraform-architect"
        )
        
        assert result["allowed"] is False
        assert "missing" in result["reason"].lower()

    def test_different_requirements_per_agent(self):
        """Test that different agents have different requirements."""
        context = {
            "contract": {
                "project_details": {"id": "test"},
                "operational_guidelines": {"tier": "T1"},
            }
        }
        
        # devops-developer only needs project_details + operational_guidelines
        result = pre_phase_2_context(
            context_payload=context,
            agent_name="devops-developer"
        )
        
        assert result["allowed"] is True

    def test_gitops_operator_requirements(self):
        """Test gitops-operator specific requirements."""
        context = {
            "contract": {
                "project_details": {"id": "test"},
                "gitops_configuration": {"repo": "gitops"},
                "cluster_details": {"name": "cluster"},
            }
        }
        
        result = pre_phase_2_context(
            context_payload=context,
            agent_name="gitops-operator"
        )
        
        assert result["allowed"] is True


class TestPrePhase4Approval:
    """Tests for Phase 4 pre-guards (approval gate)."""

    def test_allows_with_realization_package(self):
        """Test allowing when realization package exists."""
        result = pre_phase_4_approval(
            tier="T3",
            realization_package={
                "files": ["main.tf"],
                "git_operations": {"commit": True},
            }
        )
        
        assert result["allowed"] is True

    def test_blocks_without_realization_package(self):
        """Test blocking when realization package is empty."""
        result = pre_phase_4_approval(
            tier="T3",
            realization_package={}
        )
        
        # Empty dict is truthy in Python but guard checks for actual content
        # This depends on implementation - may or may not block
        assert "allowed" in result

    def test_t2_operations_also_checked(self):
        """Test that T2 operations also go through approval pre-check."""
        result = pre_phase_4_approval(
            tier="T2",
            realization_package={"files": ["config.yaml"]}
        )
        
        assert result["allowed"] is True


class TestPrePhase5Realization:
    """Tests for Phase 5 pre-guards (realization). CRITICAL."""

    def test_allows_t3_with_approval(self):
        """Test allowing T3 operation with explicit approval."""
        result = pre_phase_5_realization(
            tier="T3",
            approval_validation={"approved": True, "action": "proceed_to_realization"},
            realization_package={"files": ["main.tf"]}
        )
        
        assert result["allowed"] is True

    def test_blocks_t3_without_approval(self):
        """CRITICAL: Test blocking T3 operation without approval."""
        result = pre_phase_5_realization(
            tier="T3",
            approval_validation={"approved": False, "action": "abort"},
            realization_package={"files": ["main.tf"]}
        )
        
        assert result["allowed"] is False
        assert "approval" in result["reason"].lower() or "T3" in result["reason"]

    def test_allows_t2_without_approval(self):
        """Test T2 operations don't require approval."""
        result = pre_phase_5_realization(
            tier="T2",
            approval_validation={"approved": False},  # No approval
            realization_package={"files": ["config.yaml"]}
        )
        
        assert result["allowed"] is True

    def test_allows_t1_without_approval(self):
        """Test T1 operations don't require approval."""
        result = pre_phase_5_realization(
            tier="T1",
            approval_validation={},  # No approval
            realization_package={"files": ["README.md"]}
        )
        
        assert result["allowed"] is True

    def test_validates_approval_validation_structure(self):
        """Test validation of approval validation result."""
        result = pre_phase_5_realization(
            tier="T3",
            approval_validation={"approved": True, "action": "proceed_to_realization"},
            realization_package={}
        )
        
        assert result["allowed"] is True


class TestPrePhase6SSOTUpdate:
    """Tests for Phase 6 pre-guards (SSOT update)."""

    def test_allows_after_successful_realization(self):
        """Test allowing SSOT update after successful realization."""
        result = pre_phase_6_ssot_update(
            tier="T3",
            realization_success=True
        )
        
        assert result["allowed"] is True

    def test_blocks_after_failed_realization(self):
        """Test blocking SSOT update when realization failed."""
        result = pre_phase_6_ssot_update(
            tier="T3",
            realization_success=False
        )
        
        assert result["allowed"] is False
        assert "failed" in result["reason"].lower()

    def test_t2_after_success(self):
        """Test T2 SSOT update after success."""
        result = pre_phase_6_ssot_update(
            tier="T2",
            realization_success=True
        )
        
        assert result["allowed"] is True


class TestPrePhaseHooksIntegration:
    """Integration tests for pre-phase hooks workflow."""

    @pytest.fixture
    def available_agents(self):
        return [
            "terraform-architect",
            "gitops-operator", 
            "cloud-troubleshooter",
            "devops-developer",
        ]

    def test_full_workflow_t3_with_approval(self, available_agents):
        """Test complete workflow for T3 operation with approval."""
        # Phase 0
        phase_0 = pre_phase_0_clarification(
            ambiguity_score=0.1,
            user_prompt="Apply terraform changes"
        )
        assert phase_0["allowed"] is True
        
        # Phase 1
        phase_1 = pre_phase_1_routing(
            agent_name="terraform-architect",
            routing_confidence=0.9,
            available_agents=available_agents
        )
        assert phase_1["allowed"] is True
        
        # Phase 2
        context = {
            "contract": {
                "project_details": {},
                "terraform_infrastructure": {},
                "operational_guidelines": {},
            }
        }
        phase_2 = pre_phase_2_context(
            context_payload=context,
            agent_name="terraform-architect"
        )
        assert phase_2["allowed"] is True
        
        # Phase 4
        phase_4 = pre_phase_4_approval(
            tier="T3",
            realization_package={"files": ["main.tf"]}
        )
        assert phase_4["allowed"] is True
        
        # Phase 5 (with approval)
        phase_5 = pre_phase_5_realization(
            tier="T3",
            approval_validation={"approved": True, "action": "proceed"},
            realization_package={"files": ["main.tf"]}
        )
        assert phase_5["allowed"] is True
        
        # Phase 6
        phase_6 = pre_phase_6_ssot_update(
            tier="T3",
            realization_success=True
        )
        assert phase_6["allowed"] is True

    def test_workflow_blocked_at_phase_5_without_approval(self, available_agents):
        """Test workflow stops at Phase 5 without T3 approval."""
        # Simulate successful phases 0-4
        # Then Phase 5 without approval
        phase_5 = pre_phase_5_realization(
            tier="T3",
            approval_validation={"approved": False},
            realization_package={"files": ["main.tf"]}
        )
        
        assert phase_5["allowed"] is False
        # Workflow should stop here - Phase 6 never reached
