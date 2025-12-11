#!/usr/bin/env python3
"""
Tests for subagent_stop.py hook.

Validates:
1. Metric capture (duration, exit codes, agent info)
2. Anomaly detection (slow execution, failures, consecutive failures)
3. Gaia signal generation
4. Session ID management
"""

import json
import os
import sys
import tempfile
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from subagent_stop import (
    capture_workflow_metrics,
    detect_anomalies,
    signal_gaia_analysis,
    subagent_stop_hook,
    get_workflow_memory_dir,
    _get_or_create_session_id,
)


class TestCaptureWorkflowMetrics:
    """Tests for capture_workflow_metrics function."""

    @pytest.fixture
    def temp_workflow_dir(self, tmp_path):
        """Create temp workflow memory directory."""
        workflow_dir = tmp_path / "memory" / "workflow-episodic"
        workflow_dir.mkdir(parents=True)
        return workflow_dir

    @pytest.fixture
    def basic_task_info(self):
        return {
            "task_id": "T001",
            "agent": "terraform-architect",
            "tier": "T2",
            "tags": ["#terraform"],
        }

    @pytest.fixture
    def basic_session_context(self):
        return {
            "timestamp": datetime.now().isoformat(),
            "session_id": "session-test-001",
        }

    def test_captures_duration_from_ms_format(self, temp_workflow_dir, basic_task_info, basic_session_context):
        """Test duration extraction from 'Duration: XXX ms' format."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            output = "Task completed\nDuration: 5000 ms\nSuccess"
            metrics = capture_workflow_metrics(basic_task_info, output, basic_session_context)
            
            assert metrics["duration_ms"] == 5000
            assert metrics["exit_code"] == 0
            assert metrics["agent"] == "terraform-architect"

    def test_captures_duration_from_seconds_format(self, temp_workflow_dir, basic_task_info, basic_session_context):
        """Test duration extraction from 'took X seconds' format."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            output = "Operation took 3.5 seconds to complete"
            metrics = capture_workflow_metrics(basic_task_info, output, basic_session_context)
            
            assert metrics["duration_ms"] == 3500

    def test_extracts_error_exit_code(self, temp_workflow_dir, basic_task_info, basic_session_context):
        """Test exit code extraction from error output."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            output = "Command failed with exit code: 127"
            metrics = capture_workflow_metrics(basic_task_info, output, basic_session_context)
            
            assert metrics["exit_code"] == 127

    def test_sets_generic_error_on_failure_keyword(self, temp_workflow_dir, basic_task_info, basic_session_context):
        """Test generic error detection from 'error' or 'failed' keywords."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            output = "The operation failed due to network error"
            metrics = capture_workflow_metrics(basic_task_info, output, basic_session_context)
            
            assert metrics["exit_code"] == 1  # Generic error

    def test_writes_metrics_to_file(self, temp_workflow_dir, basic_task_info, basic_session_context):
        """Test that metrics are appended to metrics.jsonl."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            output = "Duration: 1000 ms"
            capture_workflow_metrics(basic_task_info, output, basic_session_context)
            
            metrics_file = temp_workflow_dir / "metrics.jsonl"
            assert metrics_file.exists()
            
            with open(metrics_file) as f:
                line = f.readline()
                data = json.loads(line)
                assert data["task_id"] == "T001"
                assert data["agent"] == "terraform-architect"

    def test_handles_missing_duration(self, temp_workflow_dir, basic_task_info, basic_session_context):
        """Test handling when duration is not in output."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            output = "Task completed successfully"
            metrics = capture_workflow_metrics(basic_task_info, output, basic_session_context)
            
            assert metrics["duration_ms"] is None
            assert metrics["exit_code"] == 0


class TestDetectAnomalies:
    """Tests for detect_anomalies function."""

    @pytest.fixture
    def temp_workflow_dir(self, tmp_path):
        """Create temp workflow memory directory with metrics file."""
        workflow_dir = tmp_path / "memory" / "workflow-episodic"
        workflow_dir.mkdir(parents=True)
        return workflow_dir

    def test_detects_slow_execution(self, temp_workflow_dir):
        """Test detection of slow execution (>120s)."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            metrics = {
                "agent": "terraform-architect",
                "duration_ms": 150000,  # 150 seconds
                "exit_code": 0,
            }
            
            anomalies = detect_anomalies(metrics)
            
            assert len(anomalies) >= 1
            slow_anomaly = next((a for a in anomalies if a["type"] == "slow_execution"), None)
            assert slow_anomaly is not None
            assert slow_anomaly["severity"] == "warning"

    def test_detects_execution_failure(self, temp_workflow_dir):
        """Test detection of execution failure."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            metrics = {
                "agent": "gitops-operator",
                "duration_ms": 5000,
                "exit_code": 1,
            }
            
            anomalies = detect_anomalies(metrics)
            
            assert len(anomalies) >= 1
            failure_anomaly = next((a for a in anomalies if a["type"] == "execution_failure"), None)
            assert failure_anomaly is not None
            assert failure_anomaly["severity"] == "error"

    def test_detects_consecutive_failures(self, temp_workflow_dir):
        """Test detection of consecutive failures (3+ in a row)."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            # Write previous failures to metrics file
            metrics_file = temp_workflow_dir / "metrics.jsonl"
            previous_metrics = [
                {"agent": "terraform-architect", "exit_code": 1, "timestamp": "2024-01-01T10:00:00"},
                {"agent": "terraform-architect", "exit_code": 1, "timestamp": "2024-01-01T10:01:00"},
                {"agent": "terraform-architect", "exit_code": 1, "timestamp": "2024-01-01T10:02:00"},
            ]
            with open(metrics_file, "w") as f:
                for m in previous_metrics:
                    f.write(json.dumps(m) + "\n")
            
            # Current failure
            metrics = {
                "agent": "terraform-architect",
                "duration_ms": 5000,
                "exit_code": 1,
            }
            
            anomalies = detect_anomalies(metrics)
            
            consecutive_anomaly = next((a for a in anomalies if a["type"] == "consecutive_failures"), None)
            assert consecutive_anomaly is not None
            assert consecutive_anomaly["severity"] == "critical"

    def test_no_anomaly_on_normal_execution(self, temp_workflow_dir):
        """Test no anomaly for normal execution."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(temp_workflow_dir.parent.parent)}):
            metrics = {
                "agent": "devops-developer",
                "duration_ms": 30000,  # 30 seconds
                "exit_code": 0,
            }
            
            anomalies = detect_anomalies(metrics)
            
            assert len(anomalies) == 0


