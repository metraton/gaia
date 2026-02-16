#!/usr/bin/env python3
"""
Tests for GitOps Validator.

PRIORITY: HIGH - Critical for GitOps workflow enforcement.

Validates:
1. Safe read-only commands allowed
2. Forbidden cluster-modifying commands blocked
3. Dry-run exceptions work correctly
4. Agent-specific stricter validation
5. Suggestion generation for blocked commands
6. Edge cases
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.gitops_validator import (
    GitOpsValidationResult,
    is_safe_gitops_command,
    is_forbidden_gitops_command,
    validate_gitops_workflow,
    to_dict,
    SAFE_KUBECTL_COMMANDS,
    SAFE_FLUX_COMMANDS,
    SAFE_HELM_COMMANDS,
    FORBIDDEN_KUBECTL_COMMANDS,
    FORBIDDEN_FLUX_COMMANDS,
    FORBIDDEN_HELM_COMMANDS,
)


class TestSafeKubectlCommands:
    """Test safe kubectl read-only commands."""

    @pytest.mark.parametrize("command", [
        "kubectl get pods",
        "kubectl get pods -n production",
        "kubectl describe pod my-pod",
        "kubectl logs deployment/app",
        "kubectl top nodes",
        "kubectl explain deployment",
        "kubectl version",
        "kubectl cluster-info",
        "kubectl config view",
        "kubectl api-resources",
        "kubectl api-versions",
    ])
    def test_safe_kubectl_commands_allowed(self, command):
        """Test read-only kubectl commands are detected as safe."""
        assert is_safe_gitops_command(command) is True

    @pytest.mark.parametrize("command", [
        "kubectl get pods -o wide",
        "kubectl get svc --all-namespaces",
        "kubectl describe node gke-node-1",
        "kubectl logs -f deployment/app --tail=100",
    ])
    def test_safe_kubectl_with_flags(self, command):
        """Test safe kubectl commands with additional flags."""
        assert is_safe_gitops_command(command) is True


class TestSafeFluxCommands:
    """Test safe Flux read-only commands."""

    @pytest.mark.parametrize("command", [
        "flux get all",
        "flux get sources git",
        "flux check",
        "flux version",
        "flux logs",
        "flux stats",
        "flux tree kustomization my-ks",
    ])
    def test_safe_flux_commands_allowed(self, command):
        """Test read-only flux commands are detected as safe."""
        assert is_safe_gitops_command(command) is True


class TestSafeHelmCommands:
    """Test safe Helm read-only commands."""

    @pytest.mark.parametrize("command", [
        "helm list",
        "helm list -A",
        "helm status my-release",
        "helm history my-release",
        "helm template my-chart ./chart",
        "helm lint ./chart",
        "helm version",
        "helm show values bitnami/redis",
        "helm search repo bitnami",
    ])
    def test_safe_helm_commands_allowed(self, command):
        """Test read-only helm commands are detected as safe."""
        assert is_safe_gitops_command(command) is True


class TestForbiddenKubectlCommands:
    """Test forbidden kubectl commands that modify cluster state."""

    @pytest.mark.parametrize("command", [
        "kubectl apply -f manifest.yaml",
        "kubectl create deployment my-app --image=nginx",
        "kubectl patch deployment my-app -p '{}'",
        "kubectl replace -f manifest.yaml",
        "kubectl delete pod my-pod",
        "kubectl delete namespace production",
        "kubectl scale deployment my-app --replicas=3",
        "kubectl rollout restart deployment my-app",
        "kubectl annotate pod my-pod key=value",
        "kubectl label node my-node env=prod",
    ])
    def test_forbidden_kubectl_commands_detected(self, command):
        """Test cluster-modifying kubectl commands are forbidden."""
        assert is_forbidden_gitops_command(command) is True

    def test_kubectl_apply_with_dry_run_allowed(self):
        """Test kubectl apply with --dry-run is NOT forbidden."""
        assert is_forbidden_gitops_command("kubectl apply --dry-run=client -f manifest.yaml") is False

    def test_kubectl_create_with_dry_run_allowed(self):
        """Test kubectl create with --dry-run is NOT forbidden."""
        assert is_forbidden_gitops_command("kubectl create deployment my-app --dry-run=client -o yaml") is False


class TestForbiddenFluxCommands:
    """Test forbidden Flux commands."""

    @pytest.mark.parametrize("command", [
        "flux create source git my-source --url=https://repo.git",
        "flux delete source git my-source",
        "flux suspend kustomization my-ks",
        "flux resume kustomization my-ks",
    ])
    def test_forbidden_flux_commands_detected(self, command):
        """Test cluster-modifying flux commands are forbidden."""
        assert is_forbidden_gitops_command(command) is True


class TestForbiddenHelmCommands:
    """Test forbidden Helm commands."""

    @pytest.mark.parametrize("command", [
        "helm install my-release chart/",
        "helm upgrade my-release chart/",
        "helm uninstall my-release",
        "helm rollback my-release 1",
    ])
    def test_forbidden_helm_commands_detected(self, command):
        """Test cluster-modifying helm commands are forbidden."""
        assert is_forbidden_gitops_command(command) is True

    def test_helm_install_with_dry_run_allowed(self):
        """Test helm install with --dry-run is NOT forbidden."""
        assert is_forbidden_gitops_command("helm install my-release chart/ --dry-run") is False

    def test_helm_upgrade_with_dry_run_allowed(self):
        """Test helm upgrade with --dry-run is NOT forbidden."""
        assert is_forbidden_gitops_command("helm upgrade my-release chart/ --dry-run") is False


class TestValidateGitopsWorkflow:
    """Test the main validate_gitops_workflow function."""

    def test_allows_safe_commands(self):
        """Test safe commands return allowed=True."""
        result = validate_gitops_workflow("kubectl get pods")
        assert result.allowed is True
        assert "Read-only" in result.reason

    def test_blocks_forbidden_commands(self):
        """Test forbidden commands return allowed=False."""
        result = validate_gitops_workflow("kubectl apply -f manifest.yaml")
        assert result.allowed is False
        assert "GitOps" in result.reason

    def test_provides_suggestions_for_kubectl_apply(self):
        """Test suggestions are provided for blocked kubectl apply."""
        result = validate_gitops_workflow("kubectl apply -f manifest.yaml")
        assert result.allowed is False
        assert len(result.suggestions) > 0
        assert any("dry-run" in s.lower() or "gitops" in s.lower() for s in result.suggestions)

    def test_provides_suggestions_for_helm_install(self):
        """Test suggestions are provided for blocked helm install."""
        result = validate_gitops_workflow("helm install my-release chart/")
        assert result.allowed is False
        assert len(result.suggestions) > 0

    def test_severity_critical_for_forbidden(self):
        """Test forbidden commands have critical severity."""
        result = validate_gitops_workflow("kubectl delete namespace production")
        assert result.allowed is False
        assert result.severity == "critical"

    def test_severity_info_for_safe(self):
        """Test safe commands have info severity."""
        result = validate_gitops_workflow("kubectl get pods")
        assert result.severity == "info"

    def test_unknown_commands_allowed_with_warning(self):
        """Test unrecognized commands are allowed with warning severity."""
        result = validate_gitops_workflow("some-unknown-command --flag")
        assert result.allowed is True
        assert result.severity == "warning"

    def test_case_insensitive_matching(self):
        """Test command matching is case insensitive."""
        result = validate_gitops_workflow("KUBECTL GET PODS")
        assert result.allowed is True


class TestGitopsOperatorStrictMode:
    """Test stricter validation for gitops-operator agent."""

    def test_blocks_apply_without_dry_run_for_gitops_operator(self):
        """Test gitops-operator cannot use apply without --dry-run."""
        result = validate_gitops_workflow(
            "some-tool apply -f config.yaml",
            agent_type="gitops-operator"
        )
        assert result.allowed is False
        assert "dry-run" in result.reason.lower()

    def test_blocks_create_without_dry_run_for_gitops_operator(self):
        """Test gitops-operator cannot use create without --dry-run."""
        result = validate_gitops_workflow(
            "some-tool create resource",
            agent_type="gitops-operator"
        )
        assert result.allowed is False

    def test_allows_apply_with_dry_run_for_gitops_operator(self):
        """Test gitops-operator can use apply with --dry-run."""
        result = validate_gitops_workflow(
            "some-tool apply --dry-run config.yaml",
            agent_type="gitops-operator"
        )
        assert result.allowed is True

    def test_non_gitops_agent_less_strict(self):
        """Test non-gitops agents are less strict on unknown apply commands."""
        result = validate_gitops_workflow(
            "some-tool apply -f config.yaml",
            agent_type="devops-developer"
        )
        # Should be allowed (not a known forbidden command)
        assert result.allowed is True


class TestGitOpsValidationResult:
    """Test GitOpsValidationResult dataclass."""

    def test_result_has_all_fields(self):
        """Test result contains all expected fields."""
        result = validate_gitops_workflow("kubectl get pods")
        assert hasattr(result, "allowed")
        assert hasattr(result, "reason")
        assert hasattr(result, "severity")
        assert hasattr(result, "suggestions")

    def test_default_suggestions_is_list(self):
        """Test suggestions defaults to empty list."""
        result = GitOpsValidationResult(allowed=True, reason="test")
        assert isinstance(result.suggestions, list)
        assert len(result.suggestions) == 0

    def test_default_severity_is_info(self):
        """Test default severity is 'info'."""
        result = GitOpsValidationResult(allowed=True, reason="test")
        assert result.severity == "info"


class TestToDict:
    """Test to_dict conversion function."""

    def test_converts_result_to_dict(self):
        """Test result is converted to dictionary."""
        result = validate_gitops_workflow("kubectl get pods")
        d = to_dict(result)
        assert isinstance(d, dict)
        assert "allowed" in d
        assert "reason" in d
        assert "severity" in d
        assert "suggestions" in d

    def test_dict_values_match_result(self):
        """Test dictionary values match the original result."""
        result = validate_gitops_workflow("kubectl delete pod my-pod")
        d = to_dict(result)
        assert d["allowed"] == result.allowed
        assert d["reason"] == result.reason
        assert d["severity"] == result.severity
        assert d["suggestions"] == result.suggestions


class TestConfigLists:
    """Test configuration lists are properly defined."""

    def test_safe_kubectl_commands_not_empty(self):
        """Test SAFE_KUBECTL_COMMANDS is populated."""
        assert len(SAFE_KUBECTL_COMMANDS) > 0

    def test_safe_flux_commands_not_empty(self):
        """Test SAFE_FLUX_COMMANDS is populated."""
        assert len(SAFE_FLUX_COMMANDS) > 0

    def test_safe_helm_commands_not_empty(self):
        """Test SAFE_HELM_COMMANDS is populated."""
        assert len(SAFE_HELM_COMMANDS) > 0

    def test_forbidden_kubectl_commands_not_empty(self):
        """Test FORBIDDEN_KUBECTL_COMMANDS is populated."""
        assert len(FORBIDDEN_KUBECTL_COMMANDS) > 0

    def test_forbidden_flux_commands_not_empty(self):
        """Test FORBIDDEN_FLUX_COMMANDS is populated."""
        assert len(FORBIDDEN_FLUX_COMMANDS) > 0

    def test_forbidden_helm_commands_not_empty(self):
        """Test FORBIDDEN_HELM_COMMANDS is populated."""
        assert len(FORBIDDEN_HELM_COMMANDS) > 0


class TestEdgeCases:
    """Test edge cases in GitOps validation."""

    def test_empty_command(self):
        """Test empty command is handled gracefully."""
        result = validate_gitops_workflow("")
        assert isinstance(result, GitOpsValidationResult)
        # Empty command is not safe and not forbidden, so should be warning
        assert result.allowed is True
        assert result.severity == "warning"

    def test_whitespace_command(self):
        """Test whitespace command is handled gracefully."""
        result = validate_gitops_workflow("   ")
        assert isinstance(result, GitOpsValidationResult)

    def test_non_k8s_command(self):
        """Test non-kubernetes commands are handled."""
        result = validate_gitops_workflow("ls -la /tmp")
        assert result.allowed is True

    def test_command_with_special_characters(self):
        """Test command with special characters."""
        result = validate_gitops_workflow("kubectl get pods -l 'app=my-app,env=prod'")
        assert result.allowed is True
