#!/usr/bin/env python3
"""
Subagent stop hook for Claude Code Agent System

Handles workflow metrics capture and anomaly detection when agents complete execution.

Responsibilities:
1. Capture workflow execution metrics (duration, exit code, agent)
2. Detect anomalies (slow execution, failures, consecutive failures)
3. Signal Gaia for analysis when anomalies are detected

Architecture:
- Metrics stored in .claude/memory/workflow-episodic/
- Anomaly signals trigger Gaia analysis
- Minimal footprint - no bundle creation

Integration:
- Executed automatically after agent tool completes
- Integrates with Episodic Memory for context enrichment
"""

import os
import sys
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any
import hashlib

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_workflow_memory_dir() -> Path:
    """
    Get workflow memory directory path.

    Supports override via WORKFLOW_MEMORY_BASE_PATH env var for testing.
    In production, uses .claude/memory/workflow-episodic relative to CWD.
    """
    base_path = os.environ.get("WORKFLOW_MEMORY_BASE_PATH")
    if base_path:
        return Path(base_path) / "memory" / "workflow-episodic"
    return Path(".claude/memory/workflow-episodic")


def _find_claude_dir() -> Path:
    """Find the .claude directory by searching upward from current location"""
    current = Path.cwd()

    # If we're already in a .claude directory, go up one level
    if current.name == ".claude":
        return current

    # Look for .claude in current directory
    claude_dir = current / ".claude"
    if claude_dir.exists():
        return claude_dir

    # Search upward through parent directories
    for parent in current.parents:
        claude_dir = parent / ".claude"
        if claude_dir.exists():
            return claude_dir

    # Default fallback - create .claude in current directory
    logger.warning("No .claude directory found, creating in current directory")
    claude_dir = current / ".claude"
    claude_dir.mkdir(exist_ok=True)
    return claude_dir


def _get_or_create_session_id() -> str:
    """Get existing session ID or create new one"""
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        timestamp = datetime.now().strftime("%H%M%S")
        hash_input = f"{timestamp}-{os.getpid()}"
        session_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:8]
        session_id = f"session-{timestamp}-{session_hash}"
        os.environ["CLAUDE_SESSION_ID"] = session_id
        logger.debug(f"Generated new session_id: {session_id}")
    return session_id


def capture_workflow_metrics(task_info: Dict[str, Any], agent_output: str, session_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Capture workflow execution metrics for analysis.

    Args:
        task_info: Task metadata
        agent_output: Output from agent execution
        session_context: Current session context

    Returns:
        Dict with duration, exit_code, agent, tier, etc.
    """
    # Try to extract duration from agent output
    duration_match = re.search(r"Duration:\s*(\d+)\s*ms", agent_output)
    if not duration_match:
        # Alternative pattern
        duration_match = re.search(r"took\s+(\d+(?:\.\d+)?)\s*(?:seconds?|s)", agent_output)
        if duration_match:
            duration_ms = int(float(duration_match.group(1)) * 1000)
        else:
            duration_ms = None
    else:
        duration_ms = int(duration_match.group(1))

    # Try to extract exit code
    exit_code = 0  # Default to success
    if "error" in agent_output.lower() or "failed" in agent_output.lower():
        exit_code_match = re.search(r"exit\s+code:?\s*(\d+)", agent_output.lower())
        if exit_code_match:
            exit_code = int(exit_code_match.group(1))
        else:
            exit_code = 1  # Generic error

    metrics = {
        "timestamp": session_context["timestamp"],
        "session_id": session_context["session_id"],
        "task_id": task_info.get("task_id", "unknown"),
        "agent": task_info.get("agent", "unknown"),
        "tier": task_info.get("tier", "unknown"),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "output_length": len(agent_output),
        "tags": task_info.get("tags", [])
    }

    # Save to workflow memory
    workflow_memory_dir = get_workflow_memory_dir()
    workflow_memory_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = workflow_memory_dir / "metrics.jsonl"
    with open(metrics_file, "a") as f:
        f.write(json.dumps(metrics) + "\n")

    logger.debug(f"Captured workflow metrics: {metrics['agent']} (duration: {duration_ms}ms, exit: {exit_code})")

    return metrics


def detect_anomalies(metrics: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Detect anomalies in workflow execution.

    Checks:
    - Slow execution (> 120s)
    - Failed executions (exit_code != 0)
    - Consecutive failures (3+ in a row)

    Returns:
        List of anomaly descriptions
    """
    anomalies = []

    # Check duration (if available)
    if metrics.get("duration_ms") and metrics["duration_ms"] > 120000:
        anomalies.append({
            "type": "slow_execution",
            "severity": "warning",
            "message": f"Agent {metrics['agent']} took {metrics['duration_ms']/1000:.1f}s (threshold: 120s)"
        })

    # Check exit code
    if metrics.get("exit_code", 0) != 0:
        anomalies.append({
            "type": "execution_failure",
            "severity": "error",
            "message": f"Agent {metrics['agent']} failed with exit code {metrics['exit_code']}"
        })

    # Check consecutive failures (read last N metrics)
    try:
        workflow_memory_dir = get_workflow_memory_dir()
        metrics_file = workflow_memory_dir / "metrics.jsonl"

        if metrics_file.exists():
            with open(metrics_file) as f:
                lines = f.readlines()
                # Get last 5 metrics (excluding current)
                if len(lines) >= 5:
                    last_5 = [json.loads(line) for line in lines[-6:-1]]
                else:
                    last_5 = [json.loads(line) for line in lines[:-1]] if len(lines) > 1 else []

                # Count recent failures for same agent
                agent = metrics["agent"]
                recent_failures = [
                    m for m in last_5
                    if m.get("agent") == agent and m.get("exit_code", 0) != 0
                ]

                # If current also failed and we have 2+ previous failures
                if metrics.get("exit_code", 0) != 0 and len(recent_failures) >= 2:
                    anomalies.append({
                        "type": "consecutive_failures",
                        "severity": "critical",
                        "message": f"Agent {agent} has failed {len(recent_failures) + 1} times consecutively"
                    })
    except Exception as e:
        logger.debug(f"Could not check consecutive failures: {e}")

    return anomalies


def signal_gaia_analysis(anomalies: List[Dict], metrics: Dict[str, Any]):
    """
    Signal that Gaia analysis is needed.

    Creates a flag file that orchestrator can detect.
    """
    try:
        signals_dir = get_workflow_memory_dir() / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)

        signal_file = signals_dir / "needs_analysis.flag"

        signal_data = {
            "timestamp": datetime.now().isoformat(),
            "anomalies": anomalies,
            "metrics_summary": {
                "agent": metrics["agent"],
                "task_id": metrics["task_id"],
                "duration_ms": metrics.get("duration_ms"),
                "exit_code": metrics.get("exit_code")
            },
            "suggested_action": "Invoke /gaia for system analysis"
        }

        with open(signal_file, "w") as f:
            json.dump(signal_data, f, indent=2)

        logger.info(f"Gaia analysis signal created: {signal_file}")

        # Also log to a permanent anomaly log
        anomaly_log = signals_dir.parent / "anomalies.jsonl"
        with open(anomaly_log, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "anomalies": anomalies,
                "metrics": metrics
            }) + "\n")

    except Exception as e:
        logger.warning(f"Could not create analysis signal: {e}")


