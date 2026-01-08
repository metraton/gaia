"""
Metrics collection and aggregation.

Collects execution metrics and provides FUNCTIONAL generate_summary.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict

from ..core.paths import get_metrics_dir

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collect and aggregate execution metrics."""

    def __init__(self, metrics_dir: Optional[Path] = None):
        """
        Initialize metrics collector.

        Args:
            metrics_dir: Override metrics directory (for testing)
        """
        if metrics_dir is not None:
            self.metrics_dir = Path(metrics_dir) if isinstance(metrics_dir, str) else metrics_dir
        else:
            self.metrics_dir = get_metrics_dir()
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

    def record_execution(
        self,
        tool_name: str,
        command: str,
        duration: float,
        success: bool,
        tier: str = "unknown"
    ) -> None:
        """
        Record execution metrics.

        Args:
            tool_name: Name of the tool
            command: Command executed
            duration: Duration in seconds
            success: Whether execution succeeded
            tier: Security tier
        """
        timestamp = datetime.now().isoformat()

        metrics_record = {
            "timestamp": timestamp,
            "tool_name": tool_name,
            "command_type": self._classify_command(command),
            "duration_ms": round(duration * 1000, 2),
            "success": success,
            "tier": tier,
        }

        # Write to monthly metrics file
        metrics_file = self.metrics_dir / f"metrics-{datetime.now().strftime('%Y-%m')}.jsonl"
        try:
            with open(metrics_file, "a") as f:
                f.write(json.dumps(metrics_record) + "\n")
        except Exception as e:
            logger.error(f"Error writing metrics: {e}")

    def _classify_command(self, command: str) -> str:
        """Classify command type for metrics."""
        command_lower = command.lower()
        classifiers = [
            ("terraform", "terraform"),
            ("kubectl", "kubernetes"),
            ("helm", "helm"),
            ("gcloud", "gcp"),
            ("aws", "aws"),
            ("flux", "flux"),
            ("docker", "docker"),
            ("git", "git"),
        ]
        for keyword, classification in classifiers:
            if keyword in command_lower:
                return classification
        return "general"

    def generate_summary(self, days: int = 7) -> Dict[str, Any]:
        """
        Generate FUNCTIONAL metrics summary for the last N days.

        Args:
            days: Number of days to include

        Returns:
            Dictionary with aggregated metrics
        """
        cutoff_date = datetime.now() - timedelta(days=days)
        records = self._load_records_since(cutoff_date)

        if not records:
            return {
                "period_days": days,
                "total_executions": 0,
                "success_rate": 0.0,
                "avg_duration_ms": 0.0,
                "top_commands": [],
                "tier_distribution": {},
                "command_type_distribution": {},
            }

        # Calculate metrics
        total = len(records)
        successes = sum(1 for r in records if r.get("success", True))
        total_duration = sum(r.get("duration_ms", 0) for r in records)

        # Count by command type
        command_types = defaultdict(int)
        for r in records:
            command_types[r.get("command_type", "unknown")] += 1

        # Count by tier
        tiers = defaultdict(int)
        for r in records:
            tiers[r.get("tier", "unknown")] += 1

        # Top command types
        top_commands = sorted(
            command_types.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]

        return {
            "period_days": days,
            "total_executions": total,
            "success_rate": round(successes / total, 4) if total > 0 else 0.0,
            "avg_duration_ms": round(total_duration / total, 2) if total > 0 else 0.0,
            "top_commands": [{"type": t, "count": c} for t, c in top_commands],
            "tier_distribution": dict(tiers),
            "command_type_distribution": dict(command_types),
            "generated_at": datetime.now().isoformat(),
        }

    def _load_records_since(self, cutoff_date: datetime) -> List[Dict]:
        """Load all records since cutoff date."""
        records = []

        # Get all metrics files
        try:
            metrics_files = list(self.metrics_dir.glob("metrics-*.jsonl"))
        except Exception as e:
            logger.error(f"Error listing metrics files: {e}")
            return records

        for metrics_file in metrics_files:
            try:
                with open(metrics_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                            record_time = datetime.fromisoformat(
                                record.get("timestamp", "")
                            )
                            if record_time >= cutoff_date:
                                records.append(record)
                        except (json.JSONDecodeError, ValueError):
                            continue
            except Exception as e:
                logger.debug(f"Error reading {metrics_file}: {e}")

        return records


# Singleton collector
_metrics_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """Get singleton metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def record_metric(
    tool_name: str,
    command: str,
    duration: float,
    success: bool,
    tier: str = "unknown"
) -> None:
    """Record execution metric (convenience function)."""
    get_metrics_collector().record_execution(
        tool_name, command, duration, success, tier
    )


def generate_summary(days: int = 7) -> Dict[str, Any]:
    """Generate metrics summary (convenience function)."""
    return get_metrics_collector().generate_summary(days)
