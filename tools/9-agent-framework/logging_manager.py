#!/usr/bin/env python3
"""
Logging Manager - JSON Structured Logging for Benchmarking

All agent activities logged as JSON for parsing and metrics collection.

Reference: Agent-Execution-Profiles.md (Section 5: Logging & Observability)
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List
from enum import Enum


class EventType(Enum):
    """Types of events to log"""
    VALIDATION_START = "validation_start"
    VALIDATION_COMPLETE = "validation_complete"
    DISCOVERY_START = "discovery_start"
    DISCOVERY_COMPLETE = "discovery_complete"
    CLASSIFICATION_START = "classification_start"
    CLASSIFICATION_COMPLETE = "classification_complete"
    EXECUTION_START = "execution_start"
    EXECUTION_COMPLETE = "execution_complete"
    ERROR = "error"
    WARNING = "warning"


@dataclass
class LogEvent:
    """Structured log event"""
    timestamp: str
    event_type: str
    agent: str
    phase: str
    status: str  # "in_progress", "success", "failed"
    duration_ms: Optional[int] = None
    details: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_json(self) -> str:
        """Serialize to JSON"""
        data = asdict(self)
        return json.dumps(data)


class JSONLogger:
    """
    Structured JSON logging for benchmarking.

    All events are logged to:
    1. Console (real-time)
    2. Log file (for later analysis)
    3. Metrics aggregator (for dashboards)
    """

    def __init__(self, log_dir: Path = Path(".claude/logs")):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.events: List[LogEvent] = []
        self.logger = logging.getLogger("gaia.agent-framework")

        # Setup file handler
        handler = logging.FileHandler(
            self.log_dir / "agent-execution.jsonl",
            mode="a"
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.DEBUG)

    def log_event(self, event: LogEvent):
        """Log a structured event"""
        self.events.append(event)
        self.logger.debug(event.to_json())

    def log_validation_complete(
        self,
        agent: str,
        is_valid: bool,
        duration_ms: int,
        fields_valid: int,
        fields_missing: int
    ):
        """Log validation completion"""
        event = LogEvent(
            timestamp=datetime.utcnow().isoformat(),
            event_type=EventType.VALIDATION_COMPLETE.value,
            agent=agent,
            phase="A",
            status="success" if is_valid else "failed",
            duration_ms=duration_ms,
            details={
                "fields_valid": fields_valid,
                "fields_missing": fields_missing,
                "validation_passed": is_valid
            }
        )
        self.log_event(event)

    def log_discovery_complete(
        self,
        agent: str,
        files_discovered: int,
        ssot_count: int,
        discrepancies: int,
        duration_ms: int
    ):
        """Log discovery completion"""
        event = LogEvent(
            timestamp=datetime.utcnow().isoformat(),
            event_type=EventType.DISCOVERY_COMPLETE.value,
            agent=agent,
            phase="B",
            status="success",
            duration_ms=duration_ms,
            details={
                "files_discovered": files_discovered,
                "ssot_files": ssot_count,
                "discrepancies": discrepancies
            }
        )
        self.log_event(event)

    def log_execution_complete(
        self,
        agent: str,
        command: str,
        status: str,
        duration_ms: int,
        exit_code: Optional[int] = None,
        retry_attempts: int = 0,
        output_lines: int = 0
    ):
        """Log command execution completion"""
        event = LogEvent(
            timestamp=datetime.utcnow().isoformat(),
            event_type=EventType.EXECUTION_COMPLETE.value,
            agent=agent,
            phase="D",
            status=status,
            duration_ms=duration_ms,
            details={
                "command": command,
                "exit_code": exit_code,
                "retry_attempts": retry_attempts,
                "output_lines": output_lines
            }
        )
        self.log_event(event)

    def generate_metrics_summary(self) -> Dict[str, Any]:
        """Generate summary metrics from all events"""
        validation_times = []
        discovery_times = []
        execution_times = []
        total_retries = 0
        failed_executions = 0

        for event in self.events:
            if event.event_type == EventType.VALIDATION_COMPLETE.value:
                if event.duration_ms:
                    validation_times.append(event.duration_ms)

            elif event.event_type == EventType.DISCOVERY_COMPLETE.value:
                if event.duration_ms:
                    discovery_times.append(event.duration_ms)

            elif event.event_type == EventType.EXECUTION_COMPLETE.value:
                if event.duration_ms:
                    execution_times.append(event.duration_ms)
                if event.status == "failed":
                    failed_executions += 1
                if event.details and event.details.get("retry_attempts"):
                    total_retries += event.details["retry_attempts"]

        def avg(lst):
            return sum(lst) / len(lst) if lst else 0

        def p95(lst):
            if not lst:
                return 0
            sorted_lst = sorted(lst)
            idx = int(len(sorted_lst) * 0.95)
            return sorted_lst[idx] if idx < len(sorted_lst) else sorted_lst[-1]

        return {
            "total_events": len(self.events),
            "validation": {
                "count": len(validation_times),
                "avg_ms": avg(validation_times),
                "p95_ms": p95(validation_times),
            },
            "discovery": {
                "count": len(discovery_times),
                "avg_ms": avg(discovery_times),
                "p95_ms": p95(discovery_times),
            },
            "execution": {
                "count": len(execution_times),
                "avg_ms": avg(execution_times),
                "p95_ms": p95(execution_times),
                "failed": failed_executions,
                "total_retries": total_retries,
            },
            "success_rate": {
                "percent": (len(self.events) - failed_executions) / len(self.events) * 100
                if self.events else 0
            }
        }

    def save_metrics_report(self, filename: str = "metrics-report.json"):
        """Save metrics report to file"""
        metrics = self.generate_metrics_summary()
        report_path = self.log_dir / filename

        with open(report_path, "w") as f:
            json.dump(metrics, f, indent=2)

        return report_path

    def print_metrics_summary(self):
        """Print metrics summary to console"""
        metrics = self.generate_metrics_summary()

        print(f"\n{'='*60}")
        print("METRICS SUMMARY")
        print(f"{'='*60}")

        print(f"\nTotal Events: {metrics['total_events']}")

        print(f"\nVALIDATION:")
        print(f"  Count: {metrics['validation']['count']}")
        print(f"  Avg: {metrics['validation']['avg_ms']:.0f}ms")
        print(f"  P95: {metrics['validation']['p95_ms']:.0f}ms")

        print(f"\nDISCOVERY:")
        print(f"  Count: {metrics['discovery']['count']}")
        print(f"  Avg: {metrics['discovery']['avg_ms']:.0f}ms")
        print(f"  P95: {metrics['discovery']['p95_ms']:.0f}ms")

        print(f"\nEXECUTION:")
        print(f"  Count: {metrics['execution']['count']}")
        print(f"  Avg: {metrics['execution']['avg_ms']:.0f}ms")
        print(f"  P95: {metrics['execution']['p95_ms']:.0f}ms")
        print(f"  Failed: {metrics['execution']['failed']}")
        print(f"  Total Retries: {metrics['execution']['total_retries']}")

        print(f"\nSUCCESS RATE: {metrics['success_rate']['percent']:.1f}%")

        print(f"\n{'='*60}\n")


# CLI Usage
if __name__ == "__main__":
    logger = JSONLogger()

    # Example events
    logger.log_validation_complete("terraform-architect", True, 1200, 5, 0)
    logger.log_discovery_complete("terraform-architect", 15, 3, 0, 4500)
    logger.log_execution_complete("terraform-architect", "terraform plan", "success", 32000, 0, 0, 145)

    logger.print_metrics_summary()
    report_path = logger.save_metrics_report()
    print(f"Metrics saved to: {report_path}")