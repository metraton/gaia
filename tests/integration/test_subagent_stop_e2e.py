#!/usr/bin/env python3
"""
End-to-end integration tests for subagent_stop hook.

Validates the FULL flow:
  1. Agent output with CONTEXT_UPDATE -> subagent_stop processes it
     -> project-context.json is updated -> audit trail created
  2. Stdin handler (Claude Code SubagentStop) -> processes correctly -> exit 0

Modules under test:
  - hooks/subagent_stop.py (subagent_stop_hook, _process_context_updates, stdin handler)
  - hooks/modules/context/context_writer.py (used internally)
  - tools/context/deep_merge.py (used internally by context_writer)
"""

import sys
import json
import os
import subprocess
import shutil
import pytest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
TOOLS_DIR = REPO_ROOT / "tools"
CONFIG_DIR = REPO_ROOT / "config"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(HOOKS_DIR / "modules" / "context"))
sys.path.insert(0, str(TOOLS_DIR))
sys.path.insert(0, str(TOOLS_DIR / "context"))


# ---------------------------------------------------------------------------
# Lazy imports
# ---------------------------------------------------------------------------

def _import_subagent_stop():
    """Import subagent_stop module at call time so pytest can collect tests."""
    import subagent_stop
    return subagent_stop


def _import_process_agent_output():
    """Import process_agent_output at call time."""
    from context_writer import process_agent_output
    return process_agent_output


# ---------------------------------------------------------------------------
# Test data constants
# ---------------------------------------------------------------------------

AGENT_OUTPUT_WITH_CONTEXT_UPDATE = """\
## Namespace Validation Report

20 namespaces found across all categories.

CONTEXT_UPDATE:
{
  "cluster_details": {
    "namespaces": {
      "application": ["adm", "dev", "nova-auth-dev"],
      "infrastructure": ["flux-system", "ingress-nginx", "istio-system", "keycloak", "gitlab-runner"],
      "system": ["default", "kube-system", "kube-public", "kube-node-lease"]
    },
    "total_namespace_count": 20
  }
}

<!-- AGENT_STATUS -->
PLAN_STATUS: COMPLETE
CURRENT_PHASE: Investigation
PENDING_STEPS: []
NEXT_ACTION: Task complete
AGENT_ID: cloud-troubleshooter
<!-- /AGENT_STATUS -->
"""

INITIAL_CONTEXT = {
    "metadata": {
        "version": "1.0",
        "cloud_provider": "gcp",
        "project_name": "test-project",
    },
    "sections": {
        "project_details": {"project_id": "test-project-id"},
        "cluster_details": {},
    },
}

