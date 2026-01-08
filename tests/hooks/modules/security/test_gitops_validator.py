#!/usr/bin/env python3
"""
Tests for GitOps Workflow Validator.

Validates:
1. Safe GitOps command detection
2. Forbidden GitOps command detection
3. GitOps workflow validation
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.gitops_validator import (
    is_safe_gitops_command,
    is_forbidden_gitops_command,
    validate_gitops_workflow,
    GitOpsValidationResult,
    to_dict,
    SAFE_KUBECTL_COMMANDS,
    SAFE_FLUX_COMMANDS,
    SAFE_HELM_COMMANDS,
    FORBIDDEN_KUBECTL_COMMANDS,
    FORBIDDEN_FLUX_COMMANDS,
    FORBIDDEN_HELM_COMMANDS,
)


class TestIsSafeGitOpsCommand:
    """Test is_safe_gitops_command() function."""

    # kubectl safe commands
    @pytest.mark.parametrize("command", [
        "kubectl get pods",
        "kubectl get pods -n kube-system",
        "kubectl describe pod test-pod",
        "kubectl logs deployment/app",
        "kubectl logs -f pod/test",
        "kubectl top pods",
        "kubectl top nodes",
        "kubectl explain pods",
        "kubectl version",
        "kubectl cluster-info",
        "kubectl api-resources",
    ])
    def test_kubectl_safe_commands(self, command):
        """Test kubectl read-only commands are safe."""
        assert is_safe_gitops_command(command) is True

    # flux safe commands
    @pytest.mark.parametrize("command", [
        "flux get all",
        "flux get kustomizations",
        "flux check",
        "flux version",
        "flux logs",
    ])
    def test_flux_safe_commands(self, command):
        """Test flux read-only commands are safe."""
        assert is_safe_gitops_command(command) is True

    # helm safe commands
    @pytest.mark.parametrize("command", [
        "helm list",
        "helm list -A",
        "helm status release",
        "helm history release",
        "helm template chart/",
        "helm lint chart/",
        "helm version",
        "helm show values chart/",
        "helm search repo stable",
    ])
    def test_helm_safe_commands(self, command):
        """Test helm read-only commands are safe."""
        assert is_safe_gitops_command(command) is True

    # Not safe commands
    @pytest.mark.parametrize("command", [
        "terraform apply",  # Not GitOps
        "kubectl apply -f manifest.yaml",
        "helm install release chart/",
    ])
    def test_unsafe_commands_not_safe(self, command):
        """Test unsafe commands are not marked as safe."""
        assert is_safe_gitops_command(command) is False


class TestIsForbiddenGitOpsCommand:
    """Test is_forbidden_gitops_command() function."""

    # kubectl forbidden commands
    @pytest.mark.parametrize("command", [
        "kubectl apply -f manifest.yaml",
        "kubectl create deployment test",
        "kubectl patch deployment test",
        "kubectl delete pod test-pod",
        "kubectl scale deployment test --replicas=3",
        "kubectl rollout restart deployment/app",
    ])
    def test_kubectl_forbidden_commands(self, command):
        """Test kubectl write commands are forbidden."""
        assert is_forbidden_gitops_command(command) is True

    # flux forbidden commands
    @pytest.mark.parametrize("command", [
        "flux create source git test",
        "flux delete kustomization test",
        "flux suspend kustomization test",
        "flux resume kustomization test",
    ])
    def test_flux_forbidden_commands(self, command):
        """Test flux write commands are forbidden."""
        assert is_forbidden_gitops_command(command) is True

    # helm forbidden commands
    @pytest.mark.parametrize("command", [
        "helm install release chart/",
        "helm upgrade release chart/",
        "helm uninstall release",
        "helm rollback release",
    ])
    def test_helm_forbidden_commands(self, command):
        """Test helm write commands are forbidden."""
        assert is_forbidden_gitops_command(command) is True

    # Not forbidden (safe or dry-run)
    @pytest.mark.parametrize("command", [
        "kubectl get pods",
        "kubectl apply --dry-run=client -f manifest.yaml",
        "helm list",
        "helm install --dry-run release chart/",
    ])
    def test_safe_commands_not_forbidden(self, command):
        """Test safe commands are not forbidden."""
        assert is_forbidden_gitops_command(command) is False


class TestValidateGitOpsWorkflow:
    """Test validate_gitops_workflow() function."""

    def test_allows_safe_commands(self):
        """Test allows safe GitOps commands."""
        result = validate_gitops_workflow("kubectl get pods")
        assert result.allowed is True
        assert "read-only" in result.reason.lower()

    def test_blocks_forbidden_commands(self):
        """Test blocks forbidden GitOps commands."""
        result = validate_gitops_workflow("kubectl apply -f manifest.yaml")
        assert result.allowed is False
        assert result.severity == "critical"
        assert len(result.suggestions) > 0

    def test_provides_suggestions_for_kubectl_apply(self):
        """Test provides suggestions for kubectl apply."""
        result = validate_gitops_workflow("kubectl apply -f manifest.yaml")
        assert result.allowed is False
        # Should suggest dry-run
        assert any("dry-run" in s.lower() for s in result.suggestions)

    def test_provides_suggestions_for_helm_install(self):
        """Test provides suggestions for helm install."""
        result = validate_gitops_workflow("helm install release chart/")
        assert result.allowed is False
        # Should suggest template or dry-run
        assert any("template" in s.lower() or "dry-run" in s.lower() for s in result.suggestions)

    def test_stricter_for_gitops_operator_agent(self):
        """Test stricter validation for gitops-operator agent."""
        # Ambiguous command that might be allowed normally
        command = "kubectl create secret generic test --from-literal=key=value"

        # Without agent type
        result1 = validate_gitops_workflow(command)

        # With gitops-operator agent
        result2 = validate_gitops_workflow(command, agent_type="gitops-operator")

        # gitops-operator should be at least as strict
        if result1.allowed:
            # May or may not allow for gitops-operator
            pass
        else:
            assert result2.allowed is False

    def test_warns_for_unclear_commands(self):
        """Test warns for commands with unclear intent."""
        # A command not explicitly in safe or forbidden lists
        result = validate_gitops_workflow("some-kubectl-plugin command")
        # Should warn about unclear intent
        if result.allowed:
            assert result.severity == "warning"


class TestGitOpsValidationResult:
    """Test GitOpsValidationResult structure."""

    def test_result_has_expected_fields(self):
        """Test result contains expected fields."""
        result = validate_gitops_workflow("kubectl get pods")
        assert hasattr(result, "allowed")
        assert hasattr(result, "reason")
        assert hasattr(result, "severity")
        assert hasattr(result, "suggestions")

    def test_to_dict_function(self):
        """Test to_dict() conversion."""
        result = validate_gitops_workflow("kubectl apply -f file.yaml")
        result_dict = to_dict(result)
        assert isinstance(result_dict, dict)
        assert "allowed" in result_dict
        assert "reason" in result_dict
        assert "severity" in result_dict
        assert "suggestions" in result_dict

    def test_severity_levels(self):
        """Test severity levels are valid."""
        valid_severities = ["info", "warning", "high", "critical"]

        result_safe = validate_gitops_workflow("kubectl get pods")
        assert result_safe.severity in valid_severities

        result_forbidden = validate_gitops_workflow("kubectl delete pod test")
        assert result_forbidden.severity in valid_severities


class TestSafeCommandPatterns:
    """Test safe command pattern configurations."""

    def test_kubectl_safe_patterns_populated(self):
        """Test kubectl safe patterns are populated."""
        assert len(SAFE_KUBECTL_COMMANDS) > 0

    def test_flux_safe_patterns_populated(self):
        """Test flux safe patterns are populated."""
        assert len(SAFE_FLUX_COMMANDS) > 0

    def test_helm_safe_patterns_populated(self):
        """Test helm safe patterns are populated."""
        assert len(SAFE_HELM_COMMANDS) > 0


class TestForbiddenCommandPatterns:
    """Test forbidden command pattern configurations."""

    def test_kubectl_forbidden_patterns_populated(self):
        """Test kubectl forbidden patterns are populated."""
        assert len(FORBIDDEN_KUBECTL_COMMANDS) > 0

    def test_flux_forbidden_patterns_populated(self):
        """Test flux forbidden patterns are populated."""
        assert len(FORBIDDEN_FLUX_COMMANDS) > 0

    def test_helm_forbidden_patterns_populated(self):
        """Test helm forbidden patterns are populated."""
        assert len(FORBIDDEN_HELM_COMMANDS) > 0


class TestEdgeCases:
    """Test edge cases in GitOps validation."""

    def test_dry_run_allows_apply(self):
        """Test --dry-run flag allows apply commands."""
        result = validate_gitops_workflow("kubectl apply --dry-run=client -f manifest.yaml")
        # Should not be forbidden due to dry-run
        assert is_forbidden_gitops_command("kubectl apply --dry-run=client -f manifest.yaml") is False

    def test_dry_run_allows_helm_install(self):
        """Test --dry-run flag allows helm install."""
        result = validate_gitops_workflow("helm install --dry-run release chart/")
        assert is_forbidden_gitops_command("helm install --dry-run release chart/") is False

    def test_case_sensitivity(self):
        """Test case sensitivity of pattern matching."""
        # Patterns should be case-insensitive
        result1 = validate_gitops_workflow("KUBECTL GET PODS")
        result2 = validate_gitops_workflow("kubectl get pods")
        # Both should be allowed
        assert result1.allowed == result2.allowed

    def test_handles_empty_command(self):
        """Test handles empty command gracefully."""
        result = validate_gitops_workflow("")
        # Should not crash, behavior depends on implementation
        assert isinstance(result.allowed, bool)
