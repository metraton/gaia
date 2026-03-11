"""
Workflow metrics capture and persistence.

Renamed from metrics_recorder.py for clarity.

Provides:
    - get_workflow_memory_dir(): Resolve workflow memory directory
    - record(): Build metrics dict, write to JSONL
    - capture_workflow_metrics(): Backward-compatible alias for record()
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


def get_workflow_memory_dir() -> Path:
    """
    Get workflow memory directory path.

    Supports override via WORKFLOW_MEMORY_BASE_PATH env var for testing.
    In production, uses .claude/project-context/workflow-episodic-memory relative to CWD.
    """
    base_path = os.environ.get("WORKFLOW_MEMORY_BASE_PATH")
    if base_path:
        return Path(base_path) / "project-context" / "workflow-episodic-memory"
    return Path(".claude/project-context/workflow-episodic-memory")


def record(
    task_info: Dict[str, Any],
    agent_output: str,
    session_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Capture workflow execution metrics for analysis.

    Args:
        task_info: Task metadata
        agent_output: Output from agent execution
        session_context: Current session context

    Returns:
        Dict with duration, exit_code, agent, tier, etc.
    """
    # Duration cannot be reliably measured from within this hook because
    # it fires only at agent completion (no start timestamp available).
    duration_ms = None

    # Use exit_code from task_info (derived from AGENT_STATUS block) instead
    # of naive text matching which gives false positives on "No errors found".
    exit_code = task_info.get("exit_code", 0)

    # Approximate token count: 4 chars per token is a reliable heuristic for LLM output
    output_tokens_approx = len(agent_output) // 4

    metrics = {
        "timestamp": session_context["timestamp"],
        "session_id": session_context["session_id"],
        "task_id": task_info.get("task_id", "unknown"),
        "agent": task_info.get("agent", "unknown"),
        "tier": task_info.get("tier", "T0"),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "plan_status": task_info.get("plan_status", ""),
        "output_length": len(agent_output),
        "output_tokens_approx": output_tokens_approx,
        "tags": task_info.get("tags", []),
        "prompt": task_info.get("description", ""),  # Store for episodic
    }

    # Save to workflow memory (gated behind env var; default: no write)
    if os.environ.get("GAIA_WRITE_WORKFLOW_METRICS") == "1":
        workflow_memory_dir = get_workflow_memory_dir()
        workflow_memory_dir.mkdir(parents=True, exist_ok=True)

        metrics_file = workflow_memory_dir / "metrics.jsonl"
        with open(metrics_file, "a") as f:
            f.write(json.dumps(metrics) + "\n")

    logger.debug(
        "Captured workflow metrics: %s (duration: %sms, exit: %s)",
        metrics["agent"], duration_ms, exit_code,
    )

    return metrics


# Backward-compatible alias
capture_workflow_metrics = record
