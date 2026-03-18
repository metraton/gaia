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
    _extract_commands_from_evidence,
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
    """Test AGENT_STATUS-based exit code extraction via json:contract."""

    def test_complete_status_returns_zero(self):
        output = '```json:contract\n{"agent_status": {"plan_status": "COMPLETE", "agent_id": "a00001"}}\n```'
        assert _extract_exit_code_from_output(output) == 0

    def test_blocked_status_returns_one(self):
        output = '```json:contract\n{"agent_status": {"plan_status": "BLOCKED", "agent_id": "a00001"}}\n```'
        assert _extract_exit_code_from_output(output) == 1

    def test_no_status_returns_zero(self):
        output = "Agent output with no status block"
        assert _extract_exit_code_from_output(output) == 0

    def test_last_status_wins(self):
        # Only the first json:contract block is parsed, so this tests a single block
        output = '```json:contract\n{"agent_status": {"plan_status": "COMPLETE", "agent_id": "a00001"}}\n```'
        assert _extract_exit_code_from_output(output) == 0

    def test_no_false_positive_on_error_text(self):
        """Text like 'No errors found' should not trigger exit_code=1."""
        output = 'No errors found.\n```json:contract\n{"agent_status": {"plan_status": "COMPLETE", "agent_id": "a00001"}}\n```'
        assert _extract_exit_code_from_output(output) == 0


# ============================================================================
# Test _build_task_info_from_hook_data
# ============================================================================

class TestBuildTaskInfoExitCode:
    """Test that _build_task_info_from_hook_data includes exit_code."""

    def test_exit_code_from_complete_output(self):
        hook_data = {"agent_type": "cloud-troubleshooter", "agent_id": "a123"}
        output = '```json:contract\n{"agent_status": {"plan_status": "COMPLETE", "agent_id": "a123"}}\n```'
        task_info = _build_task_info_from_hook_data(hook_data, output)
        assert task_info["exit_code"] == 0

    def test_exit_code_from_blocked_output(self):
        hook_data = {"agent_type": "cloud-troubleshooter", "agent_id": "a123"}
        output = '```json:contract\n{"agent_status": {"plan_status": "BLOCKED", "agent_id": "a123"}}\n```'
        task_info = _build_task_info_from_hook_data(hook_data, output)
        assert task_info["exit_code"] == 1

    def test_exit_code_default_without_output(self):
        hook_data = {"agent_type": "cloud-troubleshooter", "agent_id": "a123"}
        task_info = _build_task_info_from_hook_data(hook_data)
        assert task_info["exit_code"] == 0


# ============================================================================
# Test _extract_commands_from_evidence
# ============================================================================

class TestExtractCommandsFromEvidence:
    """Test that _extract_commands_from_evidence filters out not-run commands."""

    def test_extracts_executed_commands(self):
        evidence_text = (
            '```json:contract\n'
            '{"evidence_report": {"commands_run": ['
            '{"command": "kubectl get pods", "result": "3 pods running"},'
            '{"command": "terraform plan", "result": "2 to add, 0 to destroy"}'
            ']}}\n```'
        )
        commands = _extract_commands_from_evidence(evidence_text)
        assert "kubectl get pods" in commands
        assert "terraform plan" in commands
        assert len(commands) == 2

    def test_skips_not_run_commands(self):
        """Commands marked as 'not run' should not appear in commands_executed."""
        evidence_text = (
            '```json:contract\n'
            '{"evidence_report": {"commands_run": ['
            '{"command": "kubectl get pods", "result": "3 pods running"},'
            '{"command": "not run"},'
            '{"command": "skipped"}'
            ']}}\n```'
        )
        commands = _extract_commands_from_evidence(evidence_text)
        assert "kubectl get pods" in commands
        assert len(commands) == 1

    def test_skips_not_executed_commands(self):
        evidence_text = (
            '```json:contract\n'
            '{"evidence_report": {"commands_run": ['
            '{"command": "not executed"},'
            '{"command": "kubectl get svc", "result": "2 services found"}'
            ']}}\n```'
        )
        commands = _extract_commands_from_evidence(evidence_text)
        assert "kubectl get svc" in commands
        assert len(commands) == 1

    def test_skips_na_commands(self):
        evidence_text = (
            '```json:contract\n'
            '{"evidence_report": {"commands_run": ['
            '{"command": "n/a"},'
            '{"command": "flux get ks", "result": "1 kustomization reconciled"}'
            ']}}\n```'
        )
        commands = _extract_commands_from_evidence(evidence_text)
        assert "flux get ks" in commands
        assert len(commands) == 1

    def test_skips_literal_none_entries(self):
        evidence_text = (
            '```json:contract\n'
            '{"evidence_report": {"commands_run": ["none", "not run"]}}\n```'
        )
        commands = _extract_commands_from_evidence(evidence_text)
        assert len(commands) == 0

    def test_empty_commands_section(self):
        evidence_text = (
            '```json:contract\n'
            '{"evidence_report": {"commands_run": []}}\n```'
        )
        commands = _extract_commands_from_evidence(evidence_text)
        assert len(commands) == 0


