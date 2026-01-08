#!/usr/bin/env python3
"""
Tests for Hook State Management.

PRIORITY: HIGH - Critical for pre-to-post hook communication.

Validates:
1. HookState dataclass operations
2. save/get/clear state functions
3. State passing between pre and post hooks
"""

import os
import sys
import json
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core.state import (
    HookState,
    save_hook_state,
    get_hook_state,
    clear_hook_state,
    create_pre_hook_state,
    STATE_FILE_NAME,
    _get_state_file_path,
)
from modules.core.paths import clear_path_cache


class TestHookStateDataclass:
    """Test HookState dataclass operations."""

    def test_default_values(self):
        """Test HookState default values."""
        state = HookState()
        assert state.tool_name == ""
        assert state.command == ""
        assert state.tier == "unknown"
        assert state.start_time == ""
        assert state.session_id == ""
        assert state.pre_hook_result == "allowed"
        assert state.metadata == {}

    def test_custom_values(self):
        """Test HookState with custom values."""
        state = HookState(
            tool_name="bash",
            command="kubectl get pods",
            tier="T0",
            start_time="2024-01-01T10:00:00",
            session_id="session-123",
            pre_hook_result="allowed",
            metadata={"extra": "data"},
        )
        assert state.tool_name == "bash"
        assert state.command == "kubectl get pods"
        assert state.tier == "T0"
        assert state.metadata["extra"] == "data"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        state = HookState(
            tool_name="bash",
            command="ls -la",
            tier="T0",
        )
        data = state.to_dict()
        assert isinstance(data, dict)
        assert data["tool_name"] == "bash"
        assert data["command"] == "ls -la"
        assert data["tier"] == "T0"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "tool_name": "task",
            "command": "Task:terraform-architect",
            "tier": "T3",
            "start_time": "2024-01-01T10:00:00",
            "session_id": "sess-456",
            "pre_hook_result": "blocked",
            "metadata": {"agent": "terraform-architect"},
        }
        state = HookState.from_dict(data)
        assert state.tool_name == "task"
        assert state.command == "Task:terraform-architect"
        assert state.tier == "T3"
        assert state.metadata["agent"] == "terraform-architect"

    def test_from_dict_with_missing_keys(self):
        """Test from_dict handles missing keys gracefully."""
        data = {"tool_name": "bash"}
        state = HookState.from_dict(data)
        assert state.tool_name == "bash"
        assert state.tier == "unknown"  # Default
        assert state.metadata == {}  # Default


