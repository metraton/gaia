"""
Subagent workflow metrics capture.

Extracts and stores metrics from subagent execution.
"""

import re
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict

from ..core.paths import get_memory_dir

logger = logging.getLogger(__name__)


@dataclass
class SubagentMetrics:
    """Metrics captured from subagent execution."""
    timestamp: str
    session_id: str
    task_id: str
    agent: str
    tier: str
    duration_ms: Optional[int]
    exit_code: int
    output_length: int
    tags: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def capture_workflow_metrics(
    task_info: Dict[str, Any],
    agent_output: str,
    session_context: Dict[str, Any]
) -> SubagentMetrics:
    """
    Capture workflow execution metrics from subagent.

    Args:
        task_info: Task metadata (task_id, agent, tier, tags)
        agent_output: Output from agent execution
        session_context: Session context (timestamp, session_id)

    Returns:
        SubagentMetrics with captured data
    """
    # Extract duration from agent output
    duration_ms = _extract_duration(agent_output)

    # Extract exit code
    exit_code = _extract_exit_code(agent_output)

    metrics = SubagentMetrics(
        timestamp=session_context.get("timestamp", datetime.now().isoformat()),
        session_id=session_context.get("session_id", "unknown"),
        task_id=task_info.get("task_id", "unknown"),
        agent=task_info.get("agent", "unknown"),
        tier=task_info.get("tier", "unknown"),
        duration_ms=duration_ms,
        exit_code=exit_code,
        output_length=len(agent_output),
        tags=task_info.get("tags", []),
    )

    # Save to workflow memory
    _save_metrics(metrics)

    logger.debug(
        f"Captured workflow metrics: {metrics.agent} "
        f"(duration: {duration_ms}ms, exit: {exit_code})"
    )

    return metrics


def _extract_duration(output: str) -> Optional[int]:
    """Extract duration from agent output."""
    # Pattern: "Duration: 45000 ms"
    match = re.search(r"Duration:\s*(\d+)\s*ms", output)
    if match:
        return int(match.group(1))

    # Alternative: "took 45.0 seconds"
    match = re.search(r"took\s+(\d+(?:\.\d+)?)\s*(?:seconds?|s)", output)
    if match:
        return int(float(match.group(1)) * 1000)

    return None


def _extract_exit_code(output: str) -> int:
    """Extract exit code from agent output."""
    output_lower = output.lower()

    # Check for explicit exit code
    match = re.search(r"exit\s+code:?\s*(\d+)", output_lower)
    if match:
        return int(match.group(1))

    # Infer from error keywords
    if "error" in output_lower or "failed" in output_lower:
        return 1

    return 0


def _save_metrics(metrics: SubagentMetrics) -> None:
    """Save metrics to workflow memory."""
    try:
        workflow_memory_dir = get_memory_dir("workflow-episodic")
        metrics_file = workflow_memory_dir / "metrics.jsonl"

        with open(metrics_file, "a") as f:
            f.write(json.dumps(metrics.to_dict()) + "\n")

    except Exception as e:
        logger.error(f"Error saving workflow metrics: {e}")


def get_recent_metrics(agent: Optional[str] = None, limit: int = 10) -> List[SubagentMetrics]:
    """
    Get recent workflow metrics.

    Args:
        agent: Filter by agent name (optional)
        limit: Maximum number of records to return

    Returns:
        List of SubagentMetrics
    """
    try:
        workflow_memory_dir = get_memory_dir("workflow-episodic")
        metrics_file = workflow_memory_dir / "metrics.jsonl"

        if not metrics_file.exists():
            return []

        records = []
        with open(metrics_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if agent is None or data.get("agent") == agent:
                        records.append(SubagentMetrics(**data))
                except (json.JSONDecodeError, TypeError):
                    continue

        # Return last N records
        return records[-limit:]

    except Exception as e:
        logger.error(f"Error loading workflow metrics: {e}")
        return []
