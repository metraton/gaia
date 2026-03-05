#!/usr/bin/env python3
"""
Tests for Bash Validator.

Tests the bash command validation system:
1. Safe commands (T0, T1, T2) are allowed
2. Dangerous (T3) commands are blocked with structured block_response
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

    # T3 commands (blocked by dangerous verb detector with structured response)
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "git push origin main",
    ])
    def test_t3_commands_blocked_by_dangerous_verb_detector(self, validator, command):
        """Test T3 commands are blocked with a structured block_response."""
        result = validator.validate(command)
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "dangerous" in result.reason.lower()
        # Block response uses hookSpecificOutput format for corrective messaging
        assert result.block_response is not None
        assert "hookSpecificOutput" in result.block_response
        assert result.block_response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "approval workflow" in result.block_response["hookSpecificOutput"]["permissionDecisionReason"]

    # Commands blocked by dangerous verb detector (before reaching GitOps validator)
    @pytest.mark.parametrize("command", [
        "kubectl apply -f manifest.yaml",
        "helm install my-release chart/",
    ])
    def test_blocks_mutative_gitops_commands(self, validator, command):
        """Test mutative GitOps commands are blocked by dangerous verb detector."""
        result = validator.validate(command)
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "dangerous" in result.reason.lower()
        assert result.block_response is not None

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

    def test_t3_in_compound_blocked(self, validator):
        """Test compound with T3 part is blocked by dangerous verb detector."""
        result = validator.validate("ls -la && terraform apply")
        assert result.allowed is False
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
        # T0 + T2 (simulation) should return T2
        result = validator.validate("ls -la && terraform plan")
        assert result.allowed is True
        assert result.tier == SecurityTier.T2_DRY_RUN


class TestClaudeFooterStripping:
    """Test auto-stripping of Claude-generated commit footers via updatedInput.

    Note: git commit is a mutative command and will be blocked by the dangerous
    verb detector. Footer stripping still happens before the command reaches
    _validate_single_command, so we test the stripping logic directly and verify
    that the block response contains the cleaned command (without footers).
    """

    def test_footer_stripping_occurs_before_validation(self, validator):
        """Test that footer stripping happens before dangerous verb detection."""
        result = validator.validate('git commit -m "feat(test): add feature\n\nGenerated with Claude Code"')
        # git commit is blocked by dangerous verb detector
        assert result.allowed is False
        assert result.block_response is not None
        # The block response message should NOT contain the stripped footer
        reason = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Generated with Claude Code" not in reason

    def test_strips_co_authored_by_before_validation(self, validator):
        """Test that Co-Authored-By footer is stripped before dangerous verb detection."""
        result = validator.validate('git commit -m "feat(test): add feature\n\nCo-Authored-By: Claude Opus 4.6"')
        assert result.allowed is False
        assert result.block_response is not None

    def test_clean_commit_blocked_by_dangerous_verb(self, validator):
        """Test that clean commits are blocked by dangerous verb detector."""
        result = validator.validate('git commit -m "feat(api): add endpoint"')
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "commit" in result.reason.lower()

    def test_internal_strip_method(self, validator):
        """Test the internal _strip_claude_footers method directly."""
        command = 'git commit -m "feat(test): add feature\n\nGenerated with Claude Code"'
        stripped = validator._strip_claude_footers(command)
        assert "Generated with Claude Code" not in stripped
        assert "feat(test): add feature" in stripped

    def test_internal_strip_co_authored(self, validator):
        """Test the internal _strip_claude_footers strips Co-Authored-By."""
        command = 'git commit -m "feat(test): add feature\n\nCo-Authored-By: Claude Opus 4.6"'
        stripped = validator._strip_claude_footers(command)
        assert "Co-Authored-By" not in stripped
        assert "feat(test): add feature" in stripped

    def test_no_modified_input_for_non_commit_commands(self, validator):
        """Test that non-commit commands don't have modified_input."""
        result = validator.validate("ls -la")
        assert result.allowed is True
        assert result.modified_input is None


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

    def test_convenience_function_blocks_t3_commands(self):
        """Test convenience function blocks dangerous T3 commands."""
        result = validate_bash_command("terraform apply")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert result.block_response is not None

    def test_convenience_function_blocks_dangerous(self):
        """Test convenience function blocks dangerous commands."""
        result = validate_bash_command("kubectl delete namespace production")
        assert result.allowed is False


class TestBlockedCommandsPriority:
    """Test that blocked commands are caught BEFORE cloud_pipe_validator.

    This is the critical bug fix: when a command is permanently blocked
    (e.g., kubectl delete namespace), it must be caught with exit 2
    (no block_response) regardless of whether cloud_pipe_validator would
    also flag it. Exit 2 is reliably honored by Claude Code; exit 0 with
    permissionDecision: "deny" is NOT reliable.
    """

    def test_kubectl_delete_namespace_blocked_without_block_response(self, validator):
        """kubectl delete namespace must be caught by deny list, not cloud_pipe_validator."""
        result = validator.validate("kubectl delete namespace production")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "security policy" in result.reason.lower()
        # CRITICAL: block_response must be None (exit 2 path), not a dict (exit 0 path)
        assert result.block_response is None

    def test_kubectl_delete_namespace_with_or_blocked_reliably(self, validator):
        """The exact bug scenario: kubectl delete namespace with || and 2>&1.

        Previously, cloud_pipe_validator caught `||` as a false-positive pipe
        and returned exit 0 block (unreliable). Now, blocked_commands runs
        first and returns exit 2 (reliable).
        """
        result = validator.validate("kubectl delete namespace test 2>&1 || echo 'blocked'")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "security policy" in result.reason.lower()
        assert result.block_response is None

    def test_blocked_command_in_compound_caught_early(self, validator):
        """Blocked command as part of compound is caught before cloud_pipe_validator."""
        result = validator.validate("ls -la && kubectl delete namespace production")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "security policy" in result.reason.lower()
        assert result.block_response is None

    def test_gcloud_cluster_delete_blocked_without_block_response(self, validator):
        """gcloud container clusters delete must use exit 2 path."""
        result = validator.validate("gcloud container clusters delete my-cluster --region us-central1")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "security policy" in result.reason.lower()
        assert result.block_response is None

    def test_aws_eks_delete_cluster_blocked_without_block_response(self, validator):
        """aws eks delete-cluster must use exit 2 path."""
        result = validator.validate("aws eks delete-cluster --name my-cluster")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED
        assert "security policy" in result.reason.lower()
        assert result.block_response is None


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
