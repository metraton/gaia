"""
Integration tests for hooks and permissions system.

Tests the integration between:
- pre_tool_use hook and PolicyEngine
- post_tool_use hook and AuditLogger
- Settings permissions and pattern matching
- GitOps security validation
- Tier-based command classification
"""

import pytest
import sys
import json
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "system"))

try:
    from pre_tool_use import PolicyEngine, SecurityTier, pre_tool_use_hook
    PRE_HOOK_AVAILABLE = True
except ImportError as e:
    print(f"Warning: pre_tool_use hook not available: {e}")
    PRE_HOOK_AVAILABLE = False

try:
    from post_tool_use import post_tool_use_hook, AuditLogger, MetricsCollector
    POST_HOOK_AVAILABLE = True
except ImportError as e:
    print(f"Warning: post_tool_use hook not available: {e}")
    POST_HOOK_AVAILABLE = False

from permissions_helpers import (
    get_permission_decision,
    matches_any_pattern,
    get_permission_level,
    merge_settings,
    load_merged_settings
)


@pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="pre_tool_use hook not available")
class TestPreToolUseHook:
    """Test pre_tool_use hook integration"""
    
    def test_hook_allows_read_operations(self):
        """Test that read operations are allowed"""
        result = pre_tool_use_hook("bash", {"command": "kubectl get pods"})
        assert result is None, "Read operations should be allowed"
    
    def test_hook_blocks_write_operations(self):
        """Test that write operations are blocked"""
        result = pre_tool_use_hook("bash", {"command": "kubectl apply -f manifest.yaml"})
        assert result is not None, "Write operations should be blocked"
        assert "blocked" in result.lower()
    
    def test_hook_allows_dry_run_operations(self):
        """Test that dry-run operations are allowed"""
        result = pre_tool_use_hook("bash", {"command": "kubectl apply -f manifest.yaml --dry-run=client"})
        assert result is None, "Dry-run operations should be allowed"
    
    def test_hook_blocks_terraform_apply(self):
        """Test that terraform apply is blocked"""
        result = pre_tool_use_hook("bash", {"command": "terraform apply"})
        assert result is not None
        assert "blocked" in result.lower()
    
    def test_hook_allows_terraform_plan(self):
        """Test that terraform plan is allowed"""
        result = pre_tool_use_hook("bash", {"command": "terraform plan"})
        assert result is None
    
    def test_hook_handles_empty_command(self):
        """Test that empty commands are rejected"""
        result = pre_tool_use_hook("bash", {"command": ""})
        assert result is not None
        assert "empty" in result.lower() or "error" in result.lower()
    
    def test_hook_allows_non_bash_tools(self):
        """Test that non-bash tools are allowed"""
        result = pre_tool_use_hook("read", {"file_path": "/tmp/test.txt"})
        assert result is None, "Non-bash tools should be allowed"
    
    def test_hook_blocks_git_push(self):
        """Test that git push is blocked"""
        result = pre_tool_use_hook("bash", {"command": "git push origin main"})
        assert result is not None
        assert "blocked" in result.lower()
    
    def test_hook_blocks_git_status(self):
        """Test that git status is blocked (not in allowed patterns)"""
        result = pre_tool_use_hook("bash", {"command": "git status"})
        # git status is not in allowed_read_operations, so it's blocked by default
        assert result is not None
    
    def test_hook_blocks_helm_install(self):
        """Test that helm install is blocked"""
        result = pre_tool_use_hook("bash", {"command": "helm install myapp ./chart"})
        assert result is not None
    
    def test_hook_allows_helm_template(self):
        """Test that helm template is allowed"""
        result = pre_tool_use_hook("bash", {"command": "helm template myapp ./chart"})
        assert result is None
    
    def test_hook_blocks_flux_reconcile(self):
        """Test that flux reconcile is blocked"""
        result = pre_tool_use_hook("bash", {"command": "flux reconcile kustomization flux-system"})
        assert result is not None
    
    def test_hook_allows_flux_get(self):
        """Test that flux get is allowed"""
        result = pre_tool_use_hook("bash", {"command": "flux get kustomizations"})
        assert result is None
    
    def test_hook_blocks_gcloud_create(self):
        """Test that gcloud create operations are blocked"""
        result = pre_tool_use_hook("bash", {"command": "gcloud compute instances create test-vm"})
        assert result is not None
    
    def test_hook_allows_gcloud_describe(self):
        """Test that gcloud describe operations are allowed"""
        result = pre_tool_use_hook("bash", {"command": "gcloud compute instances describe test-vm"})
        assert result is None
    
    def test_hook_blocks_docker_build(self):
        """Test that docker build is blocked"""
        result = pre_tool_use_hook("bash", {"command": "docker build -t myapp:latest ."})
        assert result is not None
    
    def test_hook_default_permit_for_docker_ps(self):
        """Test that docker ps follows default permit policy for unrecognized commands.
        
        The PolicyEngine uses a default-permit model for commands not explicitly
        in the allowed or blocked lists. docker ps is a read-only command but
        is not in the explicit allowed_read_operations list, so it gets default
        permit behavior (None = allowed) rather than explicit block.
        """
        result = pre_tool_use_hook("bash", {"command": "docker ps"})
        # Default permit: unrecognized commands are allowed (return None)
        # This is by design - the hook only blocks explicitly dangerous operations
        assert result is None, "docker ps should be allowed by default permit policy"
    
    def test_hook_provides_helpful_error_messages(self):
        """Test that blocked commands get helpful error messages"""
        result = pre_tool_use_hook("bash", {"command": "kubectl delete pod test-pod"})
        assert result is not None
        assert ("alternative" in result.lower() or "instead" in result.lower()), \
            "Error message should suggest alternatives"


@pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="PolicyEngine not available")
class TestPolicyEngine:
    """Test PolicyEngine command classification"""
    
    @pytest.fixture
    def policy_engine(self):
        """Create a PolicyEngine instance"""
        return PolicyEngine()
    
    def test_classify_read_operations(self, policy_engine):
        """Test classification of read operations"""
        tier = policy_engine.classify_command_tier("kubectl get pods")
        assert tier == SecurityTier.T0_READ_ONLY
    
    def test_classify_validation_operations(self, policy_engine):
        """Test classification of validation operations"""
        tier = policy_engine.classify_command_tier("terraform plan")
        assert tier == SecurityTier.T1_VALIDATION
    
    def test_classify_dry_run_operations(self, policy_engine):
        """Test classification of dry-run operations"""
        tier = policy_engine.classify_command_tier("kubectl apply -f test.yaml --dry-run=client")
        assert tier == SecurityTier.T2_DRY_RUN
    
    def test_classify_blocked_operations(self, policy_engine):
        """Test classification of blocked operations"""
        tier = policy_engine.classify_command_tier("terraform apply")
        assert tier == SecurityTier.T3_BLOCKED
    
    def test_validate_command_returns_tuple(self, policy_engine):
        """Test that validate_command returns proper tuple"""
        is_allowed, tier, reason = policy_engine.validate_command("bash", "kubectl get pods")
        assert isinstance(is_allowed, bool)
        assert isinstance(tier, str)
        assert isinstance(reason, str)
    
    def test_validate_allows_safe_commands(self, policy_engine):
        """Test that safe commands are allowed"""
        is_allowed, tier, reason = policy_engine.validate_command("bash", "ls -la")
        assert is_allowed is True
    
    def test_validate_blocks_dangerous_commands(self, policy_engine):
        """Test that dangerous commands are blocked"""
        is_allowed, tier, reason = policy_engine.validate_command("bash", "kubectl delete namespace production")
        assert is_allowed is False
        assert tier == SecurityTier.T3_BLOCKED
    
    def test_validate_handles_invalid_tool_name(self, policy_engine):
        """Test handling of invalid tool names"""
        is_allowed, tier, reason = policy_engine.validate_command(123, "test")
        assert is_allowed is False
        assert "invalid" in reason.lower()
    
    def test_validate_handles_invalid_command(self, policy_engine):
        """Test handling of invalid commands"""
        is_allowed, tier, reason = policy_engine.validate_command("bash", None)
        assert is_allowed is False
    
    def test_check_credentials_required(self, policy_engine):
        """Test credential requirement detection"""
        requires, warning = policy_engine.check_credentials_required("kubectl get pods")
        assert isinstance(requires, bool)
        assert isinstance(warning, str)


@pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="PolicyEngine not available")
class TestGitOpsSecurityValidation:
    """Test GitOps-specific security validation"""
    
    @pytest.fixture
    def policy_engine(self):
        return PolicyEngine()
    
    def test_kubectl_write_blocked(self, policy_engine):
        """Test that kubectl write operations are blocked"""
        is_allowed, tier, _ = policy_engine.validate_command("bash", "kubectl apply -f deployment.yaml")
        assert is_allowed is False
    
    def test_kubectl_read_allowed(self, policy_engine):
        """Test that kubectl read operations are allowed"""
        is_allowed, tier, _ = policy_engine.validate_command("bash", "kubectl get deployments")
        assert is_allowed is True
    
    def test_helm_upgrade_blocked(self, policy_engine):
        """Test that helm upgrade is blocked"""
        is_allowed, tier, _ = policy_engine.validate_command("bash", "helm upgrade myapp ./chart")
        assert is_allowed is False
    
    def test_helm_template_allowed(self, policy_engine):
        """Test that helm template is allowed"""
        is_allowed, tier, _ = policy_engine.validate_command("bash", "helm template myapp ./chart")
        assert is_allowed is True
    
    def test_flux_reconcile_blocked(self, policy_engine):
        """Test that flux reconcile is blocked"""
        is_allowed, tier, _ = policy_engine.validate_command("bash", "flux reconcile helmrelease myapp")
        assert is_allowed is False
    
    def test_flux_check_allowed(self, policy_engine):
        """Test that flux check is allowed"""
        is_allowed, tier, _ = policy_engine.validate_command("bash", "flux check")
        assert is_allowed is True
    
    def test_dry_run_kubectl_allowed(self, policy_engine):
        """Test that kubectl --dry-run is allowed"""
        is_allowed, tier, _ = policy_engine.validate_command(
            "bash", 
            "kubectl apply -f deployment.yaml --dry-run=client"
        )
        assert is_allowed is True
        assert tier == SecurityTier.T2_DRY_RUN
    
    def test_dry_run_helm_allowed(self, policy_engine):
        """Test that helm --dry-run is allowed"""
        is_allowed, tier, _ = policy_engine.validate_command(
            "bash",
            "helm install myapp ./chart --dry-run"
        )
        assert is_allowed is True
    
    def test_namespace_delete_blocked(self, policy_engine):
        """Test that namespace deletion is blocked"""
        is_allowed, tier, _ = policy_engine.validate_command(
            "bash",
            "kubectl delete namespace production"
        )
        assert is_allowed is False


class TestSettingsPermissionMatching:
    """Test settings-based permission matching"""
    
    @pytest.fixture
    def sample_settings(self):
        """Create sample settings for testing"""
        return {
            "permissions": {
                "bash": {
                    "deny": [
                        "rm -rf",
                        "terraform apply",
                        "git push"
                    ],
                    "ask": {
                        "terraform plan": "Confirm terraform plan execution?",
                        "kubectl apply.*--dry-run": "Confirm dry-run execution?"
                    },
                    "allow": [
                        "kubectl get",
                        "kubectl describe",
                        "terraform validate",
                        "ls",
                        "cat"
                    ]
                }
            }
        }
    
    def test_deny_priority_highest(self, sample_settings):
        """Test that deny has highest priority"""
        decision = get_permission_decision("rm -rf /tmp", "bash", sample_settings)
        assert decision == "deny"
    
    def test_ask_priority_over_allow(self, sample_settings):
        """Test that ask has priority over allow"""
        # Even if "terraform" might match allow patterns, specific ask should win
        decision = get_permission_decision("terraform plan", "bash", sample_settings)
        assert decision == "ask"
    
    def test_allow_works_when_no_higher_priority(self, sample_settings):
        """Test that allow works when no deny/ask matches"""
        decision = get_permission_decision("kubectl get pods", "bash", sample_settings)
        assert decision == "allow"
    
    def test_default_deny_when_no_match(self, sample_settings):
        """Test default deny when no patterns match"""
        decision = get_permission_decision("unknown-command", "bash", sample_settings)
        assert decision == "default_deny"
    
    def test_pattern_matching_with_wildcards(self):
        """Test pattern matching with wildcards"""
        patterns = ["kubectl get*", "helm template*"]
        assert matches_any_pattern("kubectl get pods", patterns) is True
        assert matches_any_pattern("helm template mychart", patterns) is True
        assert matches_any_pattern("kubectl apply", patterns) is False
    
    def test_pattern_matching_with_regex(self):
        """Test pattern matching with regex patterns"""
        patterns = [r"kubectl\s+apply.*--dry-run"]
        assert matches_any_pattern("kubectl apply -f test.yaml --dry-run=client", patterns) is True
        assert matches_any_pattern("kubectl apply -f test.yaml", patterns) is False
    
    def test_settings_without_permissions(self):
        """Test handling of settings without permissions section"""
        settings = {"other": "config"}
        decision = get_permission_decision("any command", "bash", settings)
        assert decision == "default_deny"
    
    def test_settings_without_tool(self):
        """Test handling when tool not in permissions"""
        settings = {"permissions": {"other_tool": {}}}
        decision = get_permission_decision("any command", "bash", settings)
        assert decision == "default_deny"


