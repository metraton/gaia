"""
Anomaly detection and Gaia signaling.

Detects workflow anomalies and signals Gaia for analysis.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from enum import Enum
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict

from ..core.paths import get_memory_dir
from .subagent_metrics import SubagentMetrics, get_recent_metrics

logger = logging.getLogger(__name__)


class AnomalySeverity(str, Enum):
    """Severity levels for anomalies."""
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Anomaly:
    """Detected anomaly."""
    anomaly_type: str
    severity: AnomalySeverity
    message: str
    agent: str = ""
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.anomaly_type,
            "severity": self.severity.value,
            "message": self.message,
            "agent": self.agent,
            "details": self.details,
        }


# Thresholds for anomaly detection
ANOMALY_THRESHOLDS = {
    "slow_execution_ms": 120000,  # 2 minutes
    "consecutive_failures": 3,
}


def detect_anomalies(metrics: SubagentMetrics) -> List[Anomaly]:
    """
    Detect anomalies in workflow execution.

    Checks:
    - Slow execution (> 120s)
    - Failed executions (exit_code != 0)
    - Consecutive failures (3+ in a row)

    Args:
        metrics: Current execution metrics

    Returns:
        List of detected anomalies
    """
    anomalies = []

    # Check duration
    if metrics.duration_ms and metrics.duration_ms > ANOMALY_THRESHOLDS["slow_execution_ms"]:
        anomalies.append(Anomaly(
            anomaly_type="slow_execution",
            severity=AnomalySeverity.WARNING,
            message=f"Agent {metrics.agent} took {metrics.duration_ms/1000:.1f}s (threshold: 120s)",
            agent=metrics.agent,
            details={"duration_ms": metrics.duration_ms},
        ))

    # Check exit code
    if metrics.exit_code != 0:
        anomalies.append(Anomaly(
            anomaly_type="execution_failure",
            severity=AnomalySeverity.ERROR,
            message=f"Agent {metrics.agent} failed with exit code {metrics.exit_code}",
            agent=metrics.agent,
            details={"exit_code": metrics.exit_code},
        ))

    # Check consecutive failures
    consecutive = _check_consecutive_failures(metrics)
    if consecutive >= ANOMALY_THRESHOLDS["consecutive_failures"]:
        anomalies.append(Anomaly(
            anomaly_type="consecutive_failures",
            severity=AnomalySeverity.CRITICAL,
            message=f"Agent {metrics.agent} has failed {consecutive} times consecutively",
            agent=metrics.agent,
            details={"consecutive_count": consecutive},
        ))

    return anomalies


def _check_consecutive_failures(current_metrics: SubagentMetrics) -> int:
    """
    Check for consecutive failures of the same agent.

    Args:
        current_metrics: Current execution metrics

    Returns:
        Count of consecutive failures (including current)
    """
    if current_metrics.exit_code == 0:
        return 0

    # Get recent metrics for this agent
    recent = get_recent_metrics(agent=current_metrics.agent, limit=5)

    # Count consecutive failures from the end
    consecutive = 1  # Current failure
    for metrics in reversed(recent[:-1]):  # Exclude current
        if metrics.exit_code != 0:
            consecutive += 1
        else:
            break

    return consecutive


def signal_gaia_analysis(anomalies: List[Anomaly], metrics: SubagentMetrics) -> bool:
    """
    Signal that Gaia analysis is needed.

    Creates a flag file that orchestrator can detect.

    Args:
        anomalies: List of detected anomalies
        metrics: Metrics that triggered the signal

    Returns:
        True if signal was created successfully
    """
    try:
        signals_dir = get_memory_dir("workflow-episodic") / "signals"
        signals_dir.mkdir(parents=True, exist_ok=True)

        signal_file = signals_dir / "needs_analysis.flag"

        signal_data = {
            "timestamp": datetime.now().isoformat(),
            "anomalies": [a.to_dict() for a in anomalies],
            "metrics_summary": {
                "agent": metrics.agent,
                "task_id": metrics.task_id,
                "duration_ms": metrics.duration_ms,
                "exit_code": metrics.exit_code,
            },
            "suggested_action": "Invoke /gaia for system analysis",
        }

        with open(signal_file, "w") as f:
            json.dump(signal_data, f, indent=2)

        logger.info(f"Gaia analysis signal created: {signal_file}")

        # Also log to permanent anomaly log
        _log_anomalies(anomalies, metrics)

        return True

    except Exception as e:
        logger.warning(f"Could not create analysis signal: {e}")
        return False


def _log_anomalies(anomalies: List[Anomaly], metrics: SubagentMetrics) -> None:
    """Log anomalies to permanent log file."""
    try:
        anomaly_log = get_memory_dir("workflow-episodic") / "anomalies.jsonl"
        with open(anomaly_log, "a") as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "anomalies": [a.to_dict() for a in anomalies],
                "metrics": metrics.to_dict(),
            }) + "\n")
    except Exception as e:
        logger.debug(f"Could not log anomalies: {e}")


def check_analysis_signal() -> Optional[Dict[str, Any]]:
    """
    Check if there's an active analysis signal.

    Returns:
        Signal data if exists, None otherwise
    """
    try:
        signal_file = get_memory_dir("workflow-episodic") / "signals" / "needs_analysis.flag"
        if signal_file.exists():
            with open(signal_file, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.debug(f"Error checking analysis signal: {e}")
    return None


def clear_analysis_signal() -> bool:
    """
    Clear the analysis signal after Gaia has processed it.

    Returns:
        True if cleared successfully
    """
    try:
        signal_file = get_memory_dir("workflow-episodic") / "signals" / "needs_analysis.flag"
        if signal_file.exists():
            signal_file.unlink()
            logger.info("Analysis signal cleared")
        return True
    except Exception as e:
        logger.warning(f"Could not clear analysis signal: {e}")
        return False
