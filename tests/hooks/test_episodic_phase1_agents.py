#!/usr/bin/env python3
"""
Tests for Phase 1: Episode-AgentSession Integration

Validates:
- Episode schema includes agents field
- get_agents_for_episode() extracts correct agent info
- Phase 5 captures agent_ids
- Backward compatibility (episodes without agents work)
- Full workflow integration
"""

import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools" / "4-memory"))

from episodic_capture_hook import (
    capture_phase_0,
    update_phase_5,
    get_agents_for_episode
)
from episodic import EpisodicMemory
from agent_session import AgentSession


@pytest.fixture
def temp_memory_dir(tmp_path):
    """Create temporary episodic memory directory."""
    memory_dir = tmp_path / "episodic-memory"
    memory_dir.mkdir(parents=True)
    return memory_dir


@pytest.fixture
def temp_session_dir(tmp_path):
    """Create temporary agent session directory."""
    session_dir = tmp_path / "sessions"
    session_dir.mkdir(parents=True)
    return session_dir


@pytest.fixture
def memory_instance(temp_memory_dir):
    """Create EpisodicMemory instance."""
    return EpisodicMemory(base_path=temp_memory_dir)


@pytest.fixture
def session_manager(temp_session_dir):
    """Create AgentSession manager instance."""
    return AgentSession(base_path=temp_session_dir)


class TestEpisodeSchemaWithAgents:
    """Test that Episode dataclass supports agents field."""
    
    def test_episode_with_agents_field(self, memory_instance):
        """Test storing episode with agents field."""
        agents = [
            {
                "agent_id": "agent-20260108-123456-abc123",
                "agent_name": "gitops-operator",
                "phases": ["initializing", "planning", "executing", "completed"],
                "duration_seconds": 45.2,
                "success": True
            }
        ]
        
        episode_id = memory_instance.store_episode(
            prompt="test prompt",
            enriched_prompt="test enriched",
            agents=agents
        )
        
        # Verify episode stored with agents
        episode = memory_instance.get_episode(episode_id)
        assert episode is not None
        assert "agents" in episode
        assert len(episode["agents"]) == 1
        assert episode["agents"][0]["agent_id"] == "agent-20260108-123456-abc123"
        assert episode["agents"][0]["agent_name"] == "gitops-operator"
        assert episode["agents"][0]["success"] is True
    
    def test_episode_without_agents_backward_compatible(self, memory_instance):
        """Test that episodes without agents field still work (backward compatibility)."""
        episode_id = memory_instance.store_episode(
            prompt="test prompt",
            enriched_prompt="test enriched"
        )
        
        episode = memory_instance.get_episode(episode_id)
        assert episode is not None
        # agents field should be None or not present
        assert episode.get("agents") is None