def subagent_stop_hook(task_info: Dict[str, Any], agent_output: str) -> Dict[str, Any]:
    """
    Main subagent stop hook - captures metrics and detects anomalies.

    Args:
        task_info: Task information including ID, description, agent, etc.
        agent_output: Complete output from agent execution

    Returns:
        Success confirmation with metrics info
    """
    try:
        session_id = _get_or_create_session_id()

        # Create session context for metrics
        session_context = {
            "timestamp": datetime.now().isoformat(),
            "session_id": session_id,
            "task_id": task_info.get('task_id', 'unknown'),
            "agent": task_info.get('agent', 'unknown'),
        }

        # Capture workflow metrics
        workflow_metrics = capture_workflow_metrics(task_info, agent_output, session_context)

        # Check for anomalies
        anomalies = detect_anomalies(workflow_metrics)

        if anomalies:
            logger.warning(f"{len(anomalies)} anomalies detected in workflow")
            signal_gaia_analysis(anomalies, workflow_metrics)

        return {
            "success": True,
            "session_id": session_id,
            "status": "metrics_captured",
            "metrics_captured": True,
            "anomalies_detected": len(anomalies) if anomalies else 0
        }

    except Exception as e:
        logger.debug(f"Error in subagent_stop_hook: {e}")
        return {
            "success": False,
            "error": str(e),
            "status": "partial_update"
        }


def main():
    """CLI interface for testing metrics capture"""

    if len(sys.argv) < 2:
        print("Usage: python subagent_stop.py --test")
        sys.exit(1)

    if sys.argv[1] == "--test":
        test_task_info = {
            "task_id": "T006",
            "description": "Terraform plan for infrastructure",
            "agent": "terraform-architect",
            "tier": "T1",
            "tags": ["#terraform", "#infrastructure"],
        }

        test_output = """
# Terraform Architect Execution Log

## Task: T006 - Terraform plan for infrastructure

### Results:
- Configuration validated successfully
- Plan generated with 12 resources
- Duration: 45000 ms
"""

        result = subagent_stop_hook(test_task_info, test_output)

        if result["success"]:
            print("Test completed successfully!")
            print(f"Session ID: {result['session_id']}")
            print(f"Anomalies: {result['anomalies_detected']}")
        else:
            print(f"Test failed: {result.get('error', 'Unknown error')}")

    else:
        print("Unknown command. Use --test to run test.")


if __name__ == "__main__":
    main()
