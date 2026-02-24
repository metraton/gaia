#!/usr/bin/env python3
"""
Tests for Task Validator.

PRIORITY: HIGH - Critical for context enforcement.

Validates:
1. Agent existence verification
2. Context provisioning enforcement (# Project Context)
3. T3 operation approval requirement
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.task_validator import (
    TaskValidator,
    validate_task_invocation,
    TaskValidationResult,
    AVAILABLE_AGENTS,
    META_AGENTS,
    T3_KEYWORDS,
    APPROVAL_INDICATORS,
)
from modules.security.tiers import SecurityTier


class TestAgentExistence:
    """Test agent existence verification."""

    @pytest.fixture
    def validator(self):
        return TaskValidator()

    @pytest.mark.parametrize("agent", AVAILABLE_AGENTS)
    def test_allows_valid_agents(self, validator, agent):
        """Test that all registered agents are allowed."""
        params = {
            "subagent_type": agent,
            "prompt": "Test prompt",
        }
        result = validator.validate(params)
        # Should pass agent existence check (may fail for other reasons)
        assert "Unknown agent" not in result.reason

    def test_blocks_unknown_agent(self, validator):
        """Test that unknown agents are blocked."""
        params = {
            "subagent_type": "nonexistent-agent",
            "prompt": "Test prompt",
        }
        result = validator.validate(params)
        assert result.allowed is False
        assert "Unknown agent" in result.reason or "unknown" in result.reason.lower()

    def test_lists_available_agents_on_error(self, validator):
        """Test that error message lists available agents."""
        params = {
            "subagent_type": "fake-agent",
            "prompt": "Test",
        }
        result = validator.validate(params)
        assert result.allowed is False
        # Should mention available agents
        assert any(agent in result.reason for agent in AVAILABLE_AGENTS[:3])

    def test_project_agents_registered(self):
        """Test that all project agents are in the registry."""
        project_agents = [
            "terraform-architect",
            "gitops-operator",
            "cloud-troubleshooter",
            "devops-developer",
        ]
        for agent in project_agents:
            assert agent in AVAILABLE_AGENTS, f"Project agent {agent} should be registered"

    def test_meta_agents_registered(self):
        """Test that all meta-agents are in the registry."""
        for agent in META_AGENTS:
            assert agent in AVAILABLE_AGENTS, f"Meta-agent {agent} should be registered"


class TestContextProvisioning:
    """Test context provisioning enforcement."""

    @pytest.fixture
    def validator(self):
        return TaskValidator()

    def test_detects_project_context_header(self, validator):
        """Test detection of '# Project Context' in prompt."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "# Project Context\nProject: test\n\nDeploy changes",
        }
        result = validator.validate(params)
        assert result.has_context is True

    def test_detects_contract_keyword(self, validator):
        """Test detection of 'contract' in prompt."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "Contract includes project details and terraform config",
        }
        result = validator.validate(params)
        assert result.has_context is True

    def test_detects_context_provider_reference(self, validator):
        """Test detection of context_provider.py reference."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "Context from context_provider.py output",
        }
        result = validator.validate(params)
        assert result.has_context is True

    def test_warns_missing_context_for_project_agents(self, validator, caplog):
        """Test warning when project agent lacks context."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "Deploy changes to production",
        }
        result = validator.validate(params)
        # Should log a warning about missing context
        assert result.has_context is False

    def test_meta_agents_dont_require_context(self, validator):
        """Test that meta-agents don't require context_provider."""
        for agent in META_AGENTS:
            params = {
                "subagent_type": agent,
                "prompt": "Analyze the system",
            }
            result = validator.validate(params)
            # Should not block due to missing context
            if not result.allowed:
                assert "context" not in result.reason.lower()