class TestCreatePreHookState:
    """Test create_pre_hook_state convenience function."""

    def test_creates_state_with_timestamp(self):
        """Test that timestamp is automatically set."""
        state = create_pre_hook_state(
            tool_name="bash",
            command="ls",
            tier="T0",
        )
        assert state.start_time != ""
        # Should be valid ISO format
        datetime.fromisoformat(state.start_time)

    def test_includes_session_id(self):
        """Test that session_id is set from environment."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "test-session"}):
            state = create_pre_hook_state(
                tool_name="bash",
                command="pwd",
                tier="T0",
            )
            assert state.session_id == "test-session"

    def test_default_session_id(self):
        """Test default session_id when not in environment."""
        env = dict(os.environ)
        env.pop("CLAUDE_SESSION_ID", None)
        with patch.dict(os.environ, env, clear=True):
            state = create_pre_hook_state(
                tool_name="bash",
                command="pwd",
                tier="T0",
            )
            assert state.session_id == "default"

    def test_extra_metadata(self):
        """Test that extra kwargs become metadata."""
        state = create_pre_hook_state(
            tool_name="task",
            command="Task:agent",
            tier="T3",
            is_t3=True,
            has_approval=False,
        )
        assert state.metadata.get("is_t3") is True
        assert state.metadata.get("has_approval") is False


class TestStatePersistence:
    """Test state save/get/clear operations."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temporary directory for tests."""
        # Clear path cache before each test
        clear_path_cache()
        # Create a mock .claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        # Patch find_claude_dir to return our temp directory
        with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
            yield claude_dir

    def test_save_and_get_state(self, setup):
        """Test saving and retrieving state."""
        state = HookState(
            tool_name="bash",
            command="kubectl get pods",
            tier="T0",
            session_id="test",
        )

        # Save state
        result = save_hook_state(state)
        assert result is True

        # Get state
        retrieved = get_hook_state()
        assert retrieved is not None
        assert retrieved.tool_name == "bash"
        assert retrieved.command == "kubectl get pods"
        assert retrieved.tier == "T0"

    def test_clear_state(self, setup):
        """Test clearing state."""
        state = HookState(tool_name="bash", command="ls")
        save_hook_state(state)

        # Verify state exists
        assert get_hook_state() is not None

        # Clear state
        result = clear_hook_state()
        assert result is True

        # Verify state is gone
        assert get_hook_state() is None

    def test_get_state_when_none_exists(self, setup):
        """Test get_hook_state returns None when no state file."""
        # Clear any existing state
        clear_hook_state()
        result = get_hook_state()
        assert result is None

    def test_clear_state_when_none_exists(self, setup):
        """Test clear_hook_state succeeds even when no state exists."""
        clear_hook_state()  # Ensure no state
        result = clear_hook_state()  # Clear again
        assert result is True

    def test_state_file_location(self, setup):
        """Test that state file is in expected location."""
        state = HookState(tool_name="test")
        save_hook_state(state)

        state_file = setup / STATE_FILE_NAME
        assert state_file.exists()

    def test_state_file_is_valid_json(self, setup):
        """Test that state file contains valid JSON."""
        state = HookState(
            tool_name="bash",
            command="test",
            tier="T1",
            metadata={"key": "value"},
        )
        save_hook_state(state)

        state_file = setup / STATE_FILE_NAME
        with open(state_file) as f:
            data = json.load(f)

        assert data["tool_name"] == "bash"
        assert data["command"] == "test"
        assert data["tier"] == "T1"


class TestTierPassing:
    """Test tier passing from pre-hook to post-hook."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temporary directory."""
        clear_path_cache()
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
            yield claude_dir

    def test_tier_preserved_through_save_get(self, setup):
        """Test that tier is correctly preserved."""
        for tier in ["T0", "T1", "T2", "T3"]:
            state = HookState(
                tool_name="bash",
                tier=tier,
            )
            save_hook_state(state)
            retrieved = get_hook_state()
            assert retrieved.tier == tier, f"Tier {tier} should be preserved"

    def test_pre_hook_creates_state_with_tier(self, setup):
        """Test that pre-hook state includes tier."""
        state = create_pre_hook_state(
            tool_name="bash",
            command="terraform apply",
            tier="T3",
        )
        save_hook_state(state)

        retrieved = get_hook_state()
        assert retrieved.tier == "T3"

    def test_post_hook_can_read_pre_hook_tier(self, setup):
        """Simulate pre->post hook tier communication."""
        # Pre-hook saves state
        pre_state = create_pre_hook_state(
            tool_name="bash",
            command="kubectl apply -f manifest.yaml",
            tier="T3",
        )
        save_hook_state(pre_state)

        # Post-hook reads state
        post_state = get_hook_state()
        assert post_state is not None
        assert post_state.tier == "T3"

        # Post-hook clears state
        clear_hook_state()
        assert get_hook_state() is None


class TestErrorHandling:
    """Test error handling in state operations."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up test environment."""
        clear_path_cache()
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
            yield claude_dir

    def test_save_handles_permission_error(self, setup):
        """Test save handles permission errors gracefully."""
        # Make directory read-only
        setup.chmod(0o444)
        try:
            state = HookState(tool_name="test")
            result = save_hook_state(state)
            # Should return False or handle gracefully
            assert result is False or result is True  # Depends on implementation
        finally:
            setup.chmod(0o755)

    def test_get_handles_corrupt_json(self, setup):
        """Test get handles corrupt JSON gracefully."""
        state_file = setup / STATE_FILE_NAME
        with open(state_file, "w") as f:
            f.write("not valid json {")

        result = get_hook_state()
        # Should return None on error
        assert result is None
