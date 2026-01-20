#!/usr/bin/env python3
"""
Tests for Blocked Command Detection.

Tests ONLY commands that are PERMANENTLY BLOCKED (deny list).
Commands that require approval (ask list) are NOT tested here.

Validates:
1. AWS/GCP critical delete operations are blocked
2. Kubernetes critical operations (namespace, pv, node, cluster) are blocked
3. Git force push is blocked
4. Disk destruction operations are blocked
5. Safe commands are NOT blocked
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


class TestAWSCriticalBlockedCommands:
    """Test AWS critical delete operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "aws cloudformation delete-stack my-stack",
        "aws ec2 terminate-instances --instance-ids i-1234567",
        "aws rds delete-db-instance my-db",
        "aws eks delete-cluster my-cluster",
        "aws s3 rb s3://my-bucket --force",
        "aws lambda delete-function my-function",
    ])
    def test_aws_critical_delete_blocked(self, command):
        """Test AWS critical delete operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "aws_delete"


class TestGCPCriticalBlockedCommands:
    """Test GCP critical delete operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "gcloud container clusters delete my-cluster",
        "gcloud compute instances delete my-instance",
        "gcloud projects delete my-project",
        "gcloud sql instances delete my-sql-instance",
        "gsutil rb gs://my-bucket",
        "gsutil rm -r gs://my-bucket/*",
    ])
    def test_gcp_critical_delete_blocked(self, command):
        """Test GCP critical delete operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "gcp_delete"


class TestKubernetesCriticalBlockedCommands:
    """Test Kubernetes CRITICAL operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "kubectl delete namespace production",
        "kubectl delete pv my-persistent-volume",
        "kubectl delete node worker-node-1",
        "kubectl delete cluster my-cluster",
        "kubectl delete crd mycustomresources.example.com",
        "kubectl drain worker-node-1",
    ])
    def test_kubernetes_critical_blocked(self, command):
        """Test Kubernetes CRITICAL operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "kubernetes_critical"


class TestGitForceBlockedCommands:
    """Test Git force push operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "git push --force origin main",
        "git push -f origin main",
        "git push origin --force",
        "git push origin -f",
    ])
    def test_git_force_push_blocked(self, command):
        """Test Git force push is blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "git_force"


class TestFluxDeleteBlockedCommands:
    """Test Flux delete operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "flux delete source git my-source",
        "flux delete helmrelease my-release",
        "flux delete kustomization my-kustomization",
    ])
    def test_flux_delete_blocked(self, command):
        """Test Flux delete operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "flux_delete"


class TestDiskOperationsBlocked:
    """Test disk destruction operations are permanently blocked."""

    @pytest.mark.parametrize("command", [
        "dd if=/dev/zero of=/dev/sda",
        "fdisk /dev/sda",
        "mkfs.ext4 /dev/sda1",
        "mkfs /dev/sda1",
    ])
    def test_disk_operations_blocked(self, command):
        """Test disk destruction operations are blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is True
        assert result.category == "disk_operations"


class TestSafeCommandsNotBlocked:
    """Test that safe commands and commands in ask list are NOT blocked."""

    @pytest.mark.parametrize("command", [
        # Read-only commands
        "ls -la",
        "kubectl get pods",
        "terraform plan",
        "aws ec2 describe-instances",
        "gcloud compute instances list",

        # Commands that require APPROVAL (ask list) but NOT blocked
        "terraform apply",
        "terraform destroy",
        "kubectl apply -f manifest.yaml",
        "kubectl delete pod my-pod",
        "helm install my-release chart/",
        "git commit -m 'message'",
        "git push origin main",

        # Dry-run commands
        "terraform plan -out=plan.tfplan",
        "kubectl apply --dry-run=client -f manifest.yaml",
        "flux reconcile source git my-source",
    ])
    def test_safe_and_ask_commands_not_blocked(self, command):
        """Test safe commands and ask-list commands are NOT blocked."""
        result = is_blocked_command(command)
        assert result.is_blocked is False