TASK_INFO_CLOUD_TROUBLESHOOTER = {
    "task_id": "T-E2E-001",
    "description": "Validate cluster namespaces",
    "agent": "cloud-troubleshooter",
    "tier": "T0",
    "tags": ["#gcp", "#debug"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def write_context(context_file: Path, data: dict) -> None:
    """Write a project-context.json file."""
    context_file.parent.mkdir(parents=True, exist_ok=True)
    context_file.write_text(json.dumps(data, indent=2))


def read_context(context_file: Path) -> dict:
    """Read and parse a project-context.json file."""
    return json.loads(context_file.read_text())


def read_audit(context_file: Path) -> list:
    """Read the audit JSONL file next to context_file."""
    audit_path = context_file.parent / "context-audit.jsonl"
    if not audit_path.exists():
        return []
    entries = []
    for line in audit_path.read_text().strip().splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_env(tmp_path, monkeypatch):
    """Creates an isolated project environment mimicking a real project.

    Structure:
        tmp_path/
          .claude/
            project-context/
              project-context.json   (initial data, empty cluster_details)
            config/
              context-contracts.gcp.json  (copied from real config dir)
    """
    # Isolate file I/O
    monkeypatch.setenv("WORKFLOW_MEMORY_BASE_PATH", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    # Create directory structure
    claude_dir = tmp_path / ".claude"
    context_dir = claude_dir / "project-context"
    config_dir = claude_dir / "config"
    context_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)

    # Write initial project-context.json
    context_file = context_dir / "project-context.json"
    write_context(context_file, INITIAL_CONTEXT)

    # Copy real GCP contracts file
    real_contracts = CONFIG_DIR / "context-contracts.gcp.json"
    if real_contracts.exists():
        shutil.copy(real_contracts, config_dir / "context-contracts.gcp.json")

    # Create pending-updates directory (needed by extract_and_store_discoveries)
    pending_dir = context_dir / "pending-updates"
    pending_dir.mkdir(parents=True)
    (pending_dir / "applied").mkdir()

    # Copy classification rules
    rules_src = CONFIG_DIR / "classification-rules.json"
    if rules_src.exists():
        project_config_dir = tmp_path / "config"
        project_config_dir.mkdir(exist_ok=True)
        shutil.copy(rules_src, project_config_dir / "classification-rules.json")

    return {
        "tmp_path": tmp_path,
        "claude_dir": claude_dir,
        "context_dir": context_dir,
        "config_dir": config_dir,
        "context_file": context_file,
    }


# ============================================================================
# Test Suite 1: _process_context_updates E2E
# ============================================================================

class TestProcessContextUpdatesE2E:
    """Test that _process_context_updates correctly updates project-context.json
    when called with agent output containing a CONTEXT_UPDATE block."""

    def test_context_update_applied_to_project_context(self, project_env):
        """Full flow: agent output with CONTEXT_UPDATE -> project-context.json updated."""
        mod = _import_subagent_stop()
        context_file = project_env["context_file"]

        # Call _process_context_updates directly
        result = mod._process_context_updates(
            AGENT_OUTPUT_WITH_CONTEXT_UPDATE,
            TASK_INFO_CLOUD_TROUBLESHOOTER,
        )

        # Verify result indicates success
        assert result is not None, "Expected non-None result from _process_context_updates"
        assert result["updated"] is True
        assert "cluster_details" in result["sections_updated"]

        # Verify project-context.json was updated
        updated = read_context(context_file)
        namespaces = updated["sections"]["cluster_details"]["namespaces"]
        assert "adm" in namespaces["application"]
        assert "dev" in namespaces["application"]
        assert "nova-auth-dev" in namespaces["application"]
        assert "flux-system" in namespaces["infrastructure"]
        assert "kube-system" in namespaces["system"]

        # Verify total_namespace_count
        assert updated["sections"]["cluster_details"]["total_namespace_count"] == 20

        # Verify audit trail was created
        audit = read_audit(context_file)
        assert len(audit) > 0
        assert audit[0]["agent"] == "cloud-troubleshooter"
        assert audit[0]["success"] is True

    def test_config_dir_uses_claude_dir(self, project_env):
        """Verify config_dir is resolved to .claude/config/, not repo_root/config/."""
        mod = _import_subagent_stop()
        context_file = project_env["context_file"]
        config_dir = project_env["config_dir"]

        # Confirm contracts file exists in .claude/config/
        assert (config_dir / "context-contracts.gcp.json").exists(), (
            "Contracts file should be in .claude/config/"
        )

        # If the bug were still present, config_dir would be tmp_path/config
        # and the contracts file would not be found (fallback to legacy).
        # With the fix, it uses .claude/config/ and finds the real contracts.
        result = mod._process_context_updates(
            AGENT_OUTPUT_WITH_CONTEXT_UPDATE,
            TASK_INFO_CLOUD_TROUBLESHOOTER,
        )

        assert result is not None
        assert result["updated"] is True

    def test_no_context_update_in_output(self, project_env):
        """Agent output without CONTEXT_UPDATE should not modify project-context.json."""
        mod = _import_subagent_stop()
        context_file = project_env["context_file"]

        agent_output_no_update = (
            "## Agent Execution Complete\n\n"
            "Checked all pods. Everything looks healthy.\n"
        )

        result = mod._process_context_updates(
            agent_output_no_update,
            TASK_INFO_CLOUD_TROUBLESHOOTER,
        )

        # Context should remain unchanged
        updated = read_context(context_file)
        assert updated["sections"]["cluster_details"] == {}

        # Result should indicate no update
        if result is not None:
            assert result["updated"] is False


# ============================================================================
# Test Suite 2: Full subagent_stop_hook E2E
# ============================================================================

class TestSubagentStopHookE2E:
    """Test the full subagent_stop_hook() processing chain with context updates."""

    @patch("subagent_stop.capture_episodic_memory", return_value=None)
    def test_full_hook_with_context_update(self, mock_episodic, project_env):
        """Full hook flow: metrics + anomalies + context update."""
        mod = _import_subagent_stop()
        context_file = project_env["context_file"]

        result = mod.subagent_stop_hook(
            TASK_INFO_CLOUD_TROUBLESHOOTER,
            AGENT_OUTPUT_WITH_CONTEXT_UPDATE,
        )

        # Hook should succeed
        assert result["success"] is True
        assert result["metrics_captured"] is True
        assert result["context_updated"] is True

        # Verify project-context.json was actually updated
        updated = read_context(context_file)
        namespaces = updated["sections"]["cluster_details"]["namespaces"]
        assert len(namespaces["application"]) == 3
        assert "nova-auth-dev" in namespaces["application"]

        # Verify audit trail
        audit = read_audit(context_file)
        assert len(audit) > 0

    @patch("subagent_stop.capture_episodic_memory", return_value=None)
    def test_full_hook_without_context_update(self, mock_episodic, project_env):
        """Hook processes metrics even when no CONTEXT_UPDATE is present."""
        mod = _import_subagent_stop()

        agent_output_plain = (
            "## Investigation Complete\n\n"
            "All systems nominal. No issues found.\n"
        )

        result = mod.subagent_stop_hook(
            TASK_INFO_CLOUD_TROUBLESHOOTER,
            agent_output_plain,
        )

        assert result["success"] is True
        assert result["metrics_captured"] is True
        assert result["context_updated"] is False


# ============================================================================
# Test Suite 3: Stdin handler (subprocess integration)
# ============================================================================

class TestStdinHandler:
    """Test the stdin handler by invoking subagent_stop.py as a subprocess."""

    def test_stdin_handler_with_transcript(self, project_env, tmp_path):
        """Simulate Claude Code SubagentStop: pipe JSON via stdin with transcript."""
        context_file = project_env["context_file"]

        # Create a fake transcript JSONL file
        transcript_path = tmp_path / "agent_transcript.jsonl"
        transcript_lines = [
            json.dumps({
                "role": "assistant",
                "content": AGENT_OUTPUT_WITH_CONTEXT_UPDATE,
            }),
        ]
        transcript_path.write_text("\n".join(transcript_lines))

        # Build the stdin payload matching Claude Code SubagentStop schema
        stdin_payload = json.dumps({
            "hook_event_name": "SubagentStop",
            "session_id": "test-session-e2e-001",
            "agent_type": "cloud-troubleshooter",
            "agent_id": "agent-e2e-001",
            "transcript_path": str(tmp_path / "session_transcript.jsonl"),
            "agent_transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
            "stop_hook_active": True,
            "permission_mode": "default",
        })

        # Run subagent_stop.py as subprocess
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "subagent_stop.py")],
            input=stdin_payload,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "WORKFLOW_MEMORY_BASE_PATH": str(tmp_path),
            },
            timeout=30,
        )

        # Verify it exits 0
        assert result.returncode == 0, (
            f"subagent_stop.py exited with code {result.returncode}.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # Parse stdout as JSON
        stdout_lines = result.stdout.strip().splitlines()
        # The result JSON should be the last line (logging may precede it)
        result_json = None
        for line in reversed(stdout_lines):
            try:
                result_json = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

        assert result_json is not None, (
            f"Expected JSON output from subagent_stop.py, got:\n{result.stdout}"
        )
        assert result_json["success"] is True

        # Verify project-context.json was updated by the subprocess
        updated = read_context(context_file)
        namespaces = updated["sections"]["cluster_details"].get("namespaces", {})
        assert "application" in namespaces
        assert "nova-auth-dev" in namespaces["application"]

        # Verify audit trail
        audit = read_audit(context_file)
        assert len(audit) > 0

    def test_stdin_handler_empty_transcript(self, project_env, tmp_path):
        """Stdin handler should handle missing transcript gracefully."""
        stdin_payload = json.dumps({
            "hook_event_name": "SubagentStop",
            "session_id": "test-session-e2e-002",
            "agent_type": "cloud-troubleshooter",
            "agent_id": "agent-e2e-002",
            "transcript_path": "",
            "agent_transcript_path": "",
            "cwd": str(tmp_path),
            "stop_hook_active": True,
            "permission_mode": "default",
        })

        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "subagent_stop.py")],
            input=stdin_payload,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "WORKFLOW_MEMORY_BASE_PATH": str(tmp_path),
            },
            timeout=30,
        )

        # Should still exit 0 (graceful handling)
        assert result.returncode == 0, (
            f"subagent_stop.py exited with code {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )

    def test_stdin_handler_invalid_json(self, tmp_path):
        """Stdin handler should exit 1 on invalid JSON input."""
        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "subagent_stop.py")],
            input="not valid json {{{",
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "WORKFLOW_MEMORY_BASE_PATH": str(tmp_path),
            },
            timeout=30,
        )

        assert result.returncode == 1

    def test_stdin_handler_content_list_format(self, project_env, tmp_path):
        """Verify handling of transcript with content as list of blocks."""
        context_file = project_env["context_file"]

        # Create transcript with content as list (Claude API format)
        transcript_path = tmp_path / "agent_transcript_blocks.jsonl"
        transcript_lines = [
            json.dumps({
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "## Namespace Validation Report\n\n20 namespaces found.\n\n"},
                    {"type": "text", "text": "CONTEXT_UPDATE:\n"},
                    {"type": "text", "text": json.dumps({
                        "cluster_details": {
                            "namespaces": {
                                "application": ["adm", "dev"],
                                "system": ["kube-system"],
                            }
                        }
                    })},
                ],
            }),
        ]
        transcript_path.write_text("\n".join(transcript_lines))

        stdin_payload = json.dumps({
            "hook_event_name": "SubagentStop",
            "session_id": "test-session-e2e-003",
            "agent_type": "cloud-troubleshooter",
            "agent_id": "agent-e2e-003",
            "transcript_path": "",
            "agent_transcript_path": str(transcript_path),
            "cwd": str(tmp_path),
            "stop_hook_active": True,
            "permission_mode": "default",
        })

        result = subprocess.run(
            [sys.executable, str(HOOKS_DIR / "subagent_stop.py")],
            input=stdin_payload,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            env={
                **os.environ,
                "WORKFLOW_MEMORY_BASE_PATH": str(tmp_path),
            },
            timeout=30,
        )

        assert result.returncode == 0, (
            f"Exit code: {result.returncode}\nstderr: {result.stderr}"
        )

        # Verify project-context.json was updated
        updated = read_context(context_file)
        namespaces = updated["sections"]["cluster_details"].get("namespaces", {})
        assert "application" in namespaces
        assert "adm" in namespaces["application"]


