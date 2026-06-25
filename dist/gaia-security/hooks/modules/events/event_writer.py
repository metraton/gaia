"""Event writer for the GAIA Event Context system.

As of Brief 54 / Task 2.2 the event pipeline writes to the ``harness_events``
table in the Gaia SQLite substrate (``~/.gaia/gaia.db``) instead of the legacy
``events.jsonl`` file. This is an ATOMIC cutover: ``write_event`` no longer
touches ``events.jsonl`` in any code path -- there is NO dual-write.

Provides:
    - EventWriter: non-blocking, silent-on-failure DB event writer
    - read_events(): legacy JSONL reader (read-only; retained until Task 2.3
      removes events.jsonl entirely -- no longer the canonical read path)
    - Event type constants

The DB write delegates to ``gaia.store.writer.write_harness_event``, which
resolves the DB path the same way every other gaia DB writer does (via
``gaia.paths.db_path()`` -> ``GAIA_DATA_DIR`` / ``gaia.db``, falling back to
``~/.gaia/gaia.db``). The hook subprocess imports the ``gaia`` package via the
repo-root fallback already established by handoff_persister.
"""

import json
import logging
import os
import sys
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


def _import_store_writer():
    """Import gaia.store.writer, falling back to the repo layout.

    Mirrors the import contract used by
    hooks/modules/agents/handoff_persister.py: prefer a sibling ``gaia``
    package if installed; otherwise add the repo root (two levels above
    ``hooks/``) to ``sys.path`` and import from there.
    """
    try:
        from gaia.store import writer as _writer
    except ImportError:
        _repo_root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(_repo_root))
        from gaia.store import writer as _writer
    return _writer


class EventWriter:
    """Non-blocking DB event writer.

    All writes are wrapped in try/except -- events are non-critical and must
    never block the hook pipeline. The ``events_dir`` argument is retained for
    backward compatibility (legacy JSONL reads still resolve it) but is no
    longer used for writes, which target the ``harness_events`` DB table.
    """

    def __init__(self, events_dir: Optional[Path] = None):
        # Retained for compatibility with the legacy reader; not used for
        # writes. Resolved lazily-safe (never raises here).
        self.events_dir = events_dir or get_events_dir()

    def write_event(
        self,
        event_type: str,
        source: str,
        agent: str,
        result: str,
        severity: str = "info",
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a single event to the ``harness_events`` DB table.

        Fails silently on any error to avoid disrupting the hook pipeline --
        same contract as the historical file writer.

        Args:
            event_type: Dotted event category (e.g. "agent.dispatch").
            source: Who wrote the event (e.g. "hook").
            agent: Agent involved, or empty string for non-agent events.
            result: Outcome summary string.
            severity: info | warning | error.
            meta: Optional type-specific structured data (stored as JSON in
                the ``payload`` column).
        """
        try:
            writer = _import_store_writer()
            workspace = os.environ.get("GAIA_WORKSPACE") or None
            writer.write_harness_event(
                event_type=event_type,
                source=source,
                agent=agent,
                result=result,
                severity=severity,
                meta=meta,
                workspace=workspace,
            )
        except Exception as exc:
            logger.debug("Event write failed (non-fatal): %s", exc)


def read_events(
    hours: int = 24,
    event_type: Optional[str] = None,
    limit: int = 50,
    events_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Read recent events from the legacy JSONL log.

    NOTE: As of Task 2.2 this is no longer the canonical read path -- new
    events are written to the ``harness_events`` DB table. This reader is
    retained read-only until Task 2.3 removes ``events.jsonl`` entirely, so
    historical pre-cutover events remain consultable. New callers should use
    ``gaia.store.reader.cross_surface_query(surface="harness_events")``.

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
