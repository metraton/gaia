#!/usr/bin/env python3
"""
Tests for Security Tier Classification.

Validates:
1. SecurityTier enum
2. classify_command_tier() function
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
    T1_PATTERNS,
    T2_PATTERNS,
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
        "git branch",
        "git branch -a",
        "git branch -v",
        "kubectl get pods",
        "kubectl describe pod test",
        "kubectl logs deployment/app",
        "terraform show",
        "terraform output",
    ])
    def test_classifies_read_only_as_t0(self, command):
        """Test read-only commands are classified as T0."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T0_READ_ONLY, f"{command} should be T0"

    # T1 - Local validation operations
    @pytest.mark.parametrize("command", [
        "terraform validate",
        "terraform fmt",
        "helm lint",
    ])
    def test_classifies_validation_as_t1(self, command):
        """Test local validation commands are classified as T1."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T1_VALIDATION, f"{command} should be T1"

    # T2 - Simulation operations (plan, template, diff)
    @pytest.mark.parametrize("command", [
        "terraform plan",
        "terragrunt plan",
        "helm template chart/",
        "kubectl diff -f file.yaml",
    ])
    def test_classifies_simulation_as_t2(self, command):
        """Test simulation commands are classified as T2."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T2_DRY_RUN, f"{command} should be T2"

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

    # T3 - Blocked/mutative operations
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "terraform destroy",
        "kubectl apply -f file.yaml",
        "kubectl delete pod test",
        "helm install release chart/",
        "rm -rf /",
        "git push origin main",
        "git branch -D main",
        "git branch -M old new",
        "git branch --delete feat",
    ])
    def test_classifies_mutative_as_t3(self, command):
        """Test mutative commands are classified as T3."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T3_BLOCKED, f"{command} should be T3"

    @pytest.mark.parametrize("command", [
        "git branch -d feature",  # -d is not a dangerous flag (soft delete)
        "git branch -m old new",  # -m is not a dangerous flag for git
    ])
    def test_soft_branch_ops_safe_by_elimination(self, command):
        """git branch -d/-m are safe by elimination (no mutative verb, no dangerous flag)."""
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T0_READ_ONLY, f"{command} should be T0 (safe)"

    def test_empty_command_is_t3(self):
        """Test empty command is classified as T3."""
        tier = classify_command_tier("")
        assert tier == SecurityTier.T3_BLOCKED

    def test_whitespace_command_is_t3(self):
        """Test whitespace-only command is T3."""
        tier = classify_command_tier("   ")
        assert tier == SecurityTier.T3_BLOCKED

    def test_unknown_command_is_safe_by_elimination(self):
        """Scenario #29: Unknown/unclassified -> T0 (safe by elimination, NOT T3).

        This is a critical design decision: unknown commands default to SAFE,
        not BLOCKED. The security model relies on blocked_commands.py and
        mutative_verbs.py to catch dangerous commands. Everything else is safe.
        """
        tier = classify_command_tier("some_unknown_command --flag")
        assert tier == SecurityTier.T0_READ_ONLY
        assert tier != SecurityTier.T3_BLOCKED  # Explicitly NOT T3

    def test_unknown_cli_with_unknown_verb_is_t0(self):
        """Another unknown combination -- must be T0, not T3."""
        tier = classify_command_tier("mycustomtool dostuff --verbose")
        assert tier == SecurityTier.T0_READ_ONLY


class TestTierPatterns:
    """Test T1/T2 pattern detection."""

    def test_t1_patterns_exist(self):
        """Test that T1 patterns are defined."""
        assert len(T1_PATTERNS) > 0

    def test_t2_patterns_exist(self):
        """Test that T2 patterns are defined."""
        assert len(T2_PATTERNS) > 0

    @pytest.mark.parametrize("keyword", ["validate", "lint", "check", "fmt"])
    def test_t1_keywords_detected(self, keyword):
        """Test T1 (local validation) keywords are detected."""
        command = f"tool {keyword} arguments"
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T1_VALIDATION, f"'{keyword}' should classify as T1"

    @pytest.mark.parametrize("keyword", ["plan", "template", "diff"])
    def test_t2_keywords_detected(self, keyword):
        """Test T2 (simulation) keywords are detected."""
        command = f"tool {keyword} arguments"
        tier = classify_command_tier(command)
        assert tier == SecurityTier.T2_DRY_RUN, f"'{keyword}' should classify as T2"


class TestEdgeCases:
    """Test edge cases in tier classification."""

    def test_command_with_dry_run_takes_precedence(self):
        """Test --dry-run flag gives T2 even for apply commands -- never T3."""
        command = "kubectl apply -f file.yaml --dry-run=client"
        tier = classify_command_tier(command)
        # dry-run MUST give T2 (simulation), never T3
        assert tier == SecurityTier.T2_DRY_RUN
        assert tier != SecurityTier.T3_BLOCKED

    def test_terraform_plan_is_t2(self):
        """Test terraform plan is simulation (T2), not blocked."""
        tier = classify_command_tier("terraform plan")
        assert tier == SecurityTier.T2_DRY_RUN

    def test_kubectl_get_with_output_still_t0(self):
        """Test kubectl get with -o flag is still T0."""
        tier = classify_command_tier("kubectl get pods -o json")
        assert tier == SecurityTier.T0_READ_ONLY

    def test_single_safe_command_is_t0(self):
        """Individual safe commands classify as T0.

        Note: compound parsing happens in bash_validator, not here.
        classify_command_tier() only handles single commands.
        """
        tier = classify_command_tier("ls")
        assert tier == SecurityTier.T0_READ_ONLY

        tier2 = classify_command_tier("git status")
        assert tier2 == SecurityTier.T0_READ_ONLY
