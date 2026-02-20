#!/usr/bin/env python3
"""
Integration tests for subagent_stop hook after extract_and_store_discoveries removal.

Validates:
1. _extract_exit_code_from_output correctly parses AGENT_STATUS
2. _build_task_info_from_hook_data passes exit_code through
3. subagent_stop_hook no longer returns 'discoveries' key
"""

import sys
import json
import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add hooks and tools to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(TOOLS_DIR))

from subagent_stop import (
    _extract_exit_code_from_output,
    _build_task_info_from_hook_data,
    subagent_stop_hook,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def isolate_env(tmp_path, monkeypatch):
    """Isolate all file I/O to tmp_path."""
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Create minimal directory structure
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def structural_task_info():
    """Task info for a project agent."""
    return {
        "task_id": "T001",
        "description": "Investigate Workload Identity binding",
        "agent": "cloud-troubleshooter",
        "tier": "T0",
        "tags": ["#gcp", "#debug"],
    }


# ============================================================================
# Test _extract_exit_code_from_output
# ============================================================================

class TestExtractExitCode:
    """Test AGENT_STATUS-based exit code extraction."""

    def test_complete_status_returns_zero(self):
        output = "Some text\nPLAN_STATUS: COMPLETE\nMore text"
        assert _extract_exit_code_from_output(output) == 0

    def test_blocked_status_returns_one(self):
        output = "Some text\nPLAN_STATUS: BLOCKED\nMore text"
        assert _extract_exit_code_from_output(output) == 1

    def test_no_status_returns_zero(self):
        output = "Agent output with no status block"
        assert _extract_exit_code_from_output(output) == 0

    def test_last_status_wins(self):
        output = "PLAN_STATUS: BLOCKED\nRetried...\nPLAN_STATUS: COMPLETE"
        assert _extract_exit_code_from_output(output) == 0

    def test_no_false_positive_on_error_text(self):
        """Text like 'No errors found' should not trigger exit_code=1."""
        output = "No errors found. All checks passed.\nPLAN_STATUS: COMPLETE"
        assert _extract_exit_code_from_output(output) == 0


# ============================================================================
# Test _build_task_info_from_hook_data
# ============================================================================

class TestBuildTaskInfoExitCode:
    """Test that _build_task_info_from_hook_data includes exit_code."""

    def test_exit_code_from_complete_output(self):
        hook_data = {"agent_type": "cloud-troubleshooter", "agent_id": "a123"}
        output = "PLAN_STATUS: COMPLETE"
        task_info = _build_task_info_from_hook_data(hook_data, output)
        assert task_info["exit_code"] == 0

    def test_exit_code_from_blocked_output(self):
        hook_data = {"agent_type": "cloud-troubleshooter", "agent_id": "a123"}
        output = "PLAN_STATUS: BLOCKED"
        task_info = _build_task_info_from_hook_data(hook_data, output)
        assert task_info["exit_code"] == 1

    def test_exit_code_default_without_output(self):
        hook_data = {"agent_type": "cloud-troubleshooter", "agent_id": "a123"}
        task_info = _build_task_info_from_hook_data(hook_data)
        assert task_info["exit_code"] == 0


# ============================================================================
# Test subagent_stop_hook integration
# ============================================================================

class TestSubagentStopHookPostRemoval:
    """Test that subagent_stop_hook works after extract_and_store_discoveries removal."""

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_hook_no_longer_returns_discoveries(self, mock_episodic, structural_task_info):
        output = "All checks passed.\nPLAN_STATUS: COMPLETE"
        result = subagent_stop_hook(structural_task_info, output)
        assert result["success"] is True
        assert "discoveries" not in result
