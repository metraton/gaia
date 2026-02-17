#!/usr/bin/env python3
"""
Integration tests for discovery extraction in subagent_stop hook.

Validates:
1. Structural agent output creates pending updates
2. Operational-only output creates no pending updates
3. Non-project agents are skipped
4. Failures in discovery extraction don't break the hook
5. Episode references discovery if one was created
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

from subagent_stop import extract_and_store_discoveries, subagent_stop_hook


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def isolate_env(tmp_path, monkeypatch):
    """Isolate all file I/O to tmp_path."""
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Create pending-updates directory structure
    pending_dir = tmp_path / ".claude" / "project-context" / "pending-updates"
    pending_dir.mkdir(parents=True)
    (pending_dir / "applied").mkdir()

    # Create classification rules config
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    rules_src = Path(__file__).parent.parent.parent / "config" / "classification-rules.json"
    if rules_src.exists():
        import shutil
        shutil.copy(rules_src, config_dir / "classification-rules.json")

    # Copy required modules to tmp_path so imports work
    # Create hooks/modules/context structure
    hooks_ctx = tmp_path / "hooks" / "modules" / "context"
    hooks_ctx.mkdir(parents=True)
    classifier_src = HOOKS_DIR / "modules" / "context" / "discovery_classifier.py"
    if classifier_src.exists():
        import shutil
        shutil.copy(classifier_src, hooks_ctx / "discovery_classifier.py")
        (hooks_ctx / "__init__.py").write_text("")
        (tmp_path / "hooks" / "modules" / "__init__.py").write_text("")
        (tmp_path / "hooks" / "__init__.py").write_text("")

    # Create tools/context structure
    tools_ctx = tmp_path / "tools" / "context"
    tools_ctx.mkdir(parents=True)
    store_src = TOOLS_DIR / "context" / "pending_updates.py"
    if store_src.exists():
        import shutil
        shutil.copy(store_src, tools_ctx / "pending_updates.py")
        (tools_ctx / "__init__.py").write_text("")


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


@pytest.fixture
def non_project_task_info():
    """Task info for a non-project agent."""
    return {
        "task_id": "T002",
        "description": "Explore codebase",
        "agent": "Explore",
        "tier": "T0",
    }


# ============================================================================
# Test extract_and_store_discoveries
# ============================================================================

class TestExtractAndStoreDiscoveries:
    """Test the discovery extraction function."""

    def test_structural_output_creates_pending_update(self, structural_task_info):
        output = (
            "Investigation complete. Found that the WI binding "
            "references wrong project 'old-proj' but should be 'new-proj'."
        )
        ids = extract_and_store_discoveries(output, structural_task_info)
        assert len(ids) > 0

    def test_operational_output_creates_no_updates(self, structural_task_info):
        output = (
            "$ kubectl get pods -n common\n"
            "NAME                     READY   STATUS    RESTARTS   AGE\n"
            "auth-api-7f8d9c-abc12    1/1     Running   0          5d\n"
            "CPU usage: 45% average.\n"
        )
        ids = extract_and_store_discoveries(output, structural_task_info)
        assert len(ids) == 0

    def test_non_project_agent_skipped(self, non_project_task_info):
        output = "Discovered new service 'payment-api' running in namespace 'payments'."
        ids = extract_and_store_discoveries(output, non_project_task_info)
        assert len(ids) == 0

    def test_failure_returns_empty_list(self, structural_task_info):
        """Discovery extraction must never raise — returns [] on error."""
        # Pass None output to trigger internal error
        ids = extract_and_store_discoveries(None, structural_task_info)
        assert ids == []

    def test_episode_id_linked_to_discovery(self, structural_task_info, tmp_path):
        output = "Discovered new service 'payment-api' running in namespace 'payments'."
        ids = extract_and_store_discoveries(output, structural_task_info, episode_id="ep-test-123")
        assert len(ids) > 0

        # Read the index to verify episode_id was stored
        index_path = tmp_path / ".claude" / "project-context" / "pending-updates" / "pending-index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text())
            for uid, data in index.get("updates", {}).items():
                if uid in ids:
                    assert data.get("source_episode_id") == "ep-test-123"


# ============================================================================
# Test subagent_stop_hook integration
# ============================================================================

class TestSubagentStopHookDiscovery:
    """Test that subagent_stop_hook includes discovery extraction."""

    @patch("subagent_stop.capture_episodic_memory", return_value="ep-hook-001")
    def test_hook_returns_discoveries_count(self, mock_episodic, structural_task_info):
        output = "Discovered new service 'api-gw' running in namespace 'gateway'."
        result = subagent_stop_hook(structural_task_info, output)
        assert result["success"] is True
        assert "discoveries" in result
