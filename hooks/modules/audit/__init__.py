"""
Audit module - Logging, metrics aggregation, and event detection.

Provides:
- logger: AuditLogger for tool executions (write path)
- metrics: generate_summary reads audit logs and aggregates (read path)
- event_detector: CriticalEventDetector
"""

from .logger import AuditLogger, log_execution
from .metrics import generate_summary
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
    "generate_summary",
    # Event detector
    "CriticalEventDetector",
    "detect_critical_event",
    "EventType",
]
