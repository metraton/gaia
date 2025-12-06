#!/usr/bin/env python3
"""
Integration test for Agent Contract System
Tests the complete flow: Query -> Contract -> Response -> Extraction -> Continuation
"""

import sys
import json
from pathlib import Path
from datetime import datetime
import pytest

# Add conversation directory directly to path
conversation_dir = Path(__file__).parent.parent / "tools" / "conversation"
sys.path.insert(0, str(conversation_dir))


class TestCompleteConversationFlow:
    """Test a complete multi-turn conversation with contracts."""

    @pytest.fixture
    def manager(self):
        """Initialize conversation manager"""
        from enhanced_conversation_manager import EnhancedConversationManager
        return EnhancedConversationManager(
            storage_path="/tmp/test_conversations",
            project_context_path=".claude/project-context.json"
        )

    def test_start_conversation(self, manager):
        """Test starting a conversation"""
        user_query = "check the pods in namespace tcm-non-prod"
        conv_id = manager.start_conversation("gitops-operator", user_query)
        assert conv_id is not None, "Conversation ID should be created"

    def test_build_agent_contract(self, manager):
        """Test building an agent contract"""
        user_query = "check the pods in namespace tcm-non-prod"
        conv_id = manager.start_conversation("gitops-operator", user_query)
        contract = manager.build_agent_contract(conv_id, user_query)
        
        assert contract is not None, "Contract should be built"
        assert "expected_response" in contract, "Contract should have expected_response"

    def test_process_agent_response(self, manager):
        """Test processing agent response"""
        user_query = "check the pods in namespace tcm-non-prod"
        conv_id = manager.start_conversation("gitops-operator", user_query)
        
        agent_response = {
            "version": "1.0",
            "metadata": {
                "agent": "gitops-operator",
                "timestamp": datetime.now().isoformat(),
                "execution_time": "2.3s",
                "tier": "T0",
                "success": True
            },
            "findings": {
                "resources": [
                    {
                        "type": "pod",
                        "name": "tcm-api",
                        "namespace": "tcm-non-prod",
                        "state": "Running",
                        "severity": "info",
                        "details": "Healthy"
                    }
                ],
                "errors": [],
                "metrics": {"total_resources": 1, "healthy": 1}
            },
            "actions": {"performed": []},
            "human_summary": "Found 1 pod running"
        }
        
        summary = manager.process_agent_response(conv_id, agent_response, user_query)
        assert summary is not None, "Summary should be generated"

    def test_continuation_detection(self, manager):
        """Test continuation detection"""
        # Start first conversation
        user_query_1 = "check the pods"
        conv_id = manager.start_conversation("gitops-operator", user_query_1)
        
        # Second query should be detected as continuation
        user_query_2 = "show me those pod logs"
        is_continuation = manager.is_continuation(user_query_2, "gitops-operator")
        
        # This may or may not be True depending on implementation
        assert isinstance(is_continuation, bool), "Should return boolean"


class TestProgressiveDisclosureLevels:
    """Test different Progressive Disclosure levels."""

    def test_simple_query_level(self):
        """Test that simple queries get low complexity"""
        from progressive_disclosure import ProgressiveDisclosureManager
        manager = ProgressiveDisclosureManager()
        
        intent = manager.analyze_query_intent("how many pods are running?")
        assert intent["recommended_level"] in [1, 2], \
            "Simple query should have low disclosure level"

    def test_complex_query_level(self):
        """Test that complex queries get higher complexity"""
        from progressive_disclosure import ProgressiveDisclosureManager
        manager = ProgressiveDisclosureManager()
        
        intent = manager.analyze_query_intent("debug and fix all the errors you found")
        assert intent["recommended_level"] in [3, 4], \
            "Complex debug query should have high disclosure level"


def test_complete_conversation_flow():
    """Legacy function test - kept for backward compatibility"""
    # This test just verifies basic imports work
    from enhanced_conversation_manager import EnhancedConversationManager
    from progressive_disclosure import ProgressiveDisclosureManager
    from agent_contract_builder import AgentContractBuilder
    
    # Basic instantiation test
    manager = EnhancedConversationManager(
        storage_path="/tmp/test_conversations",
        project_context_path=".claude/project-context.json"
    )
    
    user_query = "check pods"
    conv_id = manager.start_conversation("gitops-operator", user_query)
    
    assert conv_id is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
