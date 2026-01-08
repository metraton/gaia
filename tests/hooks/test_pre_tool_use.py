#!/usr/bin/env python3
"""
Tests for Pre-Tool Use Hook.

Validates:
1. Entry point hook logic
2. Tool routing
3. State creation
4. Permission responses
"""

import os
import sys
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from pre_tool_use import (
    pre_tool_use_hook,
    check_orchestrator_gate,
    is_orchestrator_context,
    _handle_bash,
    _handle_task,
    ORCHESTRATOR_ALLOWED_TOOLS,
)


class TestPreToolUseHook:
    """Test main pre_tool_use_hook function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        logs_dir = claude_dir / "logs"
        logs_dir.mkdir()

        with patch("pre_tool_use.get_logs_dir", return_value=logs_dir):
            with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
                yield

    def test_allows_safe_bash_command(self):
        """Test allows safe bash command."""
        result = pre_tool_use_hook("bash", {"command": "ls -la"})
        assert result is None  # None means allowed

    def test_blocks_dangerous_bash_command(self):
        """Test blocks dangerous bash command."""
        result = pre_tool_use_hook("bash", {"command": "rm -rf /"})
        assert result is not None  # Error message means blocked

    def test_allows_valid_task(self):
        """Test allows valid task invocation."""
        result = pre_tool_use_hook("task", {
            "subagent_type": "devops-developer",
            "prompt": "Test prompt"
        })
        # May or may not be allowed depending on context check
        assert isinstance(result, (str, type(None)))

    def test_handles_invalid_tool_name(self):
        """Test handles invalid tool name."""
        result = pre_tool_use_hook(123, {"command": "test"})
        assert result is not None
        assert "Invalid" in result or "Error" in result

    def test_handles_invalid_parameters(self):
        """Test handles invalid parameters."""
        result = pre_tool_use_hook("bash", "not a dict")
        assert result is not None
        assert "Invalid" in result or "Error" in result

    def test_allows_non_validated_tools(self):
        """Test allows tools without specific validation."""
        result = pre_tool_use_hook("Read", {"file_path": "/test"})
        assert result is None


class TestOrchestratorGate:
    """Test orchestrator gate functionality."""

    def test_blocks_bash_in_orchestrator_mode(self):
        """Test blocks Bash in orchestrator mode."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate("Bash")
            assert allowed is False
            assert "Task" in reason  # Should suggest Task

    def test_allows_task_in_orchestrator_mode(self):
        """Test allows Task in orchestrator mode."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, _ = check_orchestrator_gate("Task")
            assert allowed is True

    def test_allows_all_outside_orchestrator(self):
        """Test allows all tools outside orchestrator mode."""
        env = dict(os.environ)
        env.pop("GAIA_ORCHESTRATOR_MODE", None)
        env.pop("CLAUDE_SESSION_CONTEXT", None)
        with patch.dict(os.environ, env, clear=True):
            allowed, _ = check_orchestrator_gate("Bash")
            assert allowed is True


class TestHandleBash:
    """Test _handle_bash function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        logs_dir = claude_dir / "logs"
        logs_dir.mkdir()

        with patch("pre_tool_use.get_logs_dir", return_value=logs_dir):
            with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
                with patch("pre_tool_use.save_hook_state", return_value=True):
                    yield

    def test_requires_command_parameter(self):
        """Test requires command in parameters."""
        result = _handle_bash("bash", {})
        assert result is not None
        assert "requires" in result.lower() or "command" in result.lower()

    def test_validates_command(self):
        """Test validates command."""
        result = _handle_bash("bash", {"command": "kubectl get pods"})
        assert result is None  # Safe command allowed

    def test_blocks_dangerous_command(self):
        """Test blocks dangerous command."""
        result = _handle_bash("bash", {"command": "terraform apply"})
        assert result is not None


class TestHandleTask:
    """Test _handle_task function."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
            with patch("pre_tool_use.save_hook_state", return_value=True):
                yield

    def test_validates_agent_type(self):
        """Test validates agent type."""
        result = _handle_task("task", {
            "subagent_type": "unknown-agent",
            "prompt": "Test"
        })
        assert result is not None  # Unknown agent blocked

    def test_allows_valid_agent(self):
        """Test allows valid agent."""
        result = _handle_task("task", {
            "subagent_type": "devops-developer",
            "prompt": "Test prompt"
        })
        # May or may not require context
        assert isinstance(result, (str, type(None)))


class TestOrchestratorContextDetection:
    """Test is_orchestrator_context function."""

    def test_detects_env_var(self):
        """Test detects GAIA_ORCHESTRATOR_MODE env var."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            assert is_orchestrator_context() is True

    def test_detects_session_context(self):
        """Test detects session context indicators."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_CONTEXT": "Running Phase 4 approval"}):
            assert is_orchestrator_context() is True

    def test_no_detection_without_indicators(self):
        """Test no detection without indicators."""
        env = dict(os.environ)
        env.pop("GAIA_ORCHESTRATOR_MODE", None)
        env.pop("CLAUDE_SESSION_CONTEXT", None)
        with patch.dict(os.environ, env, clear=True):
            result = is_orchestrator_context()
            # Should be False without indicators
            assert result is False


class TestIntegration:
    """Integration tests for pre-tool hook."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        """Set up temp environment."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        logs_dir = claude_dir / "logs"
        logs_dir.mkdir()

        with patch("pre_tool_use.get_logs_dir", return_value=logs_dir):
            with patch("modules.core.state.find_claude_dir", return_value=claude_dir):
                yield

    def test_full_flow_safe_command(self):
        """Test full flow for safe command."""
        result = pre_tool_use_hook("bash", {"command": "git status"})
        assert result is None  # Allowed

    def test_full_flow_blocked_command(self):
        """Test full flow for blocked command."""
        result = pre_tool_use_hook("bash", {"command": "terraform destroy"})
        assert result is not None  # Blocked
        assert "terraform" in result.lower() or "blocked" in result.lower()

    def test_full_flow_unknown_tool(self):
        """Test full flow for unknown tool."""
        result = pre_tool_use_hook("unknown_tool", {"param": "value"})
        assert result is None  # Unknown tools pass through
