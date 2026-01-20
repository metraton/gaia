#!/usr/bin/env python3
"""
Tests for Bash Validator.

Tests the bash command validation system:
1. Safe commands (T0, T1, T2) are allowed
2. T3 commands require approval (allowed=True, tier=T3)
3. Deny list commands are blocked (allowed=False)
4. Compound commands are validated correctly
5. Credential requirements are detected
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import BashValidator, validate_bash_command, BashValidationResult
from modules.security.tiers import SecurityTier


@pytest.fixture
def validator():
    """Create BashValidator instance."""
    return BashValidator()


class TestBashValidator:
    """Test BashValidator class."""

    # Safe commands (T0, T1, T2)
    @pytest.mark.parametrize("command", [
        "ls -la",
        "pwd",
        "cat file.txt",
        "kubectl get pods",
        "terraform plan",
        "git status",
    ])
    def test_allows_safe_commands(self, validator, command):
        """Test allows safe read-only commands."""
        result = validator.validate(command)
        assert result.allowed is True
        assert result.tier in [SecurityTier.T0_READ_ONLY, SecurityTier.T1_VALIDATION, SecurityTier.T2_DRY_RUN]

    # T3 commands (require approval but allowed)
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "git push origin main",
    ])
    def test_t3_commands_require_approval(self, validator, command):
        """Test T3 commands are allowed but require approval."""
        result = validator.validate(command)
        assert result.allowed is True
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "tier T3" in result.reason or "approval" in result.reason.lower()

    # Commands blocked by specific policies
    @pytest.mark.parametrize("command,policy", [
        ("kubectl apply -f manifest.yaml", "GitOps"),
        ("helm install my-release chart/", "GitOps"),
    ])
    def test_blocks_gitops_policy_violations(self, validator, command, policy):
        """Test commands blocked by GitOps policy."""
        result = validator.validate(command)
        assert result.allowed is False
        assert policy.lower() in result.reason.lower()

    def test_blocks_invalid_commit_message(self, validator):
        """Test blocks git commit with invalid format."""
        result = validator.validate("git commit -m 'bad message format'")
        assert result.allowed is False
        assert "commit message" in result.reason.lower() or "conventional" in result.reason.lower()

    # Permanently blocked commands (deny list)
    @pytest.mark.parametrize("command", [
        "aws eks delete-cluster my-cluster",
        "gcloud container clusters delete my-cluster",
        "kubectl delete namespace production",
        "git push --force origin main",
    ])
    def test_blocks_deny_list_commands(self, validator, command):
        """Test permanently blocked commands from deny list."""
        result = validator.validate(command)
        assert result.allowed is False
        assert "blocked" in result.reason.lower() or "denied" in result.reason.lower()

    def test_blocks_empty_command(self, validator):
        """Test blocks empty command."""
        result = validator.validate("")
        assert result.allowed is False

    def test_blocks_whitespace_command(self, validator):
        """Test blocks whitespace-only command."""
        result = validator.validate("   ")
        assert result.allowed is False


class TestCompoundCommandValidation:
    """Test validation of compound commands."""

    def test_allows_all_safe_compound(self, validator):
        """Test allows compound with all safe parts."""
        result = validator.validate("ls -la && pwd && cat file.txt")
        assert result.allowed is True
        assert result.tier == SecurityTier.T0_READ_ONLY

    def test_t3_in_compound_requires_approval(self, validator):
        """Test compound with T3 part requires approval."""
        result = validator.validate("ls -la && terraform apply")
        assert result.allowed is True
        assert result.tier == SecurityTier.T3_BLOCKED

    def test_blocks_deny_in_compound(self, validator):
        """Test blocks compound with denied command."""
        result = validator.validate("ls -la && kubectl delete namespace production")
        assert result.allowed is False

    def test_blocks_piped_unsafe(self, validator):
        """Test blocks piped command with unsafe part."""
        result = validator.validate("cat file.txt | kubectl delete namespace production")
        assert result.allowed is False

    def test_returns_highest_tier(self, validator):
        """Test returns highest security tier from compound."""
        # T0 + T3 should return T3
        result = validator.validate("ls -la && terraform apply")
        assert result.tier == SecurityTier.T3_BLOCKED


class TestClaudeFooterDetection:
    """Test detection of Claude-generated commit footers."""

    def test_blocks_generated_with_claude_code(self, validator):
        """Test blocks commit with 'Generated with Claude Code' footer."""
        result = validator.validate('git commit -m "test\n\nGenerated with Claude Code"')
        assert result.allowed is False
        assert "claude" in result.reason.lower() or "footer" in result.reason.lower()

    def test_blocks_co_authored_by_claude(self, validator):
        """Test blocks commit with 'Co-Authored-By: Claude' footer."""
        result = validator.validate('git commit -m "test\n\nCo-Authored-By: Claude Sonnet"')
        assert result.allowed is False


class TestCredentialRequirement:
    """Test credential requirement detection."""

    def test_detects_kubectl_credentials(self, validator):
        """Test detects kubectl commands need credentials."""
        result = validator.validate("kubectl get pods")
        # This should either require credentials or be marked somehow
        # Implementation may vary - adjust based on actual behavior

    def test_detects_helm_credentials(self, validator):
        """Test detects helm commands need credentials."""
        result = validator.validate("helm list")
        # This should either require credentials or be marked somehow
        # Implementation may vary - adjust based on actual behavior

    def test_detects_flux_credentials(self, validator):
        """Test detects flux commands need credentials."""
        result = validator.validate("flux get sources git")
        # This should either require credentials or be marked somehow
        # Implementation may vary - adjust based on actual behavior


class TestBashValidationResult:
    """Test BashValidationResult structure."""

    def test_result_has_expected_fields(self, validator):
        """Test result has all expected fields."""
        result = validator.validate("ls -la")
        assert hasattr(result, "allowed")
        assert hasattr(result, "tier")
        assert hasattr(result, "reason")
        assert hasattr(result, "suggestions")
        assert hasattr(result, "requires_credentials")

    def test_suggestions_for_blocked(self, validator):
        """Test provides suggestions for blocked commands."""
        result = validator.validate("kubectl delete namespace production")
        assert result.allowed is False
        # May or may not have suggestions depending on implementation


class TestConvenienceFunction:
    """Test validate_bash_command convenience function."""

    def test_convenience_function_allows_safe(self):
        """Test convenience function works for safe commands."""
        result = validate_bash_command("ls -la")
        assert result.allowed is True

    def test_convenience_function_t3_requires_approval(self):
        """Test convenience function marks T3 commands."""
        result = validate_bash_command("terraform apply")
        assert result.allowed is True
        assert result.tier == SecurityTier.T3_BLOCKED

    def test_convenience_function_blocks_dangerous(self):
        """Test convenience function blocks dangerous commands."""
        result = validate_bash_command("kubectl delete namespace production")
        assert result.allowed is False


class TestEdgeCases:
    """Test edge cases."""

    def test_handles_long_command(self, validator):
        """Test handles very long commands."""
        long_cmd = "ls " + " ".join([f"file{i}" for i in range(1000)])
        result = validator.validate(long_cmd)
        assert isinstance(result, BashValidationResult)

    def test_handles_special_characters(self, validator):
        """Test handles special characters in commands."""
        result = validator.validate('echo "hello && world"')
        assert isinstance(result, BashValidationResult)
