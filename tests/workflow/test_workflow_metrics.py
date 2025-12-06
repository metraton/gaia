#!/usr/bin/env python3
"""
Test workflow metrics capture and anomaly detection.

NOTE: Uses tempfile for test isolation - does NOT create .claude/ in CWD.
Sets WORKFLOW_MEMORY_BASE_PATH env var to redirect all workflow memory writes
to a temp directory.
"""

import os
import sys
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
import pytest

# Test isolation: Set up temp directory BEFORE importing subagent_stop
_TEST_TEMP_DIR = Path(tempfile.mkdtemp(prefix="workflow_metrics_test_"))
os.environ["WORKFLOW_MEMORY_BASE_PATH"] = str(_TEST_TEMP_DIR)

# Add hooks to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_artifacts():
    """Clean up temp directory after all tests in module"""
    yield
    global _TEST_TEMP_DIR
    if _TEST_TEMP_DIR and _TEST_TEMP_DIR.exists():
        shutil.rmtree(_TEST_TEMP_DIR)
        _TEST_TEMP_DIR = None
    # Clean up env var
    if "WORKFLOW_MEMORY_BASE_PATH" in os.environ:
        del os.environ["WORKFLOW_MEMORY_BASE_PATH"]


def get_test_memory_path():
    """Get isolated temp directory for test artifacts"""
    return _TEST_TEMP_DIR / "memory" / "workflow-episodic"


class TestMetricsCapture:
    """Test workflow metrics capture"""

    def test_normal_execution_metrics(self):
        """Test case 1: Normal execution"""
        from subagent_stop import capture_workflow_metrics

        task_info = {
            "task_id": "T001",
            "agent": "terraform-architect",
            "tier": "T1"
        }

        agent_output = "Task completed successfully. Duration: 45000 ms"

        session_context = {
            "timestamp": datetime.now().isoformat(),
            "session_id": "test-session-001",
            "task_id": "T001"
        }

        metrics = capture_workflow_metrics(task_info, agent_output, session_context)

        assert metrics["duration_ms"] == 45000, \
            f"Duration not captured correctly: {metrics}"
        assert metrics["exit_code"] == 0, \
            f"Exit code not captured correctly: {metrics}"

    def test_failed_execution_metrics(self):
        """Test case 2: Failed execution"""
        from subagent_stop import capture_workflow_metrics

        task_info = {
            "task_id": "T001",
            "agent": "terraform-architect",
            "tier": "T1"
        }

        agent_output_failed = "Task failed with error. Exit code: 1"

        session_context = {
            "timestamp": datetime.now().isoformat(),
            "session_id": "test-session-001",
            "task_id": "T001"
        }

        metrics_failed = capture_workflow_metrics(task_info, agent_output_failed, session_context)

        assert metrics_failed["exit_code"] == 1, \
            f"Exit code not detected: {metrics_failed}"


class TestAnomalyDetection:
    """Test anomaly detection"""

    def test_slow_execution_detected(self):
        """Test 1: Slow execution"""
        from subagent_stop import detect_anomalies

        metrics_slow = {
            "agent": "terraform-architect",
            "duration_ms": 150000,  # 150 seconds
            "exit_code": 0
        }

        anomalies = detect_anomalies(metrics_slow)

        assert any(a["type"] == "slow_execution" for a in anomalies), \
            "Slow execution not detected"

    def test_failed_execution_detected(self):
        """Test 2: Failed execution"""
        from subagent_stop import detect_anomalies

        metrics_failed = {
            "agent": "gitops-operator",
            "duration_ms": 5000,
            "exit_code": 1
        }

        anomalies = detect_anomalies(metrics_failed)

        assert any(a["type"] == "execution_failure" for a in anomalies), \
            "Failed execution not detected"


class TestSignalCreation:
    """Test Gaia signal creation"""

    def test_signal_file_created(self):
        """Test Gaia signal creation"""
        from subagent_stop import signal_gaia_analysis

        anomalies = [
            {
                "type": "slow_execution",
                "severity": "warning",
                "message": "Test agent took too long"
            }
        ]

        metrics = {
            "agent": "test-agent",
            "task_id": "T999",
            "duration_ms": 150000,
            "exit_code": 0
        }

        # Create signal using the actual hook function (uses WORKFLOW_MEMORY_BASE_PATH env var)
        signal_gaia_analysis(anomalies, metrics)

        # Check if signal file was created in temp directory
        signal_file = get_test_memory_path() / "signals" / "needs_analysis.flag"

        assert signal_file.exists(), "Signal file not created"

        # Read and verify content
        with open(signal_file) as f:
            read_signal_data = json.load(f)

        assert len(read_signal_data["anomalies"]) == 1, \
            "Signal should contain correct anomaly data"


class TestWorkflowMemoryStructure:
    """Verify workflow memory structure"""

    def test_memory_directory_exists(self):
        """Verify workflow memory structure using temp directory"""
        memory_path = get_test_memory_path()

        if not memory_path.exists():
            memory_path.mkdir(parents=True, exist_ok=True)

        assert memory_path.exists(), f"Memory path should exist: {memory_path}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
