#!/usr/bin/env python3
"""
Tests for episodic_capture_hook.py

Tests all phases of episodic memory capture throughout the workflow.
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools" / "4-memory"))

from episodic_capture_hook import (
    capture_phase_0,
    update_phase_3,
    update_phase_4,
    update_phase_5,
    get_memory,
    _extract_tags,
    _sanitize_context,
    _classify_operations
)
from episodic import EpisodicMemory


@pytest.fixture
def temp_memory_dir(tmp_path):
    """Create temporary episodic memory directory for testing."""
    memory_dir = tmp_path / "episodic-memory"
    memory_dir.mkdir(parents=True)
    return memory_dir


@pytest.fixture
def memory_instance(temp_memory_dir):
    """Create EpisodicMemory instance for testing."""
    return EpisodicMemory(base_path=temp_memory_dir)


class TestPhase0Capture:
    """Test Phase 0 episodic capture."""
    
    def test_capture_phase_0_basic(self, memory_instance, monkeypatch):
        """Test basic Phase 0 capture with minimal data."""
        # Monkeypatch the EpisodicMemory constructor to use our test instance
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = capture_phase_0(
            original_prompt="check the API",
            enriched_prompt="Check graphql-server API status in common namespace",
            clarification_data=None,
            command_context={"command": "general"}
        )
        
        assert episode_id is not None
        assert episode_id.startswith("ep_")
        
        # Verify episode was stored
        episode = memory_instance.get_episode(episode_id)
        assert episode is not None
        assert episode["prompt"] == "check the API"
        assert episode["enriched_prompt"] == "Check graphql-server API status in common namespace"
    
    def test_capture_phase_0_with_clarification(self, memory_instance, monkeypatch):
        """Test Phase 0 capture with clarification data."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        clarification_data = {
            "clarification_occurred": True,
            "ambiguity_score": 65,
            "patterns_detected": ["missing_environment", "missing_service"]
        }
        
        episode_id = capture_phase_0(
            original_prompt="deploy the service",
            enriched_prompt="Deploy graphql-server v1.0.177 to digital-eks-prod",
            clarification_data=clarification_data,
            command_context={"command": "deployment"}
        )
        
        assert episode_id is not None
        
        episode = memory_instance.get_episode(episode_id)
        assert episode["context"]["clarification"]["occurred"] is True
        assert episode["context"]["clarification"]["ambiguity_score"] == 65
        assert "missing_environment" in episode["context"]["clarification"]["patterns_detected"]
    
    def test_capture_phase_0_tags_extraction(self, memory_instance, monkeypatch):
        """Test that tags are properly extracted from prompts."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = capture_phase_0(
            original_prompt="deploy graphql-server to production cluster on AWS EKS",
            enriched_prompt="Deploy graphql-server v1.0.177 to digital-eks-prod on AWS",
            clarification_data=None,
            command_context=None
        )
        
        episode = memory_instance.get_episode(episode_id)
        tags = episode.get("tags", [])
        
        # Should extract relevant tags
        assert "kubernetes" in tags or "deployment" in tags or "aws" in tags
        assert "production" in tags


class TestPhase3Update:
    """Test Phase 3 (realization package) updates."""
    
    def test_update_phase_3_basic(self, memory_instance, monkeypatch):
        """Test basic Phase 3 update."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        # Create episode first
        episode_id = memory_instance.store_episode(
            prompt="test prompt",
            enriched_prompt="test enriched"
        )
        
        # Update with Phase 3 data
        success = update_phase_3(
            episode_id=episode_id,
            realization_package={
                "tier": "T2",
                "operations": ["kubectl get pods", "kubectl describe svc graphql-server"]
            },
            agent_name="devops-agent"
        )
        
        assert success is True
        
        # Verify update
        episode = memory_instance.get_episode(episode_id)
        assert "phase_3" in episode["context"]["workflow"]["phases_completed"]
        assert episode["context"]["workflow"]["tier"] == "T2"
        assert episode["context"]["workflow"]["agent_name"] == "devops-agent"
        assert episode["context"]["workflow"]["operation_count"] == 2
    
    def test_update_phase_3_operation_classification(self, memory_instance, monkeypatch):
        """Test that operations are properly classified."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = memory_instance.store_episode(
            prompt="test",
            enriched_prompt="test"
        )
        
        success = update_phase_3(
            episode_id=episode_id,
            realization_package={
                "tier": "T3",
                "operations": [
                    "kubectl apply -f deployment.yaml",
                    "kubectl delete pod old-pod",
                    "terraform plan"
                ]
            },
            agent_name="terraform-architect"
        )
        
        episode = memory_instance.get_episode(episode_id)
        op_types = episode["context"]["workflow"]["operation_types"]
        
        assert "create" in op_types  # kubectl apply
        assert "delete" in op_types  # kubectl delete
        assert "plan" in op_types    # terraform plan
    
    def test_update_phase_3_nonexistent_episode(self, memory_instance, monkeypatch):
        """Test Phase 3 update with nonexistent episode."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        success = update_phase_3(
            episode_id="ep_nonexistent_12345678",
            realization_package={"tier": "T1", "operations": []},
            agent_name="test-agent"
        )
        
        assert success is False


