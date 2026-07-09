"""
Metrics aggregation from audit logs.

Reads audit-*.jsonl files (the single source of truth for execution data)
and produces aggregated summaries. No write path — audit/logger.py owns writes.
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
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
                        # Audit records are written with UTC-aware timestamps
                        # (logger.py uses datetime.now(timezone.utc)), but the
                        # cutoff is naive. Comparing aware >= naive raises
                        # TypeError -- which the OUTER except would swallow,
                        # silently dropping every real record. Normalize an
                        # aware timestamp to naive-UTC so the comparison is
                        # valid for both aware (production) and naive (older
                        # test-fixture) records.
                        if record_time.tzinfo is not None:
                            record_time = record_time.astimezone(
                                timezone.utc
                            ).replace(tzinfo=None)
                        if record_time >= cutoff_date:
                            records.append(record)
                    except (json.JSONDecodeError, ValueError, TypeError):
                        continue
        except Exception as e:
            logger.debug(f"Error reading {audit_file}: {e}")

    return records


def _empty_security_events() -> Dict[str, Any]:
    """Zeroed security_events section (shared by the empty + populated paths)."""
    return {
        "t3_degraded_allow": {"total": 0, "by_reason": {}},
        "approval_persist_failed": 0,
    }


def _summarize_security_events(event_records: List[Dict]) -> Dict[str, Any]:
    """Aggregate the always-on synthetic security events over the window.

    Discriminates the two complementary sensors written to audit-*.jsonl:
      * ``t3_degraded_allow`` (event tag) -- a T3 was allowed non-blocking
        because approval persistence failed after retries; broken down
        ``by_reason`` so a query like "degraded T3 allows grouped by reason"
        is answerable.
      * ``approval_persist_failed`` (error record: event=="error" &
        error_type=="approval_persist_failed") -- the raw persist-failure
        sensor; may fire in contexts other than a degraded allow, so it is
        counted separately rather than assumed equal to the degraded count.
    """
    degraded = [e for e in event_records if e.get("event") == "t3_degraded_allow"]
    persist_failed = [
        e
        for e in event_records
        if e.get("event") == "error"
        and e.get("error_type") == "approval_persist_failed"
    ]
    by_reason: Dict[str, int] = defaultdict(int)
    for e in degraded:
        by_reason[e.get("reason") or "unknown"] += 1
    return {
        "t3_degraded_allow": {"total": len(degraded), "by_reason": dict(by_reason)},
        "approval_persist_failed": len(persist_failed),
    }


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
        - security_events (t3_degraded_allow + approval_persist_failed counts)

    Synthetic audit events (records carrying an ``event`` key, e.g.
    ``error`` / ``t3_degraded_allow``) are discriminated OUT of the
    execution-oriented aggregates: they are not tool executions, so counting
    them into ``total_executions`` / tiers / command types would be
    misleading. They are summarized separately under ``security_events``.
    """
    if logs_dir is None:
        logs_dir = get_logs_dir()

    # Naive-UTC cutoff so it is comparable to the UTC-normalized record times
    # produced in _load_audit_records_since (production records are UTC-aware).
    cutoff_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    records = _load_audit_records_since(logs_dir, cutoff_date)

    if not records:
        return {
            "period_days": days,
            "total_executions": 0,
            "avg_duration_ms": 0.0,
            "top_commands": [],
            "tier_distribution": {},
            "command_type_distribution": {},
            "security_events": _empty_security_events(),
        }

    # Split tool-execution records from synthetic event records. An execution
    # record has no ``event`` key; an event record (error / t3_degraded_allow)
    # does. Only execution records feed total_executions / tiers / command types.
    exec_records = [r for r in records if not r.get("event")]
    event_records = [r for r in records if r.get("event")]

    total = len(exec_records)
    total_duration = sum(r.get("duration_ms", 0) for r in exec_records)

    # Classify commands from audit log 'command' field
    command_types = defaultdict(int)
    for r in exec_records:
        cmd = r.get("command", "")
        command_types[_classify_command(cmd)] += 1

    # Count by tier
    tiers = defaultdict(int)
    for r in exec_records:
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
        "security_events": _summarize_security_events(event_records),
        "generated_at": datetime.now().isoformat(),
    }
