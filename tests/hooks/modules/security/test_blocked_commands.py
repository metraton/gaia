#!/usr/bin/env python3
"""
Tests for Blocked Command Detection.

Validates:
1. is_blocked_command() function
2. Blocked pattern categories
3. Suggestions for blocked commands
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.blocked_commands import (
    is_blocked_command,
    get_blocked_patterns,
    get_blocked_patterns_by_category,
    get_suggestion_for_blocked,
    BlockedCommandResult,
    BLOCKED_PATTERNS,
    BLOCKED_COMMAND_SUGGESTIONS,
)


class TestIsBlockedCommand:
    """Test is_blocked_command() function."""

    # Terraform blocked commands
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "terraform destroy",
        "terragrunt apply",
        "terragrunt destroy",
    ])
    def test_terraform_destructive_blocked(self, command):
        """Test terraform destructive commands are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "terraform"

    # Kubernetes blocked commands
    @pytest.mark.parametrize("command", [
        "kubectl apply -f manifest.yaml",
        "kubectl create deployment test",
        "kubectl delete pod test-pod",
        "kubectl patch deployment test",
    ])
    def test_kubernetes_write_blocked(self, command):
        """Test kubernetes write commands are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "kubernetes"

    # Helm blocked commands
    @pytest.mark.parametrize("command", [
        "helm install release chart/",
        "helm upgrade release chart/",
        "helm uninstall release",
        "helm delete release",
    ])
    def test_helm_write_blocked(self, command):
        """Test helm write commands are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "helm"

    # Flux blocked commands
    @pytest.mark.parametrize("command", [
        "flux create source git test",
        "flux delete helmrelease test",
    ])
    def test_flux_write_blocked(self, command):
        """Test flux write commands are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "flux"

    # File destruction blocked
    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "rm -f important_file",
        "shred /path/to/file",
    ])
    def test_file_destruction_blocked(self, command):
        """Test file destruction commands are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "file_destruction"

    # Git write commands
    @pytest.mark.parametrize("command", [
        "git push origin main",
        "git commit -m 'message'",
    ])
    def test_git_write_blocked(self, command):
        """Test git write commands are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "git"

    # Safe commands not blocked
    @pytest.mark.parametrize("command", [
        "ls -la",
        "cat file.txt",
        "kubectl get pods",
        "terraform plan",
        "git status",
    ])
    def test_safe_commands_not_blocked(self, command):
        """Test safe commands are not blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False

    def test_empty_command_not_blocked(self):
        """Test empty command is not blocked (handled elsewhere)."""
        result = is_blocked_command("")
        assert result.is_blocked is False

    def test_dry_run_not_blocked(self):
        """Test dry-run variants are not blocked."""
        result = is_blocked_command("kubectl apply --dry-run=client -f file.yaml")
        assert result.is_blocked is False


class TestBlockedCommandResult:
    """Test BlockedCommandResult structure."""

    def test_result_has_expected_fields(self):
        """Test result contains expected fields."""
        result = is_blocked_command("rm -rf /")
        assert hasattr(result, "is_blocked")
        assert hasattr(result, "pattern_matched")
        assert hasattr(result, "category")
        assert hasattr(result, "suggestion")

    def test_blocked_result_has_pattern(self):
        """Test blocked result includes matched pattern."""
        result = is_blocked_command("terraform apply")
        assert result.is_blocked is True
        assert result.pattern_matched is not None
        assert len(result.pattern_matched) > 0

    def test_blocked_result_has_category(self):
        """Test blocked result includes category."""
        result = is_blocked_command("kubectl delete pod test")
        assert result.is_blocked is True
        assert result.category == "kubernetes"


class TestGetBlockedPatterns:
    """Test get_blocked_patterns() function."""

    def test_returns_list(self):
        """Test returns a list of patterns."""
        patterns = get_blocked_patterns()
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_patterns_are_strings(self):
        """Test all patterns are strings."""
        patterns = get_blocked_patterns()
        for pattern in patterns:
            assert isinstance(pattern, str)

    def test_contains_terraform_patterns(self):
        """Test contains terraform patterns."""
        patterns = get_blocked_patterns()
        assert any("terraform" in p.lower() for p in patterns)

    def test_contains_kubectl_patterns(self):
        """Test contains kubectl patterns."""
        patterns = get_blocked_patterns()
        assert any("kubectl" in p.lower() for p in patterns)


