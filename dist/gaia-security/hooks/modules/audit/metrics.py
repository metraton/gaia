"""
Metrics aggregation from audit logs.

Reads audit-*.jsonl files (the single source of truth for execution data)
and produces aggregated summaries. No write path — audit/logger.py owns writes.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict

from ..core.paths import get_logs_dir

logger = logging.getLogger(__name__)


def _classify_command(command: str) -> str:
    """Classify command type for metrics aggregation."""
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


def _load_audit_records_since(
    logs_dir: Path, cutoff_date: datetime
) -> List[Dict]:
    """Load audit records from audit-*.jsonl files since cutoff date."""
    records = []

    try:
        audit_files = list(logs_dir.glob("audit-*.jsonl"))
    except Exception as e:
        logger.error(f"Error listing audit files: {e}")
        return records

    for audit_file in audit_files:
        try:
            with open(audit_file, "r") as f:
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
            logger.debug(f"Error reading {audit_file}: {e}")

    return records


def generate_summary(
    days: int = 7, logs_dir: Optional[Path] = None
) -> Dict[str, Any]:
    """
    Generate metrics summary from audit logs for the last N days.

    Args:
        days: Number of days to include
        logs_dir: Override logs directory (for testing)

    Returns:
        Dictionary with aggregated metrics:
        - period_days, total_executions, avg_duration_ms
        - top_commands (by classified command_type)
        - tier_distribution, command_type_distribution
    """
    if logs_dir is None:
        logs_dir = get_logs_dir()

    cutoff_date = datetime.now() - timedelta(days=days)
    records = _load_audit_records_since(logs_dir, cutoff_date)

    if not records:
        return {
            "period_days": days,
            "total_executions": 0,
            "avg_duration_ms": 0.0,
            "top_commands": [],
            "tier_distribution": {},
            "command_type_distribution": {},
        }

    total = len(records)
    total_duration = sum(r.get("duration_ms", 0) for r in records)

    # Classify commands from audit log 'command' field
    command_types = defaultdict(int)
    for r in records:
        cmd = r.get("command", "")
        command_types[_classify_command(cmd)] += 1

    # Count by tier
    tiers = defaultdict(int)
    for r in records:
        tiers[r.get("tier", "unknown")] += 1

    # Top command types
    top_commands = sorted(
        command_types.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:10]

    return {
        "period_days": days,
        "total_executions": total,
        "avg_duration_ms": round(total_duration / total, 2) if total > 0 else 0.0,
        "top_commands": [{"type": t, "count": c} for t, c in top_commands],
        "tier_distribution": dict(tiers),
        "command_type_distribution": dict(command_types),
        "generated_at": datetime.now().isoformat(),
    }
