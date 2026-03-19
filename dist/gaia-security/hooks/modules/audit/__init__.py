"""
Audit module - Logging, metrics, and event detection.

Provides:
- logger: AuditLogger for tool executions
- metrics: MetricsCollector with functional generate_summary
- event_detector: CriticalEventDetector
"""

from .logger import AuditLogger, log_execution
from .metrics import MetricsCollector, record_metric, generate_summary
from .event_detector import (
    CriticalEventDetector,
    detect_critical_event,
    EventType,
)

__all__ = [
    # Logger
    "AuditLogger",
    "log_execution",
    # Metrics
    "MetricsCollector",
    "record_metric",
    "generate_summary",
    # Event detector
    "CriticalEventDetector",
    "detect_critical_event",
    "EventType",
]