class TestPhase4Update:
    """Test Phase 4 (approval) updates."""
    
    def test_update_phase_4_approved(self, memory_instance, monkeypatch):
        """Test Phase 4 update with approval."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = memory_instance.store_episode(
            prompt="test",
            enriched_prompt="test"
        )
        
        success = update_phase_4(
            episode_id=episode_id,
            approval_decision="approved",
            tier="T3",
            user_feedback="Looks good to deploy"
        )
        
        assert success is True
        
        episode = memory_instance.get_episode(episode_id)
        assert episode["context"]["workflow"]["approval_decision"] == "approved"
        assert episode["context"]["workflow"]["user_feedback"] == "Looks good to deploy"
        assert episode["context"]["workflow"]["tier"] == "T3"
    
    def test_update_phase_4_rejected(self, memory_instance, monkeypatch):
        """Test Phase 4 update with rejection."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = memory_instance.store_episode(
            prompt="test",
            enriched_prompt="test"
        )
        
        success = update_phase_4(
            episode_id=episode_id,
            approval_decision="rejected",
            tier="T3",
            user_feedback="Not ready for production"
        )
        
        assert success is True
        
        episode = memory_instance.get_episode(episode_id)
        assert episode["context"]["workflow"]["approval_decision"] == "rejected"
        assert episode["outcome"] == "abandoned"
        assert episode["success"] is False


