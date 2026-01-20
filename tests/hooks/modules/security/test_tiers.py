#!/usr/bin/env python3
"""
Tests for Security Tier Classification.

Validates:
1. SecurityTier enum
2. classify_command_tier() function
3. tier_from_string() function
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.tiers import (
    SecurityTier,
    classify_command_tier,
    tier_from_string,
    VALIDATION_PATTERNS,
)


class TestSecurityTierEnum:
    """Test SecurityTier enum."""

    def test_tier_values(self):
        """Test tier enum values."""
        assert SecurityTier.T0_READ_ONLY.value == "T0"
        assert SecurityTier.T1_VALIDATION.value == "T1"
        assert SecurityTier.T2_DRY_RUN.value == "T2"
        assert SecurityTier.T3_BLOCKED.value == "T3"

    def test_tier_str(self):
        """Test tier string representation."""
        assert str(SecurityTier.T0_READ_ONLY) == "T0"
        assert str(SecurityTier.T3_BLOCKED) == "T3"

    def test_requires_approval_property(self):
        """Test requires_approval property."""
        assert SecurityTier.T0_READ_ONLY.requires_approval is False
        assert SecurityTier.T1_VALIDATION.requires_approval is False
        assert SecurityTier.T2_DRY_RUN.requires_approval is False
        assert SecurityTier.T3_BLOCKED.requires_approval is True

    def test_description_property(self):
        """Test description property."""
        assert "Read-only" in SecurityTier.T0_READ_ONLY.description
        assert "Validation" in SecurityTier.T1_VALIDATION.description
        assert "Dry-run" in SecurityTier.T2_DRY_RUN.description
        assert "approval" in SecurityTier.T3_BLOCKED.description.lower()


class TestClassifyCommandTier:
    """Test classify_command_tier() function."""

    # T0 - Read-only operations
    @pytest.mark.parametrize("command", [
        "ls -la",
        "pwd",
        "cat file.txt",
        "head -n 10 file.log",
        "tail -f app.log",
        "grep pattern file",
        "git status",
        "git log",
        "git diff",
        "kubectl get pods",
        "kubectl describe pod test",
        "kubectl logs deployment/app",
        "terraform show",
        "terraform output",
        "terraform plan",  # Optimized as ultra-common T0 command
    ])
    def test_classifies_read_only_as_t0(self, command):
        """Test read-only commands are classified as T0."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T0_READ_ONLY, f"{command} should be T0"

    # T1 - Validation operations
    @pytest.mark.parametrize("command", [
        "terraform validate",
        "terraform fmt",
        "helm lint",
        "helm template chart/",
        # "kubectl apply --dry-run=client -f file.yaml",  # Now T2
    ])
    def test_classifies_validation_as_t1(self, command):
        """Test validation commands are classified as T1."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T1_VALIDATION, f"{command} should be T1"

    # T2 - Dry-run operations
    @pytest.mark.parametrize("command", [
        "kubectl apply --dry-run=server -f file.yaml",
        "helm install --dry-run release chart/",
        "git push --dry-run",
    ])
    def test_classifies_dry_run_as_t2(self, command):
        """Test dry-run commands are classified as T2."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T2_DRY_RUN, f"{command} should be T2"

    # T3 - Blocked/destructive operations
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "terraform destroy",
        "kubectl apply -f file.yaml",
        "kubectl delete pod test",
        "helm install release chart/",
        "rm -rf /",
        "git push origin main",
    ])
    def test_classifies_destructive_as_t3(self, command):
        """Test destructive commands are classified as T3."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T3_BLOCKED, f"{command} should be T3"

    def test_empty_command_is_t3(self):
        """Test empty command is classified as T3."""
        tier = classify_command_tier("")
        assert tier == SecurityTier.T3_BLOCKED

    def test_whitespace_command_is_t3(self):
        """Test whitespace-only command is T3."""
        tier = classify_command_tier("   ")
        assert tier == SecurityTier.T3_BLOCKED

    def test_unknown_command_is_t3(self):
        """Test unknown command defaults to T3."""
        tier = classify_command_tier("some_unknown_command --flag")
        assert tier == SecurityTier.T3_BLOCKED


class TestTierFromString:
    """Test tier_from_string() function."""

    @pytest.mark.parametrize("tier_str,expected", [
        ("T0", SecurityTier.T0_READ_ONLY),
        ("T1", SecurityTier.T1_VALIDATION),
        ("T2", SecurityTier.T2_DRY_RUN),
        ("T3", SecurityTier.T3_BLOCKED),
    ])
    def test_converts_valid_strings(self, tier_str, expected):
        """Test conversion of valid tier strings."""
        result = tier_from_string(tier_str)
        assert result == expected

    def test_case_insensitive(self):
        """Test conversion is case insensitive."""
        assert tier_from_string("t0") == SecurityTier.T0_READ_ONLY
        assert tier_from_string("T0") == SecurityTier.T0_READ_ONLY
        assert tier_from_string("t3") == SecurityTier.T3_BLOCKED

    def test_invalid_string_defaults_to_t3(self):
        """Test invalid strings default to T3 (safest)."""
        result = tier_from_string("invalid")
        assert result == SecurityTier.T3_BLOCKED

    def test_empty_string_defaults_to_t3(self):
        """Test empty string defaults to T3."""
        result = tier_from_string("")
        assert result == SecurityTier.T3_BLOCKED


class TestValidationPatterns:
    """Test validation pattern detection."""

    def test_validation_patterns_exist(self):
        """Test that validation patterns are defined."""
        assert len(VALIDATION_PATTERNS) > 0

    @pytest.mark.parametrize("keyword", [
        "validate",
        "plan",
        "template",
        "lint",
        "check",
        "fmt",
    ])
    def test_validation_keywords_detected(self, keyword):
        """Test validation keywords are detected."""
        command = f"tool {keyword} arguments"
        tier = classify_command_tier(command)
        # Should be T1 or lower (some may be T0 if also read-only)
        assert tier in [SecurityTier.T0_READ_ONLY, SecurityTier.T1_VALIDATION]


class TestEdgeCases:
    """Test edge cases in tier classification."""

    def test_command_with_dry_run_takes_precedence(self):
        """Test --dry-run flag gives T2 even for apply commands."""
        command = "kubectl apply -f file.yaml --dry-run=client"
        tier = classify_command_tier(command)
        # dry-run should give T1 or T2, not T3
        assert tier in [SecurityTier.T1_VALIDATION, SecurityTier.T2_DRY_RUN]

    def test_terraform_plan_is_not_blocked(self):
        """Test terraform plan is validation, not blocked."""
        tier = classify_command_tier("terraform plan")
        assert tier != SecurityTier.T3_BLOCKED

    def test_kubectl_get_with_output_still_t0(self):
        """Test kubectl get with -o flag is still T0."""
        tier = classify_command_tier("kubectl get pods -o json")
        assert tier == SecurityTier.T0_READ_ONLY

    def test_compound_safe_commands(self):
        """Test compound commands with pipes."""
        # This tests the raw classification - compound parsing
        # happens in the validator, not here
        tier = classify_command_tier("ls")
        assert tier == SecurityTier.T0_READ_ONLY
