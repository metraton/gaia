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

# Test isolation: Set up temp directory BEFORE importing subagent_stop
_TEST_TEMP_DIR = Path(tempfile.mkdtemp(prefix="workflow_metrics_test_"))
os.environ["WORKFLOW_MEMORY_BASE_PATH"] = str(_TEST_TEMP_DIR)

# Add hooks to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))

def get_test_memory_path():
    """Get isolated temp directory for test artifacts"""
    return _TEST_TEMP_DIR / "memory" / "workflow-episodic"

def cleanup_test_artifacts():
    """Clean up temp directory after tests"""
    global _TEST_TEMP_DIR
    if _TEST_TEMP_DIR and _TEST_TEMP_DIR.exists():
        shutil.rmtree(_TEST_TEMP_DIR)
        _TEST_TEMP_DIR = None
    # Clean up env var
    if "WORKFLOW_MEMORY_BASE_PATH" in os.environ:
        del os.environ["WORKFLOW_MEMORY_BASE_PATH"]

def test_metrics_capture():
    """Test workflow metrics capture"""
    print("🧪 Testing Workflow Metrics Capture...")

    from subagent_stop import capture_workflow_metrics

    # Test case 1: Normal execution
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

    if metrics["duration_ms"] == 45000 and metrics["exit_code"] == 0:
        print("  ✅ PASSED: Normal execution metrics captured correctly")
    else:
        print(f"  ❌ FAILED: Metrics not captured correctly: {metrics}")
        return False

    # Test case 2: Failed execution
    agent_output_failed = "Task failed with error. Exit code: 1"

    metrics_failed = capture_workflow_metrics(task_info, agent_output_failed, session_context)

    if metrics_failed["exit_code"] == 1:
        print("  ✅ PASSED: Failed execution detected correctly")
    else:
        print(f"  ❌ FAILED: Exit code not detected: {metrics_failed}")
        return False

    return True


def test_anomaly_detection():
    """Test anomaly detection"""
    print("\n🧪 Testing Anomaly Detection...")

    from subagent_stop import detect_anomalies

    # Test 1: Slow execution
    metrics_slow = {
        "agent": "terraform-architect",
        "duration_ms": 150000,  # 150 seconds
        "exit_code": 0
    }

    anomalies = detect_anomalies(metrics_slow)

    if any(a["type"] == "slow_execution" for a in anomalies):
        print("  ✅ PASSED: Slow execution detected")
    else:
        print("  ❌ FAILED: Slow execution not detected")
        return False

    # Test 2: Failed execution
    metrics_failed = {
        "agent": "gitops-operator",
        "duration_ms": 5000,
        "exit_code": 1
    }

    anomalies = detect_anomalies(metrics_failed)

    if any(a["type"] == "execution_failure" for a in anomalies):
        print("  ✅ PASSED: Failed execution detected")
    else:
        print("  ❌ FAILED: Failed execution not detected")
        return False

    return True


def test_signal_creation():
    """Test Gaia signal creation"""
    print("\n🧪 Testing Gaia Signal Creation...")

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

    if signal_file.exists():
        print("  ✅ PASSED: Signal file created successfully")

        # Read and verify content
        with open(signal_file) as f:
            read_signal_data = json.load(f)

        if len(read_signal_data["anomalies"]) == 1:
            print("  ✅ PASSED: Signal contains correct anomaly data")

        return True
    else:
        print("  ❌ FAILED: Signal file not created")
        return False


def verify_workflow_memory_structure():
    """Verify workflow memory structure using temp directory"""
    print("\n🧪 Verifying Workflow Memory Structure...")

    # Use temp directory for test isolation
    memory_path = get_test_memory_path()

    if not memory_path.exists():
        memory_path.mkdir(parents=True, exist_ok=True)
        print(f"  ✅ Created test directory: {memory_path}")

    # Check if metrics file exists (may have been created by tests)
    metrics_file = memory_path / "metrics.jsonl"
    if metrics_file.exists():
        with open(metrics_file) as f:
            lines = f.readlines()
        print(f"  ✅ Metrics file exists with {len(lines)} entries")
    else:
        print("  ⚠️  No metrics file yet (will be created on first capture)")

    return True


def main():
    """Run all tests"""
    print("="*60)
    print("WORKFLOW METRICS TESTS")
    print("="*60)

    tests = [
        verify_workflow_memory_structure,
        test_metrics_capture,
        test_anomaly_detection,
        test_signal_creation
    ]

    results = []
    try:
        for test in tests:
            try:
                result = test()
                results.append(result)
            except Exception as e:
                print(f"\n  ❌ Test failed with exception: {e}")
                import traceback
                traceback.print_exc()
                results.append(False)

        print("\n" + "="*60)
        print(f"RESULTS: {sum(results)}/{len(results)} test suites passed")
        print("="*60)

        if all(results):
            print("✅ All tests passed!")
            return 0
        else:
            print("❌ Some tests failed")
            return 1
    finally:
        # Always cleanup temp artifacts
        cleanup_test_artifacts()
        print("🧹 Test artifacts cleaned up")


if __name__ == "__main__":
    sys.exit(main())