# ============================================================================
# Test Suite 4: _read_transcript unit tests
# ============================================================================

class TestReadTranscript:
    """Unit tests for the _read_transcript helper."""

    def test_read_string_content(self, tmp_path):
        mod = _import_subagent_stop()
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps({
            "role": "assistant",
            "content": "Hello world",
        }))

        result = mod._read_transcript(str(transcript))
        assert "Hello world" in result

    def test_read_list_content(self, tmp_path):
        mod = _import_subagent_stop()
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(json.dumps({
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ],
        }))

        result = mod._read_transcript(str(transcript))
        assert "Part 1" in result
        assert "Part 2" in result

    def test_skips_user_messages(self, tmp_path):
        mod = _import_subagent_stop()
        transcript = tmp_path / "transcript.jsonl"
        lines = [
            json.dumps({"role": "user", "content": "user message"}),
            json.dumps({"role": "assistant", "content": "assistant message"}),
        ]
        transcript.write_text("\n".join(lines))

        result = mod._read_transcript(str(transcript))
        assert "user message" not in result
        assert "assistant message" in result

    def test_missing_file_returns_empty(self, tmp_path):
        mod = _import_subagent_stop()
        result = mod._read_transcript(str(tmp_path / "nonexistent.jsonl"))
        assert result == ""

    def test_empty_path_returns_empty(self):
        mod = _import_subagent_stop()
        result = mod._read_transcript("")
        assert result == ""


# ============================================================================
# Test Suite 5: _build_task_info_from_hook_data
# ============================================================================

class TestBuildTaskInfoFromHookData:
    """Unit tests for the _build_task_info_from_hook_data helper."""

    def test_maps_fields_correctly(self):
        mod = _import_subagent_stop()
        hook_data = {
            "hook_event_name": "SubagentStop",
            "session_id": "sess-123",
            "agent_type": "cloud-troubleshooter",
            "agent_id": "agent-456",
            "cwd": "/tmp/test",
        }

        task_info = mod._build_task_info_from_hook_data(hook_data)

        assert task_info["task_id"] == "agent-456"
        assert task_info["agent"] == "cloud-troubleshooter"
        assert task_info["tier"] == "T0"
        assert "SubagentStop" in task_info["description"]

    def test_handles_missing_fields(self):
        mod = _import_subagent_stop()
        task_info = mod._build_task_info_from_hook_data({})

        assert task_info["task_id"] == "unknown"
        assert task_info["agent"] == "unknown"