# ============================================================================
# Test subagent_stop_hook integration
# ============================================================================

class TestSubagentStopHookPostRemoval:
    """Test that subagent_stop_hook works after extract_and_store_discoveries removal."""

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_hook_no_longer_returns_discoveries(self, mock_episodic, structural_task_info):
        output = "All checks passed. No json:contract block."
        result = subagent_stop_hook(structural_task_info, output)
        assert result["success"] is True
        assert "discoveries" not in result
        assert result["response_contract"]["valid"] is False

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_invalid_contract_with_agent_id_creates_pending_repair(self, mock_episodic, structural_task_info):
        task_info = dict(structural_task_info)
        task_info["agent_id"] = "a12345"
        task_info["task_id"] = "a12345"
        # Contract has agent_status but no evidence_report -> invalid
        output = (
            '## Findings\n\n'
            '```json:contract\n'
            '{"agent_status": {"plan_status": "COMPLETE", "pending_steps": "[]", '
            '"next_action": "Done", "agent_id": "a12345"}}\n'
            '```\n'
        )
        result = subagent_stop_hook(task_info, output)
        assert result["success"] is True
        assert result["response_contract"]["valid"] is False

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_multi_surface_transcript_requires_consolidation_report(self, mock_episodic, structural_task_info, tmp_path, monkeypatch):
        transcript_path = tmp_path / "agent.jsonl"
        injected_payload = {
            "project_knowledge": {"application_services": {}},
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
        # Phase 2: context is delivered via additionalContext, payload persisted to disk
        user_prompt = "Investigate rollout failure after CI image change."
        transcript_path.write_text(
            json.dumps({"message": {"role": "user", "content": user_prompt}}) + "\n"
        )
        # Write payload to disk cache (as context_injector does in Phase 2)
        payload_dir = tmp_path / "gaia-context-payloads"
        payload_dir.mkdir()
        (payload_dir / "agent.json").write_text(json.dumps(injected_payload))
        monkeypatch.setenv("TMPDIR", str(tmp_path))

        task_info = dict(structural_task_info)
        task_info["agent_id"] = "a12345"
        task_info["task_id"] = "a12345"
        task_info["agent_transcript_path"] = str(transcript_path)

        # Has evidence + agent_status but NO consolidation_report
        contract = {
            "agent_status": {
                "plan_status": "COMPLETE",
                "pending_steps": "[]",
                "next_action": "Report findings to the orchestrator",
                "agent_id": "a12345",
            },
            "evidence_report": {
                "patterns_checked": ["compared existing deployment manifests"],
                "files_checked": ["apps/base/api/deployment.yaml"],
                "commands_run": ["`kubectl get pods -n api` -> not run"],
                "key_outputs": ["image tag differs from CI artifact"],
                "verbatim_outputs": ["none"],
                "cross_layer_impacts": ["gitops_desired_state may be out of sync with CI output"],
                "open_gaps": ["live rollout failure still needs gitops verification"],
            },
        }
        output = f"## Findings\n\n```json:contract\n{json.dumps(contract, indent=2)}\n```\n"
        result = subagent_stop_hook(task_info, output)
        assert result["success"] is True
        assert result["response_contract"]["valid"] is False
        assert result["response_contract"]["consolidation_required"] is True
        assert "CONSOLIDATION_REPORT" in result["response_contract"]["missing"]
