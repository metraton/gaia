"""Event writer and reader for the GAIA Event Context system.

Provides:
    - EventWriter: append-only JSONL writer with file locking
    - read_events(): read events from last N hours with optional filtering
    - cleanup_old_events(): remove events older than N days
    - Event type constants
"""

import fcntl
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.paths import get_events_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

AGENT_DISPATCH = "agent.dispatch"
AGENT_COMPLETE = "agent.complete"
COMMAND_EXECUTED = "command.executed"
SESSION_END = "session.end"
TRIGGER_SCHEDULED = "trigger.scheduled"
HEARTBEAT = "heartbeat"
USER_NOTE = "user.note"


class EventWriter:
    """Append-only JSONL event writer with file locking.

    All writes are wrapped in try/except -- events are non-critical and
    must never block the hook pipeline.
    """

    def __init__(self, events_dir: Optional[Path] = None):
        self.events_dir = events_dir or get_events_dir()
        self.events_file = self.events_dir / "events.jsonl"
        self.lock_file = self.events_dir / "events.jsonl.lock"

    def write_event(
        self,
        event_type: str,
        source: str,
        agent: str,
        result: str,
        severity: str = "info",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a single event to the JSONL log.

        Thread-safe via exclusive file lock.  Fails silently on any error
        to avoid disrupting the hook pipeline.

        Args:
            event_type: Dotted event category (e.g. "agent.dispatch").
            source: Who wrote the event (e.g. "hook").
            agent: Agent involved, or empty string for non-agent events.
            result: Outcome summary string.
            severity: info | warning | error.
            meta: Optional type-specific structured data.
        """
        try:
            self.events_dir.mkdir(parents=True, exist_ok=True)

            record: Dict[str, Any] = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": event_type,
                "source": source,
                "agent": agent,
                "result": result,
                "severity": severity,
            }
            if meta:
                record["meta"] = meta

            with open(self.lock_file, "w") as lf:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
                try:
                    with open(self.events_file, "a") as f:
                        f.write(json.dumps(record, separators=(",", ":")) + "\n")
                finally:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

        except Exception as exc:
            logger.debug("Event write failed (non-fatal): %s", exc)


def read_events(
    hours: int = 24,
    event_type: Optional[str] = None,
    limit: int = 50,
    events_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read recent events from the JSONL log.

    Args:
        hours: How far back to look (default 24h).
        event_type: Optional filter by event type (exact match).
        limit: Maximum number of events to return.
        events_dir: Override events directory (for testing).

    Returns:
        List of event dicts, most recent last, capped at *limit*.
    """
    try:
        edir = events_dir or get_events_dir()
        events_file = edir / "events.jsonl"
        if not events_file.exists():
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        results: List[Dict[str, Any]] = []

        with open(events_file, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evt = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Time filter
                try:
                    ts = datetime.fromisoformat(evt.get("ts", ""))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue

                # Type filter
                if event_type and evt.get("type") != event_type:
                    continue

                results.append(evt)

        # Return the most recent events, capped at limit
        return results[-limit:]

    except Exception as exc:
        logger.debug("Event read failed (non-fatal): %s", exc)
        return []


def cleanup_old_events(
    days: int = 7,
    events_dir: Optional[Path] = None,
) -> int:
    """Remove events older than *days* from the JSONL log.

    Uses file locking to avoid conflicts with concurrent writers.
    Retains lines that cannot be parsed (conservative).

    Args:
        days: Retention window in days (default 7).
        events_dir: Override events directory (for testing).

    Returns:
        Number of events removed.
    """
    try:
        edir = events_dir or get_events_dir()
        events_file = edir / "events.jsonl"
        lock_file = edir / "events.jsonl.lock"

        if not events_file.exists():
            return 0

        retention_days = int(os.environ.get("GAIA_EVENT_RETENTION_DAYS", str(days)))
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        kept: List[str] = []
        removed = 0

        with open(lock_file, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                with open(events_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                            ts = datetime.fromisoformat(evt["ts"])
                            if ts < cutoff:
                                removed += 1
                                continue
                        except (json.JSONDecodeError, KeyError, ValueError):
                            pass  # Keep unparseable lines
                        kept.append(line)

                with open(events_file, "w") as f:
                    for line in kept:
                        f.write(line + "\n")
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

        return removed

    except Exception as exc:
        logger.debug("Event cleanup failed (non-fatal): %s", exc)
        return 0
