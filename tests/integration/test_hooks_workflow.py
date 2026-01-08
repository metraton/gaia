"""
Workflow integration tests for hooks system.

Tests complete workflows:
- Pre-hook validation → Command execution → Post-hook audit
- Settings merge → Permission resolution → Hook enforcement
- GitOps workflow validation
- Error handling and recovery
- Tier escalation scenarios
"""

import pytest
import sys
import json
import tempfile
import os
from pathlib import Path
from typing import Dict, Any

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "system"))

try:
    from pre_tool_use import pre_tool_use_hook
    from modules.security.tiers import SecurityTier
    from modules.tools.bash_validator import BashValidator
    PRE_HOOK_AVAILABLE = True
except ImportError:
    PRE_HOOK_AVAILABLE = False

try:
    from post_tool_use import post_tool_use_hook
    from modules.audit.logger import AuditLogger
    POST_HOOK_AVAILABLE = True
except ImportError:
    POST_HOOK_AVAILABLE = False

from permissions_helpers import (
    merge_settings,
    get_permission_decision,
    load_project_settings,
    load_shared_settings
)


class TestCompleteWorkflow:
    """Test complete hook workflow from validation to audit"""
    
    @pytest.mark.skipif(not (PRE_HOOK_AVAILABLE and POST_HOOK_AVAILABLE), 
                       reason="Hooks not available")
    def test_read_operation_complete_flow(self):
        """Test complete flow for read operation"""
        # Phase 1: Pre-hook validation
        pre_result = pre_tool_use_hook("bash", {"command": "kubectl get pods"})
        assert pre_result is None, "Read operation should pass pre-hook"
        
        # Phase 2: Command execution (simulated)
        command_result = "pod/test-pod   1/1   Running"
        duration = 0.5
        
        # Phase 3: Post-hook audit
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = AuditLogger(log_dir=tmpdir)
            audit_logger.log_execution(
                "bash",
                {"command": "kubectl get pods"},
                command_result,
                duration,
                0
            )
            
            # Verify audit log created
            log_files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(log_files) > 0
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_blocked_operation_stops_at_pre_hook(self):
        """Test that blocked operations don't proceed past pre-hook"""
        # Phase 1: Pre-hook validation (should block)
        pre_result = pre_tool_use_hook("bash", {"command": "terraform apply"})
        assert pre_result is not None, "Write operation should be blocked"
        
        # Phase 2: Command should NOT execute
        # (In real system, Claude Code stops here)
        
        # Phase 3: Post-hook should NOT be called
        # (Verified by system - we just document the expected behavior)
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_validation_operation_workflow(self):
        """Test workflow for validation operations (T1)"""
        # These should be allowed
        commands = [
            "terraform validate",
            "terraform plan",
            "helm template myapp ./chart",
            "kubectl apply -f test.yaml --dry-run=client"
        ]
        
        for command in commands:
            pre_result = pre_tool_use_hook("bash", {"command": command})
            assert pre_result is None, f"Validation command should be allowed: {command}"
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_tier_escalation_blocked(self):
        """Test that tier escalation from T1 to T3 is blocked"""
        # T1 validation - allowed
        pre_result = pre_tool_use_hook("bash", {"command": "terraform plan"})
        assert pre_result is None
        
        # T3 realization - blocked
        pre_result = pre_tool_use_hook("bash", {"command": "terraform apply"})
        assert pre_result is not None
        assert "blocked" in pre_result.lower()