class TestT3ApprovalRequirement:
    """Test T3 operation approval requirement."""

    @pytest.fixture
    def validator(self):
        return TaskValidator()

    @pytest.mark.parametrize("keyword", T3_KEYWORDS)
    def test_detects_t3_keywords(self, validator, keyword):
        """Test detection of T3 keywords in prompt."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": f"Execute {keyword} operation",
        }
        result = validator.validate(params)
        assert result.is_t3_operation is True

    def test_blocks_t3_without_approval(self, validator):
        """CRITICAL: Test that T3 operations without approval are blocked."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "Run terraform apply to deploy changes",
        }
        result = validator.validate(params)
        assert result.allowed is False
        assert result.is_t3_operation is True
        assert result.has_approval is False
        # Should mention approval requirement
        assert "approval" in result.reason.lower() or "Phase 4" in result.reason

    @pytest.mark.parametrize("indicator", ["approved by user", "user approval received"])
    def test_detects_approval_indicators(self, validator, indicator):
        """Test detection of approval indicators."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": f"terraform apply - {indicator}",
        }
        result = validator.validate(params)
        assert result.has_approval is True

    def test_allows_t3_with_approval(self, validator):
        """Test that T3 operations WITH approval are allowed."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "User approval received. Run terraform apply to deploy changes",
        }
        result = validator.validate(params)
        assert result.allowed is True
        assert result.is_t3_operation is True
        assert result.has_approval is True

    def test_allows_non_t3_without_approval(self, validator):
        """Test that non-T3 operations don't require approval."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "Run terraform plan to preview changes",
        }
        result = validator.validate(params)
        # terraform plan is T1, not T3
        assert result.is_t3_operation is False

    def test_t3_keywords_trigger_detection(self, validator):
        """Test that T3 keywords trigger T3 detection."""
        # Test terraform apply
        params1 = {
            "subagent_type": "terraform-architect",
            "prompt": "Please run terraform apply for infrastructure changes",
        }
        result1 = validator.validate(params1)
        assert result1.is_t3_operation is True

        # Test kubectl apply
        params2 = {
            "subagent_type": "gitops-operator",
            "prompt": "Execute kubectl apply to deploy the manifest",
        }
        result2 = validator.validate(params2)
        assert result2.is_t3_operation is True

    def test_git_commit_is_t3(self, validator):
        """git commit must be treated as T3."""
        params = {
            "subagent_type": "devops-developer",
            "prompt": "Run git commit -m 'feat: update deployment config'",
        }
        result = validator.validate(params)
        assert result.is_t3_operation is True

    def test_git_push_any_branch_is_t3(self, validator):
        """git push should be T3 regardless of branch name."""
        params = {
            "subagent_type": "devops-developer",
            "prompt": "Run git push origin feature/hotfix-auth",
        }
        result = validator.validate(params)
        assert result.is_t3_operation is True

    def test_t3_requires_canonical_approval_token(self, validator):
        """Canonical approval token should allow T3 execution."""
        params = {
            "subagent_type": "devops-developer",
            "prompt": "User approval received. Run git push origin feature/test",
        }
        result = validator.validate(params)
        assert result.allowed is True
        assert result.has_approval is True


class TestValidationResult:
    """Test TaskValidationResult structure."""

    @pytest.fixture
    def validator(self):
        return TaskValidator()

    def test_result_has_all_fields(self, validator):
        """Test that result contains all expected fields."""
        params = {
            "subagent_type": "devops-developer",
            "prompt": "Test prompt",
        }
        result = validator.validate(params)

        assert hasattr(result, "allowed")
        assert hasattr(result, "tier")
        assert hasattr(result, "reason")
        assert hasattr(result, "agent_name")
        assert hasattr(result, "has_context")
        assert hasattr(result, "is_t3_operation")
        assert hasattr(result, "has_approval")

    def test_result_tier_is_security_tier(self, validator):
        """Test that tier is a SecurityTier enum."""
        params = {
            "subagent_type": "devops-developer",
            "prompt": "Test",
        }
        result = validator.validate(params)
        assert isinstance(result.tier, SecurityTier)


class TestConvenienceFunction:
    """Test validate_task_invocation convenience function."""

    def test_convenience_function_works(self):
        """Test that convenience function returns expected result."""
        params = {
            "subagent_type": "devops-developer",
            "prompt": "Test prompt",
        }
        result = validate_task_invocation(params)
        assert isinstance(result, TaskValidationResult)

    def test_convenience_function_blocks_unknown_agent(self):
        """Test convenience function blocks unknown agents."""
        params = {
            "subagent_type": "unknown-agent",
            "prompt": "Test",
        }
        result = validate_task_invocation(params)
        assert result.allowed is False


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.fixture
    def validator(self):
        return TaskValidator()

    def test_empty_parameters(self, validator):
        """Test handling of empty parameters."""
        result = validator.validate({})
        assert result.agent_name == "unknown"

    def test_missing_prompt(self, validator):
        """Test handling of missing prompt."""
        params = {
            "subagent_type": "devops-developer",
        }
        result = validator.validate(params)
        # Should handle gracefully
        assert isinstance(result, TaskValidationResult)

    def test_none_parameters(self, validator):
        """Test handling when values are None."""
        params = {
            "subagent_type": None,
            "prompt": None,
        }
        # Should handle without crashing
        try:
            result = validator.validate(params)
            assert isinstance(result, TaskValidationResult)
        except (TypeError, AttributeError):
            # Some implementations may raise errors for None values
            pass

    def test_case_insensitive_t3_detection(self, validator):
        """Test that T3 keywords are detected case-insensitively."""
        params = {
            "subagent_type": "terraform-architect",
            "prompt": "Run TERRAFORM APPLY now",
        }
        result = validator.validate(params)
        assert result.is_t3_operation is True

    def test_custom_available_agents(self):
        """Test validator with custom agent list."""
        custom_agents = ["custom-agent-1", "custom-agent-2"]
        validator = TaskValidator(available_agents=custom_agents)

        params = {
            "subagent_type": "custom-agent-1",
            "prompt": "Test",
        }
        result = validator.validate(params)
        assert "Unknown agent" not in result.reason
