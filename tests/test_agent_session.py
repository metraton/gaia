#!/usr/bin/env python3
"""
Tests for Agent Session Management System

Validates P0 implementation:
- Session creation and state management
- Resume logic
- Finalization
- Cleanup
"""

import pytest
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta, timezone
import sys

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / "tools" / "4-memory"))
from agent_session import AgentSession, create_session, should_resume, get_session, update_state, finalize_session


@pytest.fixture
def temp_session_dir():
    """Create temporary session directory for testing"""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


def test_create_session(temp_session_dir):
    """Test session creation"""
    manager = AgentSession(base_path=temp_session_dir)
    
    agent_id = manager.create_session(
        agent_name="test-agent",
        purpose="test_purpose",
        metadata={"test_key": "test_value"}
    )
    
    assert agent_id.startswith("agent-")
    assert (temp_session_dir / agent_id / "state.json").exists()
    
    # Verify session state
    session = manager.get_session(agent_id)
    assert session is not None
    assert session["agent_name"] == "test-agent"
    assert session["purpose"] == "test_purpose"
    assert session["phase"] == "initializing"
    assert session["resume_ready"] is False
    assert session["metadata"]["test_key"] == "test_value"


def test_update_state(temp_session_dir):
    """Test session state updates"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Update to approval phase (resumable)
    success = manager.update_state(agent_id, phase="approval")
    assert success is True
    
    session = manager.get_session(agent_id)
    assert session["phase"] == "approval"
    assert session["resume_ready"] is True
    assert len(session["history"]) == 1
    assert session["history"][0]["from_phase"] == "initializing"
    assert session["history"][0]["to_phase"] == "approval"


def test_should_resume_success(temp_session_dir):
    """Test should_resume returns True for valid session"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Set to resumable phase
    manager.update_state(agent_id, phase="approval")
    
    # Should resume
    assert manager.should_resume(agent_id) is True


def test_should_resume_not_resumable_phase(temp_session_dir):
    """Test should_resume returns False for non-resumable phase"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Stay in initializing (not resumable)
    assert manager.should_resume(agent_id) is False
    
    # Move to executing (not resumable)
    manager.update_state(agent_id, phase="executing")
    assert manager.should_resume(agent_id) is False


def test_should_resume_timeout(temp_session_dir):
    """Test should_resume returns False after timeout"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    manager.update_state(agent_id, phase="approval")
    
    # Manually set last_updated to 31 minutes ago
    session = manager.get_session(agent_id)
    old_time = datetime.now(timezone.utc) - timedelta(minutes=31)
    session["last_updated"] = old_time.isoformat()
    manager._save_session(agent_id, session)
    
    # Should not resume (timed out)
    assert manager.should_resume(agent_id) is False


def test_should_resume_too_many_errors(temp_session_dir):
    """Test should_resume returns False with too many errors"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    manager.update_state(agent_id, phase="approval")
    
    # Add 3 errors
    manager.update_state(agent_id, error="Error 1")
    manager.update_state(agent_id, error="Error 2")
    manager.update_state(agent_id, error="Error 3")
    
    # Should not resume (too many errors)
    assert manager.should_resume(agent_id) is False


def test_finalize_session(temp_session_dir):
    """Test session finalization"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Finalize
    success = manager.finalize_session(agent_id, outcome="completed", summary="Test completed")
    assert success is True
    
    session = manager.get_session(agent_id)
    assert session["phase"] == "completed"
    assert session["resume_ready"] is False
    assert session["summary"] == "Test completed"
    assert "finalized_at" in session
    assert "duration_seconds" in session