class TestErrorHandlingWorkflow:
    """Test error handling in workflow scenarios"""
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_invalid_command_handling(self):
        """Test handling of invalid commands"""
        result = pre_tool_use_hook("bash", {"command": ""})
        assert result is not None
        assert "error" in result.lower() or "empty" in result.lower()
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_malformed_parameters_handling(self):
        """Test handling of malformed parameters"""
        # Missing command parameter
        result = pre_tool_use_hook("bash", {})
        assert result is not None
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_non_bash_tool_passes_through(self):
        """Test that non-bash tools pass through pre-hook"""
        result = pre_tool_use_hook("read", {"file_path": "/tmp/test.txt"})
        assert result is None, "Non-bash tools should pass through"
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="BashValidator not available")
    def test_bash_validator_error_handling(self):
        """Test that BashValidator handles errors gracefully"""
        validator = BashValidator()

        # Empty command
        result = validator.validate("")
        assert result.allowed is False

        # Whitespace command
        result = validator.validate("   ")
        assert result.allowed is False
    
    @pytest.mark.skipif(not POST_HOOK_AVAILABLE, reason="Post-hook not available")
    def test_audit_logger_creates_directories(self):
        """Test that AuditLogger creates necessary directories"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "nested" / "logs"
            audit_logger = AuditLogger(log_dir=str(log_dir))
            
            assert log_dir.exists(), "AuditLogger should create log directory"


class TestSettingsMergeWorkflow:
    """Test settings merge and permission resolution workflow"""
    
    @pytest.fixture
    def project_settings(self):
        """Project-specific settings"""
        return {
            "permissions": {
                "bash": {
                    "deny": ["rm -rf"],
                    "allow": ["ls", "cat"]
                }
            },
            "environment": "production"
        }
    
    @pytest.fixture
    def shared_settings(self):
        """Shared settings"""
        return {
            "permissions": {
                "bash": {
                    "deny": ["terraform destroy"],
                    "allow": ["kubectl get", "kubectl describe"],
                    "ask": {
                        "terraform apply": "Confirm?"
                    }
                }
            },
            "environment": "development"
        }
    
    def test_merge_combines_permissions(self, project_settings, shared_settings):
        """Test that merge combines permissions from both settings"""
        merged = merge_settings(project_settings, shared_settings)
        
        # Project deny should override shared deny (list replacement)
        assert "rm -rf" in merged["permissions"]["bash"]["deny"]
        
        # Shared deny is NOT in merged (project overrides)
        assert "terraform destroy" not in merged["permissions"]["bash"]["deny"]
        
        # But ask dict from shared should be in merged (not in project)
        assert "terraform apply" in merged["permissions"]["bash"]["ask"]
    
    def test_merge_project_overrides_shared(self, project_settings, shared_settings):
        """Test that project settings override shared settings"""
        merged = merge_settings(project_settings, shared_settings)
        
        # Project environment should override shared
        assert merged["environment"] == "production"
    
    def test_merged_settings_permission_resolution(self, project_settings, shared_settings):
        """Test permission resolution with merged settings"""
        merged = merge_settings(project_settings, shared_settings)
        
        # Test deny from project (project overrides shared)
        decision = get_permission_decision("rm -rf /tmp", "bash", merged)
        assert decision == "deny"
        
        # Test deny from shared - NOT in merged (project overrode deny list)
        decision = get_permission_decision("terraform destroy", "bash", merged)
        assert decision == "default_deny"  # Not in deny list, not in allow list
        
        # Test allow from project (project overrode allow list)
        decision = get_permission_decision("ls", "bash", merged)
        assert decision == "allow"
        
        # Test allow from shared - NOT in merged (project overrode allow list)
        decision = get_permission_decision("kubectl get pods", "bash", merged)
        assert decision == "default_deny"  # Not in allow list (project replaced it)
        
        # Test ask from shared (dicts are merged, not replaced)
        decision = get_permission_decision("terraform apply", "bash", merged)
        assert decision == "ask"


class TestGitOpsWorkflow:
    """Test GitOps-specific workflow scenarios"""
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_gitops_read_workflow(self):
        """Test GitOps read workflow"""
        read_commands = [
            "kubectl get pods -n production",
            "kubectl describe deployment myapp",
            "helm list -n production",
            "flux get kustomizations"
        ]
        
        for command in read_commands:
            result = pre_tool_use_hook("bash", {"command": command})
            assert result is None, f"GitOps read should be allowed: {command}"
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_gitops_write_blocked(self):
        """Test that GitOps write operations are blocked"""
        write_commands = [
            "kubectl apply -f deployment.yaml",
            "kubectl delete pod test-pod",
            "helm install myapp ./chart",
            "flux reconcile helmrelease myapp"
        ]
        
        for command in write_commands:
            result = pre_tool_use_hook("bash", {"command": command})
            assert result is not None, f"GitOps write should be blocked: {command}"
    
    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="Pre-hook not available")
    def test_gitops_validation_workflow(self):
        """Test GitOps validation workflow (dry-run, template)"""
        validation_commands = [
            "kubectl apply -f deployment.yaml --dry-run=client",
            "helm template myapp ./chart",
            "helm install myapp ./chart --dry-run"
        ]
        
        for command in validation_commands:
            result = pre_tool_use_hook("bash", {"command": command})
            assert result is None, f"GitOps validation should be allowed: {command}"


class TestTierEscalationWorkflow:
    """Test tier escalation scenarios"""

    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="BashValidator not available")
    def test_tier_progression(self):
        """Test that tiers progress logically"""
        validator = BashValidator()

        # T0: Read only
        result = validator.validate("kubectl get pods")
        assert result.tier == SecurityTier.T0_READ_ONLY

        # T1: Validation
        result = validator.validate("terraform validate")
        assert result.tier == SecurityTier.T1_VALIDATION

        # T2: Dry-run
        result = validator.validate("kubectl apply -f test.yaml --dry-run=client")
        assert result.tier == SecurityTier.T2_DRY_RUN

        # T3: Blocked
        result = validator.validate("terraform apply")
        assert result.tier == SecurityTier.T3_BLOCKED

    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="BashValidator not available")
    def test_cannot_skip_tiers(self):
        """Test that T0 commands cannot escalate directly to T3"""
        validator = BashValidator()

        # Read operation is T0
        result = validator.validate("kubectl get pods")
        assert result.allowed is True
        assert result.tier == SecurityTier.T0_READ_ONLY

        # Write operation is T3 (blocked)
        result = validator.validate("kubectl delete pod test")
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED

    @pytest.mark.skipif(not PRE_HOOK_AVAILABLE, reason="BashValidator not available")
    def test_dry_run_bridges_validation_to_realization(self):
        """Test that dry-run (T2) bridges validation (T1) to realization (T3)"""
        validator = BashValidator()

        # T1: Validation
        result = validator.validate("terraform plan")
        assert result.tier == SecurityTier.T1_VALIDATION

        # T2: Dry-run (approved path to T3)
        result = validator.validate("kubectl apply -f test.yaml --dry-run=client")
        assert result.tier == SecurityTier.T2_DRY_RUN

        # T3: Realization (requires explicit approval)
        result = validator.validate("terraform apply")
        assert result.tier == SecurityTier.T3_BLOCKED


class TestAuditTrailWorkflow:
    """Test audit trail creation and integrity"""
    
    @pytest.mark.skipif(not POST_HOOK_AVAILABLE, reason="Post-hook not available")
    def test_audit_trail_captures_all_fields(self):
        """Test that audit trail captures all required fields"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = AuditLogger(log_dir=tmpdir)
            
            audit_logger.log_execution(
                tool_name="bash",
                parameters={"command": "kubectl get pods"},
                result="pod/test-pod   1/1   Running",
                duration=0.5,
                exit_code=0
            )
            
            # Read the audit log
            log_files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(log_files) > 0
            
            with open(log_files[0], 'r') as f:
                log_entry = json.loads(f.read().strip())
            
            # Verify all required fields
            assert "timestamp" in log_entry
            assert "tool_name" in log_entry
            assert "command" in log_entry
            assert "duration_ms" in log_entry
            assert "exit_code" in log_entry
            assert log_entry["tool_name"] == "bash"
            assert log_entry["command"] == "kubectl get pods"
    
    @pytest.mark.skipif(not POST_HOOK_AVAILABLE, reason="Post-hook not available")
    def test_audit_trail_handles_large_output(self):
        """Test that audit trail handles large command output"""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_logger = AuditLogger(log_dir=tmpdir)
            
            # Simulate large output
            large_output = "line\n" * 10000
            
            audit_logger.log_execution(
                tool_name="bash",
                parameters={"command": "kubectl get all"},
                result=large_output,
                duration=2.5,
                exit_code=0
            )
            
            # Verify log was created without errors
            log_files = list(Path(tmpdir).glob("*.jsonl"))
            assert len(log_files) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