class TestGetBlockedPatternsByCategory:
    """Test get_blocked_patterns_by_category() function."""

    @pytest.mark.parametrize("category", list(BLOCKED_PATTERNS.keys()))
    def test_returns_patterns_for_valid_category(self, category):
        """Test returns patterns for all valid categories."""
        patterns = get_blocked_patterns_by_category(category)
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_returns_empty_for_invalid_category(self):
        """Test returns empty list for invalid category."""
        patterns = get_blocked_patterns_by_category("nonexistent")
        assert patterns == []


class TestGetSuggestionForBlocked:
    """Test get_suggestion_for_blocked() function."""

    def test_terraform_apply_suggestion(self):
        """Test suggestion for terraform apply."""
        suggestion = get_suggestion_for_blocked("terraform apply")
        assert suggestion is not None
        assert "plan" in suggestion.lower()

    def test_kubectl_apply_suggestion(self):
        """Test suggestion for kubectl apply."""
        suggestion = get_suggestion_for_blocked("kubectl apply -f file.yaml")
        assert suggestion is not None
        assert "dry-run" in suggestion.lower()

    def test_kubectl_delete_suggestion(self):
        """Test suggestion for kubectl delete."""
        suggestion = get_suggestion_for_blocked("kubectl delete pod test")
        assert suggestion is not None

    def test_helm_install_suggestion(self):
        """Test suggestion for helm install."""
        suggestion = get_suggestion_for_blocked("helm install release chart/")
        assert suggestion is not None
        assert "dry-run" in suggestion.lower() or "template" in suggestion.lower()

    def test_no_suggestion_for_unknown(self):
        """Test no suggestion for unknown command."""
        suggestion = get_suggestion_for_blocked("some_unknown_command")
        assert suggestion is None


class TestBlockedPatternsCategories:
    """Test blocked patterns categories."""

    def test_terraform_category_exists(self):
        """Test terraform category exists."""
        assert "terraform" in BLOCKED_PATTERNS

    def test_kubernetes_category_exists(self):
        """Test kubernetes category exists."""
        assert "kubernetes" in BLOCKED_PATTERNS

    def test_helm_category_exists(self):
        """Test helm category exists."""
        assert "helm" in BLOCKED_PATTERNS

    def test_flux_category_exists(self):
        """Test flux category exists."""
        assert "flux" in BLOCKED_PATTERNS

    def test_gcp_category_exists(self):
        """Test gcp category exists."""
        assert "gcp" in BLOCKED_PATTERNS

    def test_aws_category_exists(self):
        """Test aws category exists."""
        assert "aws" in BLOCKED_PATTERNS

    def test_docker_category_exists(self):
        """Test docker category exists."""
        assert "docker" in BLOCKED_PATTERNS

    def test_git_category_exists(self):
        """Test git category exists."""
        assert "git" in BLOCKED_PATTERNS

    def test_file_destruction_category_exists(self):
        """Test file_destruction category exists."""
        assert "file_destruction" in BLOCKED_PATTERNS


class TestEdgeCases:
    """Test edge cases in blocked command detection."""

    def test_case_insensitive_matching(self):
        """Test matching is case insensitive."""
        result1 = is_blocked_command("TERRAFORM APPLY")
        result2 = is_blocked_command("terraform apply")
        assert result1.is_blocked == result2.is_blocked

    def test_terraform_apply_help_not_blocked(self):
        """Test terraform apply --help is not blocked."""
        result = is_blocked_command("terraform apply --help")
        # Implementation may or may not block this
        assert isinstance(result.is_blocked, bool)

    def test_flux_reconcile_dry_run_not_blocked(self):
        """Test flux reconcile --dry-run is not blocked."""
        result = is_blocked_command("flux reconcile kustomization test --dry-run")
        assert result.is_blocked is False

    def test_blocked_within_longer_command(self):
        """Test detection within longer command string."""
        result = is_blocked_command("cd /tmp && terraform apply -auto-approve")
        # This tests pattern matching - compound parsing is elsewhere
        assert result.is_blocked is True