class TestSignalGaiaAnalysis:
    """Tests for signal_gaia_analysis function."""

    def test_creates_signal_file(self, tmp_path):
        """Test that signal file is created."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(tmp_path)}):
            anomalies = [{"type": "slow_execution", "severity": "warning", "message": "Test"}]
            metrics = {"agent": "test", "task_id": "T001", "duration_ms": 150000, "exit_code": 0}
            
            signal_gaia_analysis(anomalies, metrics)
            
            signal_file = tmp_path / "memory" / "workflow-episodic" / "signals" / "needs_analysis.flag"
            assert signal_file.exists()

    def test_signal_contains_anomaly_data(self, tmp_path):
        """Test signal file contains anomaly information."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(tmp_path)}):
            anomalies = [{"type": "execution_failure", "severity": "error", "message": "Failed"}]
            metrics = {"agent": "terraform-architect", "task_id": "T002", "duration_ms": 5000, "exit_code": 1}
            
            signal_gaia_analysis(anomalies, metrics)
            
            signal_file = tmp_path / "memory" / "workflow-episodic" / "signals" / "needs_analysis.flag"
            with open(signal_file) as f:
                data = json.load(f)
            
            assert "anomalies" in data
            assert len(data["anomalies"]) == 1
            assert data["anomalies"][0]["type"] == "execution_failure"

    def test_appends_to_anomaly_log(self, tmp_path):
        """Test anomalies are logged to permanent file."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(tmp_path)}):
            anomalies = [{"type": "slow_execution", "severity": "warning", "message": "Slow"}]
            metrics = {"agent": "test", "task_id": "T003", "duration_ms": 150000, "exit_code": 0}
            
            signal_gaia_analysis(anomalies, metrics)
            
            anomaly_log = tmp_path / "memory" / "workflow-episodic" / "anomalies.jsonl"
            assert anomaly_log.exists()


class TestSubagentStopHook:
    """Tests for main subagent_stop_hook function."""

    def test_returns_success_on_normal_execution(self, tmp_path):
        """Test successful return for normal execution."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(tmp_path)}):
            task_info = {
                "task_id": "T001",
                "agent": "devops-developer",
                "tier": "T1",
            }
            output = "Task completed successfully\nDuration: 5000 ms"
            
            result = subagent_stop_hook(task_info, output)
            
            assert result["success"] is True
            assert result["metrics_captured"] is True
            assert "session_id" in result

    def test_detects_and_signals_anomalies(self, tmp_path):
        """Test anomaly detection and signaling."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(tmp_path)}):
            task_info = {
                "task_id": "T002",
                "agent": "terraform-architect",
                "tier": "T3",
            }
            output = "Error: Command failed\nDuration: 180000 ms"  # Both slow and error
            
            result = subagent_stop_hook(task_info, output)
            
            assert result["success"] is True
            assert result["anomalies_detected"] >= 1  # At least slow execution

    def test_handles_exceptions_gracefully(self, tmp_path):
        """Test graceful handling of exceptions."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": "/nonexistent/path/that/will/fail"}):
            # This should not crash
            task_info = {"task_id": "T003", "agent": "test"}
            output = "test"
            
            # The function should handle exceptions internally
            result = subagent_stop_hook(task_info, output)
            # Result depends on implementation - may succeed or fail gracefully


class TestSessionIdManagement:
    """Tests for session ID creation and management."""

    def test_creates_new_session_id(self):
        """Test new session ID creation when not in env."""
        # Clear any existing session ID
        with patch.dict(os.environ, {}, clear=True):
            if "CLAUDE_SESSION_ID" in os.environ:
                del os.environ["CLAUDE_SESSION_ID"]
            
            session_id = _get_or_create_session_id()
            
            assert session_id is not None
            assert session_id.startswith("session-")

    def test_reuses_existing_session_id(self):
        """Test reuse of existing session ID from env."""
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "session-existing-123"}):
            session_id = _get_or_create_session_id()
            
            assert session_id == "session-existing-123"


class TestWorkflowMemoryDir:
    """Tests for get_workflow_memory_dir function."""

    def test_uses_env_override(self, tmp_path):
        """Test using WORKFLOW_MEMORY_BASE_PATH env var."""
        with patch.dict(os.environ, {"WORKFLOW_MEMORY_BASE_PATH": str(tmp_path)}):
            result = get_workflow_memory_dir()
            
            assert str(tmp_path) in str(result)
            assert result.name == "workflow-episodic"

    def test_uses_default_when_no_env(self):
        """Test default path when no env var set."""
        with patch.dict(os.environ, {}, clear=True):
            if "WORKFLOW_MEMORY_BASE_PATH" in os.environ:
                del os.environ["WORKFLOW_MEMORY_BASE_PATH"]
            
            result = get_workflow_memory_dir()
            
            assert result == Path(".claude/memory/workflow-episodic")
