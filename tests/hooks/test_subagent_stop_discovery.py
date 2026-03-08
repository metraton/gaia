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
from modules.agents.response_contract import clear_contract_dir_cache
from modules.core.paths import clear_path_cache


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def isolate_env(tmp_path, monkeypatch):
    """Isolate all file I/O to tmp_path."""
    clear_path_cache()
    clear_contract_dir_cache()
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Create minimal directory structure
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    yield
    clear_path_cache()
    clear_contract_dir_cache()


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
        assert result["response_contract"]["valid"] is False
        assert result["contract_repair_pending"] is False

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_invalid_contract_with_agent_id_creates_pending_repair(self, mock_episodic, structural_task_info):
        task_info = dict(structural_task_info)
        task_info["agent_id"] = "a12345"
        task_info["task_id"] = "a12345"
        output = """\
## Findings

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Done
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""
        result = subagent_stop_hook(task_info, output)
        assert result["success"] is True
        assert result["response_contract"]["valid"] is False
        assert result["contract_repair_pending"] is True

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_multi_surface_transcript_requires_consolidation_report(self, mock_episodic, structural_task_info, tmp_path):
        transcript_path = tmp_path / "agent.jsonl"
        injected_payload = {
            "contract": {"application_services": {}},
            "surface_routing": {
                "active_surfaces": ["app_ci_tooling", "gitops_desired_state"],
                "primary_surface": "app_ci_tooling",
                "multi_surface": True,
            },
            "investigation_brief": {
                "cross_check_required": True,
                "consolidation_required": True,
            },
        }
        user_prompt = (
            "# Project Context (Auto-Injected)\n\n"
            f"{json.dumps(injected_payload, indent=2)}\n\n---\n\n"
            "# User Task\n\nInvestigate rollout failure after CI image change."
        )
        transcript_path.write_text(
            json.dumps({"message": {"role": "user", "content": user_prompt}}) + "\n"
        )

        task_info = dict(structural_task_info)
        task_info["agent_id"] = "a12345"
        task_info["task_id"] = "a12345"
        task_info["agent_transcript_path"] = str(transcript_path)

        output = """\
## Findings

<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- compared existing deployment manifests
FILES_CHECKED:
- apps/base/api/deployment.yaml
COMMANDS_RUN:
- `kubectl get pods -n api` -> not run
KEY_OUTPUTS:
- image tag differs from CI artifact
CROSS_LAYER_IMPACTS:
- gitops_desired_state may be out of sync with CI output
OPEN_GAPS:
- live rollout failure still needs gitops verification
<!-- /EVIDENCE_REPORT -->

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
PENDING_STEPS: []
NEXT_ACTION: Report findings to the orchestrator
AGENT_ID: a12345
<!-- /AGENT_STATUS -->
"""
        result = subagent_stop_hook(task_info, output)
        assert result["success"] is True
        assert result["response_contract"]["valid"] is False
        assert result["response_contract"]["consolidation_required"] is True
        assert "CONSOLIDATION_REPORT" in result["response_contract"]["missing"]
        assert result["contract_repair_pending"] is True