class TestPhase5Update:
    """Test Phase 5 (final outcome) updates."""
    
    def test_update_phase_5_success(self, memory_instance, monkeypatch):
        """Test Phase 5 update with success."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = memory_instance.store_episode(
            prompt="deploy service",
            enriched_prompt="deploy graphql-server v1.0.177"
        )
        
        success = update_phase_5(
            episode_id=episode_id,
            outcome="success",
            success=True,
            duration_seconds=45.3,
            commands_executed=[
                "kubectl apply -f deployment.yaml",
                "kubectl rollout status deployment/graphql-server"
            ],
            artifacts={"deployments": ["graphql-server-v1.0.177"]}
        )
        
        assert success is True
        
        episode = memory_instance.get_episode(episode_id)
        assert episode["outcome"] == "success"
        assert episode["success"] is True
        assert episode["duration_seconds"] == 45.3
        assert len(episode["commands_executed"]) == 2
        assert episode["context"]["workflow"]["artifacts"]["deployments"][0] == "graphql-server-v1.0.177"
    
    def test_update_phase_5_failed(self, memory_instance, monkeypatch):
        """Test Phase 5 update with failure."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_id = memory_instance.store_episode(
            prompt="deploy service",
            enriched_prompt="deploy test-service"
        )
        
        success = update_phase_5(
            episode_id=episode_id,
            outcome="failed",
            success=False,
            duration_seconds=10.5,
            commands_executed=["kubectl apply -f deployment.yaml"],
            error_message="ImagePullBackOff: Failed to pull image"
        )
        
        assert success is True
        
        episode = memory_instance.get_episode(episode_id)
        assert episode["outcome"] == "failed"
        assert episode["success"] is False
        assert episode["context"]["workflow"]["error_message"] == "ImagePullBackOff: Failed to pull image"
    
    def test_update_phase_5_duration_calculation(self, memory_instance, monkeypatch):
        """Test that duration is calculated if not provided."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        # Create episode with phase_0 timestamp
        episode_id = memory_instance.store_episode(
            prompt="test",
            enriched_prompt="test",
            context={
                "workflow": {
                    "phase_0_timestamp": datetime.now(timezone.utc).isoformat()
                }
            }
        )
        
        # Update without explicit duration
        success = update_phase_5(
            episode_id=episode_id,
            outcome="success",
            success=True,
            duration_seconds=None  # Will be calculated
        )
        
        assert success is True
        
        episode = memory_instance.get_episode(episode_id)
        # Duration should be calculated (will be small but > 0)
        assert episode.get("duration_seconds") is not None


class TestHelperFunctions:
    """Test helper functions."""
    
    def test_extract_tags_kubernetes(self):
        """Test tag extraction for Kubernetes prompts."""
        tags = _extract_tags(
            "deploy to kubernetes cluster",
            "deploy graphql-server to digital-eks-prod",
            {"command": "deployment"}
        )
        
        assert "kubernetes" in tags
        assert "deployment" in tags
    
    def test_extract_tags_terraform(self):
        """Test tag extraction for Terraform prompts."""
        tags = _extract_tags(
            "apply terraform changes",
            "terraform apply in production environment",
            None
        )
        
        assert "terraform" in tags
        assert "production" in tags
    
    def test_extract_tags_troubleshooting(self):
        """Test tag extraction for troubleshooting prompts."""
        tags = _extract_tags(
            "fix the error with database connection",
            "troubleshoot PostgreSQL connection timeout",
            None
        )
        
        assert "troubleshooting" in tags
    
    def test_sanitize_context_redacts_secrets(self):
        """Test that sensitive data is redacted."""
        context = {
            "user": "john",
            "password": "secret123",
            "api_key": "abc123xyz",
            "cluster": "prod-cluster",
            "token": "jwt_token_here"
        }
        
        sanitized = _sanitize_context(context)
        
        assert sanitized["user"] == "john"
        assert sanitized["cluster"] == "prod-cluster"
        assert sanitized["password"] == "[REDACTED]"
        assert sanitized["api_key"] == "[REDACTED]"
        assert sanitized["token"] == "[REDACTED]"
    
    def test_sanitize_context_truncates_long_strings(self):
        """Test that long strings are truncated."""
        context = {
            "short": "hello",
            "long": "x" * 1500
        }
        
        sanitized = _sanitize_context(context)
        
        assert sanitized["short"] == "hello"
        assert len(sanitized["long"]) <= 1020  # 1000 + "... [truncated]"
        assert "truncated" in sanitized["long"]
    
    def test_sanitize_context_limits_lists(self):
        """Test that large lists are limited."""
        context = {
            "items": list(range(100))
        }
        
        sanitized = _sanitize_context(context)
        
        assert len(sanitized["items"]) <= 51  # 50 + truncated marker
    
    def test_classify_operations(self):
        """Test operation classification."""
        operations = [
            "kubectl get pods -n common",
            "kubectl apply -f deployment.yaml",
            "kubectl delete pod old-pod",
            "terraform plan",
            "terraform apply",
            "git commit -m 'update'",
            "aws eks list-clusters"
        ]
        
        categories = _classify_operations(operations)
        
        assert "read" in categories
        assert "create" in categories
        assert "delete" in categories
        assert "plan" in categories
        assert "apply" in categories
        assert "git" in categories
        assert "cloud_cli" in categories


class TestFullWorkflow:
    """Test complete workflow integration."""
    
    def test_full_episode_lifecycle(self, memory_instance, monkeypatch):
        """Test complete episode lifecycle from Phase 0 to Phase 5."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        # Phase 0: Capture initial episode
        episode_id = capture_phase_0(
            original_prompt="deploy graphql-server",
            enriched_prompt="Deploy graphql-server v1.0.177 to digital-eks-prod",
            clarification_data={"clarification_occurred": True, "ambiguity_score": 55},
            command_context={"command": "deployment"}
        )
        
        assert episode_id is not None
        
        # Phase 3: Agent generates realization package
        success = update_phase_3(
            episode_id=episode_id,
            realization_package={
                "tier": "T3",
                "operations": ["kubectl apply -f deployment.yaml", "kubectl rollout status deployment/graphql-server"]
            },
            agent_name="devops-agent"
        )
        assert success is True
        
        # Phase 4: User approves
        success = update_phase_4(
            episode_id=episode_id,
            approval_decision="approved",
            tier="T3"
        )
        assert success is True
        
        # Phase 5: Execution completes
        success = update_phase_5(
            episode_id=episode_id,
            outcome="success",
            success=True,
            duration_seconds=42.0,
            commands_executed=["kubectl apply -f deployment.yaml"],
            artifacts={"deployments": ["graphql-server-v1.0.177"]}
        )
        assert success is True
        
        # Verify complete episode
        episode = memory_instance.get_episode(episode_id)
        assert episode["prompt"] == "deploy graphql-server"
        assert episode["outcome"] == "success"
        assert episode["success"] is True
        assert episode["duration_seconds"] == 42.0
        assert "phase_0" in episode["context"]["workflow"]["phases_completed"]
        assert "phase_3" in episode["context"]["workflow"]["phases_completed"]
        assert "phase_4" in episode["context"]["workflow"]["phases_completed"]
        assert "phase_5" in episode["context"]["workflow"]["phases_completed"]


class TestGetMemory:
    """Test get_memory helper function."""
    
    def test_get_memory_returns_instance(self, temp_memory_dir, monkeypatch):
        """Test that get_memory returns valid instance."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: EpisodicMemory(temp_memory_dir))
        
        memory = get_memory()
        assert memory is not None
        assert isinstance(memory, EpisodicMemory)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