def test_phase_history(temp_session_dir):
    """Test phase transition history"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Go through multiple phases
    manager.update_state(agent_id, phase="investigating")
    manager.update_state(agent_id, phase="planning")
    manager.update_state(agent_id, phase="approval")
    manager.update_state(agent_id, phase="executing")
    
    session = manager.get_session(agent_id)
    assert len(session["history"]) == 4
    assert session["history"][0]["to_phase"] == "investigating"
    assert session["history"][1]["to_phase"] == "planning"
    assert session["history"][2]["to_phase"] == "approval"
    assert session["history"][3]["to_phase"] == "executing"


def test_list_sessions(temp_session_dir):
    """Test listing sessions with filters"""
    manager = AgentSession(base_path=temp_session_dir)
    
    # Create multiple sessions
    agent_id_1 = manager.create_session(agent_name="agent-1")
    agent_id_2 = manager.create_session(agent_name="agent-2")
    agent_id_3 = manager.create_session(agent_name="agent-1")
    
    # Set some to active
    manager.update_state(agent_id_1, phase="approval")
    manager.update_state(agent_id_2, phase="investigating")
    # agent_id_3 stays in initializing (not resumable)
    
    # List all
    all_sessions = manager.list_sessions()
    assert len(all_sessions) == 3
    
    # List active only
    active_sessions = manager.list_sessions(active_only=True)
    assert len(active_sessions) == 2
    
    # Filter by agent name
    agent_1_sessions = manager.list_sessions(agent_name="agent-1")
    assert len(agent_1_sessions) == 2


def test_cleanup_old_sessions(temp_session_dir):
    """Test cleanup of old sessions"""
    manager = AgentSession(base_path=temp_session_dir)
    
    # Create sessions
    agent_id_1 = manager.create_session(agent_name="old-agent")
    agent_id_2 = manager.create_session(agent_name="new-agent")
    
    # Make agent_id_1 old (25 hours ago)
    session_1 = manager.get_session(agent_id_1)
    old_time = datetime.now(timezone.utc) - timedelta(hours=25)
    session_1["last_updated"] = old_time.isoformat()
    manager._save_session(agent_id_1, session_1)
    
    # Cleanup sessions older than 24 hours
    deleted_count = manager.cleanup_old_sessions(hours=24)
    
    assert deleted_count == 1
    assert manager.get_session(agent_id_1) is None
    assert manager.get_session(agent_id_2) is not None


def test_metadata_merge(temp_session_dir):
    """Test metadata merging on update"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(
        agent_name="test-agent",
        metadata={"key1": "value1"}
    )
    
    # Add more metadata
    manager.update_state(agent_id, metadata={"key2": "value2"})
    manager.update_state(agent_id, metadata={"key3": "value3"})
    
    session = manager.get_session(agent_id)
    assert session["metadata"]["key1"] == "value1"
    assert session["metadata"]["key2"] == "value2"
    assert session["metadata"]["key3"] == "value3"


def test_error_tracking(temp_session_dir):
    """Test error tracking in sessions"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Add error
    manager.update_state(agent_id, error="Test error")
    
    session = manager.get_session(agent_id)
    assert session["error_count"] == 1
    assert session["last_error"]["message"] == "Test error"
    assert "timestamp" in session["last_error"]


def test_invalid_phase(temp_session_dir):
    """Test that invalid phases are rejected"""
    manager = AgentSession(base_path=temp_session_dir)
    agent_id = manager.create_session(agent_name="test-agent")
    
    # Try invalid phase
    success = manager.update_state(agent_id, phase="invalid_phase")
    assert success is False
    
    # Phase should remain unchanged
    session = manager.get_session(agent_id)
    assert session["phase"] == "initializing"


def test_nonexistent_session(temp_session_dir):
    """Test operations on nonexistent session"""
    manager = AgentSession(base_path=temp_session_dir)
    
    assert manager.get_session("nonexistent-id") is None
    assert manager.should_resume("nonexistent-id") is False
    assert manager.update_state("nonexistent-id", phase="approval") is False
    assert manager.finalize_session("nonexistent-id", outcome="completed") is False


def test_convenience_functions(temp_session_dir):
    """Test convenience functions work correctly"""
    # Note: Convenience functions use default path, not temp_session_dir
    # This is a smoke test to ensure they don't crash
    
    # These will create in .claude/session - cleanup manually if needed
    agent_id = create_session(agent_name="test-agent", purpose="test")
    assert agent_id is not None
    
    session = get_session(agent_id)
    assert session is not None
    
    success = update_state(agent_id, phase="approval")
    assert success is True
    
    resume = should_resume(agent_id)
    assert resume is True
    
    finalize = finalize_session(agent_id, outcome="completed")
    assert finalize is True
    
    # Cleanup
    manager = AgentSession()
    session_dir = manager.base_path / agent_id
    if session_dir.exists():
        shutil.rmtree(session_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
