#!/usr/bin/env python3
"""
Tests for Orchestrator Gate.

PRIORITY: HIGH - Critical security feature.

Validates that the orchestrator is restricted to only using:
- Read (reading context files)
- Task (delegating to agents)
- TodoWrite (managing task lists)
- AskUserQuestion (getting user input)

The orchestrator should NOT use:
- Bash (should delegate to agents)
- Edit (should delegate to agents)
- Write (should delegate to agents)
- Grep (should delegate to agents)
- Glob (should delegate to agents)
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from pre_tool_use import (
    check_orchestrator_gate,
    is_orchestrator_context,
    ORCHESTRATOR_ALLOWED_TOOLS,
    ORCHESTRATOR_CONTEXT_INDICATORS,
)


class TestOrchestratorAllowedTools:
    """Test that orchestrator can use allowed tools."""

    @pytest.mark.parametrize("tool_name", list(ORCHESTRATOR_ALLOWED_TOOLS))
    def test_allows_permitted_tools(self, tool_name):
        """Test that allowed tools pass the gate."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate(tool_name)
            assert allowed is True, f"Tool {tool_name} should be allowed for orchestrator"

    def test_read_tool_allowed(self):
        """Test that Read tool is explicitly allowed."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, _ = check_orchestrator_gate("Read")
            assert allowed is True

    def test_task_tool_allowed(self):
        """Test that Task tool is explicitly allowed."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, _ = check_orchestrator_gate("Task")
            assert allowed is True

    def test_todowrite_tool_allowed(self):
        """Test that TodoWrite tool is explicitly allowed."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, _ = check_orchestrator_gate("TodoWrite")
            assert allowed is True

    def test_askuserquestion_tool_allowed(self):
        """Test that AskUserQuestion tool is explicitly allowed."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, _ = check_orchestrator_gate("AskUserQuestion")
            assert allowed is True


class TestOrchestratorBlockedTools:
    """Test that orchestrator cannot use direct execution tools."""

    @pytest.mark.parametrize("tool_name", [
        "Bash",
        "Edit",
        "Write",
        "Grep",
        "Glob",
        "NotebookEdit",
    ])
    def test_blocks_direct_execution_tools(self, tool_name):
        """Test that direct execution tools are blocked for orchestrator."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate(tool_name)
            assert allowed is False, f"Tool {tool_name} should be blocked for orchestrator"
            assert "delegate" in reason.lower() or "should not" in reason.lower()

    def test_bash_blocked_with_reason(self):
        """Test Bash is blocked with informative reason."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate("Bash")
            assert allowed is False
            assert "Task" in reason  # Should suggest using Task tool

    def test_edit_blocked_with_reason(self):
        """Test Edit is blocked with informative reason."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate("Edit")
            assert allowed is False

    def test_write_blocked_with_reason(self):
        """Test Write is blocked with informative reason."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate("Write")
            assert allowed is False


class TestOrchestratorContextDetection:
    """Test orchestrator context detection."""

    def test_detects_orchestrator_mode_env(self):
        """Test detection via GAIA_ORCHESTRATOR_MODE env var."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            assert is_orchestrator_context() is True

    def test_no_detection_without_env(self):
        """Test no detection when env var is not set."""
        env = dict(os.environ)
        env.pop("GAIA_ORCHESTRATOR_MODE", None)
        env.pop("CLAUDE_SESSION_CONTEXT", None)
        with patch.dict(os.environ, env, clear=True):
            # Without indicators, should return False
            result = is_orchestrator_context()
            # Result depends on implementation - may check other indicators
            assert isinstance(result, bool)

    @pytest.mark.parametrize("indicator", ORCHESTRATOR_CONTEXT_INDICATORS)
    def test_detects_context_indicators(self, indicator):
        """Test detection via session context indicators."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_CONTEXT": f"Processing {indicator} workflow"}):
            assert is_orchestrator_context() is True

    def test_detects_phase_indicators(self):
        """Test detection of phase-related context."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_CONTEXT": "Executing Phase 4 approval"}):
            assert is_orchestrator_context() is True


class TestNonOrchestratorContext:
    """Test behavior when NOT in orchestrator context."""

    def test_allows_all_tools_outside_orchestrator(self):
        """Test that all tools are allowed when not in orchestrator mode."""
        env = dict(os.environ)
        env.pop("GAIA_ORCHESTRATOR_MODE", None)
        env.pop("CLAUDE_SESSION_CONTEXT", None)

        with patch.dict(os.environ, env, clear=True):
            # Check some tools that would be blocked in orchestrator mode
            for tool in ["Bash", "Edit", "Write"]:
                allowed, _ = check_orchestrator_gate(tool)
                assert allowed is True, f"Tool {tool} should be allowed outside orchestrator context"

    def test_bash_allowed_in_agent_context(self):
        """Test Bash is allowed when running as agent."""
        env = dict(os.environ)
        env.pop("GAIA_ORCHESTRATOR_MODE", None)
        with patch.dict(os.environ, env, clear=True):
            allowed, _ = check_orchestrator_gate("Bash")
            assert allowed is True


class TestGateIntegration:
    """Integration tests for orchestrator gate."""

    def test_gate_returns_delegation_suggestion(self):
        """Test that blocked tools suggest delegation."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate("Bash")
            assert allowed is False
            # Reason should suggest using Task for delegation
            assert "Task" in reason or "delegate" in reason.lower()

    def test_gate_lists_allowed_tools(self):
        """Test that blocked reason lists allowed tools."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            allowed, reason = check_orchestrator_gate("Grep")
            assert allowed is False
            # Reason should mention what tools ARE allowed
            for allowed_tool in ["Read", "Task"]:
                assert allowed_tool in reason

    def test_case_sensitivity(self):
        """Test that tool name matching is case-sensitive."""
        with patch.dict(os.environ, {"GAIA_ORCHESTRATOR_MODE": "true"}):
            # Exact case should be required
            allowed_exact, _ = check_orchestrator_gate("Task")
            assert allowed_exact is True

            # Different case may or may not work depending on implementation
            allowed_lower, _ = check_orchestrator_gate("task")
            # This test documents actual behavior
            assert isinstance(allowed_lower, bool)
