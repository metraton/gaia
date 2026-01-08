"""
Agents module - Subagent metrics and anomaly detection.

Provides:
- subagent_metrics: Metrics capture from subagent execution
- anomaly_detector: Detect anomalies and signal Gaia
"""

from .subagent_metrics import (
    capture_workflow_metrics,
    SubagentMetrics,
)
from .anomaly_detector import (
    detect_anomalies,
    signal_gaia_analysis,
    Anomaly,
    AnomalySeverity,
)

__all__ = [
    # Subagent metrics
    "capture_workflow_metrics",
    "SubagentMetrics",
    # Anomaly detector
    "detect_anomalies",
    "signal_gaia_analysis",
    "Anomaly",
    "AnomalySeverity",
]