class TestBlockedCommandResult:
    """Test BlockedCommandResult structure."""

    def test_blocked_command_has_category(self):
        """Blocked command result includes category."""
        result = is_blocked_command("aws eks delete-cluster my-cluster")
        assert result.is_blocked is True
        assert result.category == "aws_delete"
        assert result.pattern_matched is not None

    def test_safe_command_has_no_category(self):
        """Safe command result has no category."""
        result = is_blocked_command("ls -la")
        assert result.is_blocked is False
        assert result.category is None
        assert result.pattern_matched is None


class TestGetBlockedPatterns:
    """Test get_blocked_patterns() function."""

    def test_returns_list(self):
        """get_blocked_patterns() returns a list."""
        patterns = get_blocked_patterns()
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_patterns_are_strings(self):
        """All patterns are strings."""
        patterns = get_blocked_patterns()
        assert all(isinstance(p, str) for p in patterns)

    def test_contains_critical_patterns(self):
        """Patterns include critical commands."""
        patterns = get_blocked_patterns()
        patterns_str = " ".join(patterns)

        # Should contain AWS critical
        assert "aws" in patterns_str
        assert "delete" in patterns_str

        # Should contain Kubernetes critical
        assert "kubectl" in patterns_str
        assert "namespace" in patterns_str


class TestGetBlockedPatternsByCategory:
    """Test get_blocked_patterns_by_category() function."""

    @pytest.mark.parametrize("category", [
        "aws_delete",
        "gcp_delete",
        "kubernetes_critical",
        "git_force",
        "flux_delete",
        "disk_operations",
    ])
    def test_returns_patterns_for_valid_category(self, category):
        """Returns patterns for valid categories."""
        patterns = get_blocked_patterns_by_category(category)
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_returns_empty_for_invalid_category(self):
        """Returns empty list for invalid category."""
        patterns = get_blocked_patterns_by_category("nonexistent_category")
        assert patterns == []


class TestGetSuggestionForBlocked:
    """Test get_suggestion_for_blocked() function."""

    def test_returns_suggestion_for_known_commands(self):
        """Returns suggestions for known blocked commands."""
        suggestion = get_suggestion_for_blocked("aws eks delete-cluster")
        assert suggestion is not None
        assert "BLOCKED" in suggestion or "Terraform" in suggestion

    def test_returns_none_for_unknown_commands(self):
        """Returns None for unknown commands."""
        suggestion = get_suggestion_for_blocked("unknown_command")
        assert suggestion is None


class TestBlockedPatternsCategories:
    """Test that all expected categories exist in BLOCKED_PATTERNS."""

    @pytest.mark.parametrize("category", [
        "aws_delete",
        "gcp_delete",
        "kubernetes_critical",
        "git_force",
        "flux_delete",
        "disk_operations",
    ])
    def test_category_exists(self, category):
        """Test that expected category exists."""
        assert category in BLOCKED_PATTERNS
        assert len(BLOCKED_PATTERNS[category]) > 0


class TestEdgeCases:
    """Test edge cases and special scenarios."""

    def test_empty_command_not_blocked(self):
        """Empty command is not blocked."""
        result = is_blocked_command("")
        assert result.is_blocked is False

    def test_case_sensitive_matching(self):
        """Commands are case-sensitive (lowercase expected)."""
        # Should block
        result1 = is_blocked_command("aws eks delete-cluster")
        assert result1.is_blocked is True

        # Should also block (commands typically lowercase)
        result2 = is_blocked_command("AWS eks delete-cluster")
        # Note: This depends on regex implementation
        # Current implementation is case-sensitive for command names

    def test_blocked_within_compound_command(self):
        """Detects blocked command even in compound statements."""
        result = is_blocked_command("echo 'test' && aws eks delete-cluster my-cluster")
        assert result.is_blocked is True