class TestAskPermissionTriggers:
    """Test that 'ask' permissions are properly triggered"""
    
    @pytest.fixture
    def ask_settings(self):
        return {
            "permissions": {
                "bash": {
                    "ask": {
                        "terraform apply": "Confirm terraform apply?",
                        "git push": "Confirm git push?",
                        "kubectl apply -f": "Confirm kubectl apply?"
                    },
                    "allow": []
                }
            }
        }
    
    def test_terraform_apply_triggers_ask(self, ask_settings):
        """Test that terraform apply triggers ask"""
        decision = get_permission_decision("terraform apply", "bash", ask_settings)
        assert decision == "ask"
    
    def test_git_push_triggers_ask(self, ask_settings):
        """Test that git push triggers ask"""
        decision = get_permission_decision("git push origin main", "bash", ask_settings)
        assert decision == "ask"
    
    def test_kubectl_apply_triggers_ask(self, ask_settings):
        """Test that kubectl apply triggers ask"""
        decision = get_permission_decision("kubectl apply -f deployment.yaml", "bash", ask_settings)
        assert decision == "ask"
    
    def test_other_commands_default_deny(self, ask_settings):
        """Test that non-ask commands get default deny"""
        decision = get_permission_decision("ls -la", "bash", ask_settings)
        assert decision == "default_deny"


class TestPermissionWorkflow:
    """Test complete permission workflow scenarios"""
    
    @pytest.fixture
    def complex_settings(self):
        """Settings with all permission types"""
        return {
            "permissions": {
                "bash": {
                    "deny": ["rm -rf", "terraform destroy"],
                    "ask": {
                        "terraform apply": "Confirm?",
                        "git push": "Confirm?"
                    },
                    "allow": ["terraform plan", "kubectl get", "ls"]
                }
            }
        }
    
    def test_workflow_deny_blocks_immediately(self, complex_settings):
        """Test that deny blocks without asking"""
        decision = get_permission_decision("rm -rf /tmp", "bash", complex_settings)
        assert decision == "deny"
    
    def test_workflow_ask_prompts_user(self, complex_settings):
        """Test that ask returns ask decision"""
        decision = get_permission_decision("terraform apply", "bash", complex_settings)
        assert decision == "ask"
    
    def test_workflow_allow_permits_immediately(self, complex_settings):
        """Test that allow permits without asking"""
        decision = get_permission_decision("terraform plan", "bash", complex_settings)
        assert decision == "allow"
    
    def test_workflow_default_deny_for_unknown(self, complex_settings):
        """Test that unknown commands get default deny"""
        decision = get_permission_decision("unknown-tool --do-something", "bash", complex_settings)
        assert decision == "default_deny"


@pytest.mark.skipif(not POST_HOOK_AVAILABLE, reason="post_tool_use hook not available")
class TestPostToolUseHook:
    """Test post_tool_use hook integration"""
    
    def test_hook_logs_execution(self):
        """Test that post hook logs execution"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Test logging functionality
            audit_logger = AuditLogger(log_dir=tmpdir)
            audit_logger.log_execution(
                "bash",
                {"command": "kubectl get pods"},
                "pod/test-pod   1/1   Running",
                0.5,
                0
            )
            
            # Check that log file was created
            log_files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(log_files) > 0, "Log files should be created"
    
    def test_hook_records_metrics(self):
        """Test that post hook records metrics"""
        with tempfile.TemporaryDirectory() as tmpdir:
            metrics_collector = MetricsCollector(metrics_dir=tmpdir)
            metrics_collector.record_execution(
                "bash",
                "kubectl get pods",
                0.5,
                True,
                "T0"
            )
            
            # Check that metrics file was created
            metrics_files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(metrics_files) > 0, "Metrics files should be created"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