class TestGetAgentsForEpisode:
    """Test get_agents_for_episode helper function."""
    
    def test_get_agents_basic(self, session_manager):
        """Test extracting agent info from sessions."""
        # Create episode timestamp
        episode_time = datetime.now(timezone.utc)
        episode_timestamp = episode_time.isoformat()
        
        # Create agent session after episode creation
        agent_id = session_manager.create_session(
            agent_name="gitops-operator",
            purpose="test"
        )
        
        # Move through phases
        session_manager.update_state(agent_id, phase="planning")
        session_manager.update_state(agent_id, phase="approval")
        session_manager.update_state(agent_id, phase="executing")
        session_manager.finalize_session(agent_id, outcome="completed")
        
        # Extract agents with mocked AgentSession
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            agents = get_agents_for_episode(episode_timestamp)
        
        assert len(agents) == 1
        assert agents[0]["agent_id"] == agent_id
        assert agents[0]["agent_name"] == "gitops-operator"
        assert "planning" in agents[0]["phases"]
        assert "approval" in agents[0]["phases"]
        assert "executing" in agents[0]["phases"]
        assert agents[0]["success"] is True
        assert agents[0]["duration_seconds"] is not None
    
    def test_get_agents_multiple_sessions(self, session_manager):
        """Test extracting multiple agent sessions."""
        episode_time = datetime.now(timezone.utc)
        episode_timestamp = episode_time.isoformat()
        
        # Create multiple agent sessions
        agent_id_1 = session_manager.create_session(agent_name="gitops-operator")
        agent_id_2 = session_manager.create_session(agent_name="devops-agent")
        
        # Finalize both
        session_manager.finalize_session(agent_id_1, outcome="completed")
        session_manager.finalize_session(agent_id_2, outcome="failed")
        
        # Extract agents
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            agents = get_agents_for_episode(episode_timestamp)
        
        assert len(agents) == 2
        agent_names = [a["agent_name"] for a in agents]
        assert "gitops-operator" in agent_names
        assert "devops-agent" in agent_names
        
        # Check success flags
        gitops_agent = next(a for a in agents if a["agent_name"] == "gitops-operator")
        devops_agent = next(a for a in agents if a["agent_name"] == "devops-agent")
        assert gitops_agent["success"] is True
        assert devops_agent["success"] is False
    
    def test_get_agents_time_window(self, session_manager):
        """Test that only agents within time window are included."""
        episode_time = datetime.now(timezone.utc)
        episode_timestamp = episode_time.isoformat()
        
        # Create session within window (1 minute after)
        agent_id_1 = session_manager.create_session(agent_name="agent-recent")
        session_manager.finalize_session(agent_id_1, outcome="completed")
        
        # Create session outside window (manually set old timestamp)
        agent_id_2 = session_manager.create_session(agent_name="agent-old")
        session = session_manager.get_session(agent_id_2)
        old_time = episode_time - timedelta(minutes=10)  # 10 minutes before episode
        session["created_at"] = old_time.isoformat()
        session_manager._save_session(agent_id_2, session)
        
        # Extract agents
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            agents = get_agents_for_episode(episode_timestamp)
        
        # Only recent agent should be included
        assert len(agents) == 1
        assert agents[0]["agent_name"] == "agent-recent"
    
    def test_get_agents_no_sessions(self, session_manager):
        """Test when no agent sessions exist."""
        episode_timestamp = datetime.now(timezone.utc).isoformat()
        
        # No sessions created
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            agents = get_agents_for_episode(episode_timestamp)
        
        assert len(agents) == 0


class TestPhase5AgentCapture:
    """Test that Phase 5 captures agent information."""
    
    def test_phase_5_captures_agents(self, memory_instance, session_manager, monkeypatch):
        """Test Phase 5 captures agent info from sessions."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        # Create episode with phase_0_timestamp
        episode_time = datetime.now(timezone.utc)
        episode_id = memory_instance.store_episode(
            prompt="test prompt",
            enriched_prompt="test enriched",
            context={
                "workflow": {
                    "phase_0_timestamp": episode_time.isoformat()
                }
            }
        )
        
        # Create agent session
        agent_id = session_manager.create_session(agent_name="gitops-operator")
        session_manager.update_state(agent_id, phase="executing")
        session_manager.finalize_session(agent_id, outcome="completed")
        
        # Update Phase 5 with mocked AgentSession
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            success = update_phase_5(
                episode_id=episode_id,
                outcome="success",
                success=True,
                duration_seconds=30.0
            )
        
        assert success is True
        
        # Verify agents captured
        episode = memory_instance.get_episode(episode_id)
        assert "agents" in episode
        assert len(episode["agents"]) == 1
        assert episode["agents"][0]["agent_name"] == "gitops-operator"
        assert episode["agents"][0]["success"] is True
        assert episode["context"]["workflow"]["agents_count"] == 1
    
    def test_phase_5_no_agents(self, memory_instance, session_manager, monkeypatch):
        """Test Phase 5 when no agents exist."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        episode_time = datetime.now(timezone.utc)
        episode_id = memory_instance.store_episode(
            prompt="test",
            enriched_prompt="test",
            context={
                "workflow": {
                    "phase_0_timestamp": episode_time.isoformat()
                }
            }
        )
        
        # No agent sessions created
        
        # Update Phase 5
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            success = update_phase_5(
                episode_id=episode_id,
                outcome="success",
                success=True
            )
        
        assert success is True
        
        # Verify no agents field or empty
        episode = memory_instance.get_episode(episode_id)
        # agents field should not be present if no agents
        assert episode.get("agents") is None or len(episode.get("agents", [])) == 0


class TestFullWorkflowWithAgents:
    """Test complete workflow with agent tracking."""
    
    def test_complete_workflow_with_agent_tracking(self, memory_instance, session_manager, monkeypatch):
        """Test full workflow from Phase 0 to Phase 5 with agent tracking."""
        import episodic_capture_hook
        monkeypatch.setattr(episodic_capture_hook, 'EpisodicMemory', lambda: memory_instance)
        
        # Phase 0: Create episode
        episode_id = capture_phase_0(
            original_prompt="deploy service",
            enriched_prompt="Deploy graphql-server v1.0.177 to prod",
            command_context={"command": "deployment"}
        )
        
        assert episode_id is not None
        
        # Get phase_0_timestamp
        episode = memory_instance.get_episode(episode_id)
        phase_0_timestamp = episode["context"]["workflow"]["phase_0_timestamp"]
        
        # Simulate agent execution
        agent_id = session_manager.create_session(agent_name="gitops-operator", purpose="deployment")
        session_manager.update_state(agent_id, phase="investigating")
        session_manager.update_state(agent_id, phase="planning")
        session_manager.update_state(agent_id, phase="approval")
        session_manager.update_state(agent_id, phase="executing")
        session_manager.finalize_session(agent_id, outcome="completed", summary="Deployment successful")
        
        # Phase 5: Complete workflow
        with patch('episodic_capture_hook._AgentSession', return_value=session_manager):
            success = update_phase_5(
                episode_id=episode_id,
                outcome="success",
                success=True,
                duration_seconds=60.0,
                commands_executed=["kubectl apply -f deployment.yaml"],
                artifacts={"deployments": ["graphql-server-v1.0.177"]}
            )
        
        assert success is True
        
        # Verify complete episode with agents
        final_episode = memory_instance.get_episode(episode_id)
        
        # Check episode fields
        assert final_episode["outcome"] == "success"
        assert final_episode["success"] is True
        assert final_episode["duration_seconds"] == 60.0
        
        # Check agents captured
        assert "agents" in final_episode
        assert len(final_episode["agents"]) == 1
        
        agent_info = final_episode["agents"][0]
        assert agent_info["agent_id"] == agent_id
        assert agent_info["agent_name"] == "gitops-operator"
        assert "investigating" in agent_info["phases"]
        assert "planning" in agent_info["phases"]
        assert "approval" in agent_info["phases"]
        assert "executing" in agent_info["phases"]
        assert agent_info["success"] is True
        assert agent_info["duration_seconds"] is not None
        
        # Check workflow metadata
        assert final_episode["context"]["workflow"]["agents_count"] == 1


class TestBackwardCompatibility:
    """Test backward compatibility with existing episodes."""
    
    def test_existing_episodes_without_agents(self, memory_instance):
        """Test that existing episodes without agents field still work."""
        # Create old-style episode (no agents field)
        episode_id = memory_instance.store_episode(
            prompt="old episode",
            enriched_prompt="old episode enriched",
            outcome="success",
            success=True
        )
        
        # Should load without error
        episode = memory_instance.get_episode(episode_id)
        assert episode is not None
        assert episode["prompt"] == "old episode"
        assert episode["outcome"] == "success"
        
        # agents field should be None or not present
        assert episode.get("agents") is None
    
    def test_search_with_mixed_episodes(self, memory_instance):
        """Test searching works with episodes with and without agents."""
        # Create episode with agents
        with_agents = memory_instance.store_episode(
            prompt="deploy with agent kubernetes",
            enriched_prompt="deploy graphql service with gitops agent",
            agents=[{"agent_id": "agent-123", "agent_name": "gitops", "success": True}],
            tags=["kubernetes", "deployment"]
        )
        
        # Create episode without agents
        without_agents = memory_instance.store_episode(
            prompt="manual deploy terraform",
            enriched_prompt="manual terraform deployment",
            tags=["terraform", "deployment"]
        )
        
        # Search should work for both
        results = memory_instance.search_episodes("deploy deployment", max_results=10)
        # At least one should be found (might find both depending on scoring)
        assert len(results) >= 1
        
        # Both episodes should be findable
        episode_ids = [r.get("episode_id", r.get("id")) for r in results]
        assert with_agents in episode_ids
        assert without_agents in episode_ids


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
