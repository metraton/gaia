"""Event writer for the GAIA Event Context system.

The event pipeline writes to the ``harness_events`` table in the Gaia SQLite
substrate (``~/.gaia/gaia.db``); ``write_event`` never touches ``events.jsonl``.

Provides:
    - EventWriter: non-blocking, silent-on-failure DB event writer
    - read_events(): read-only reader of the legacy ``events.jsonl`` file
    - Event type constants

The DB write delegates to ``gaia.store.writer.write_harness_event``, which
resolves the DB path through ``gaia.paths.db_path()``: ``GAIA_DB``, else
``GAIA_DATA_DIR``/``gaia.db``, else ``~/.gaia/gaia.db``. When ``gaia`` is not
importable, the repo root (the parent of ``hooks/``) is added to ``sys.path``.
"""

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.paths import get_events_dir

logger = logging.getLogger(__name__)

AGENT_DISPATCH = "agent.dispatch"
AGENT_COMPLETE = "agent.complete"
COMMAND_EXECUTED = "command.executed"
SESSION_END = "session.end"
TRIGGER_SCHEDULED = "trigger.scheduled"
HEARTBEAT = "heartbeat"
USER_NOTE = "user.note"


def _import_store_writer():
    """Import gaia.store.writer, falling back to the repo layout.

    Prefers an importable ``gaia`` package; otherwise adds the repo root (the
    parent of ``hooks/``) to ``sys.path`` and imports from there.
    """
    try:
        from gaia.store import writer as _writer
    except ImportError:
        _repo_root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(_repo_root))
        from gaia.store import writer as _writer
    return _writer


def _resolve_workspace() -> str:
    """Resolve the workspace to attribute this event to via the canonical cascade.

    Delegates to ``gaia.project.resolve_workspace`` (``GAIA_DISPATCH_WORKSPACE``
    -> ``GAIA_WORKSPACE`` -> ``gaia.project.current()`` -> ``"global"``), which
    falls back to ``"global"`` and never returns ``None``.

    Uses the same repo-root import fallback as :func:`_import_store_writer`. Any
    failure returns ``"global"`` so event writing stays non-blocking.
    """
    try:
        try:
            from gaia.project import resolve_workspace as _resolve
        except ImportError:
            _repo_root = Path(__file__).resolve().parents[3]
            sys.path.insert(0, str(_repo_root))
            from gaia.project import resolve_workspace as _resolve
        return _resolve()
    except Exception as exc:
        logger.debug("workspace resolution failed (non-fatal): %s", exc)
        return "global"


class EventWriter:
    """Non-blocking DB event writer.

    All writes are wrapped in try/except -- events are non-critical and must
    never block the hook pipeline. Writes target the ``harness_events`` DB
    table; ``events_dir`` is not used by them.
    """

    def __init__(self, events_dir: Optional[Path] = None):
        # Never raises here: an unwritable data home leaves events_dir None.
        try:
            self.events_dir = events_dir or get_events_dir()
        except OSError:
            self.events_dir = None

    def write_event(
        self,
        event_type: str,
        source: str,
        agent: str,
        result: str,
        severity: str = "info",
        meta: Optional[Dict[str, Any]] = None,
        lean: bool = False,
    ) -> None:
        """Append a single event to the ``harness_events`` DB table.

        Fails silently on any error to avoid disrupting the hook pipeline.

        Args:
            event_type: Dotted event category (e.g. "agent.dispatch").
            source: Who wrote the event (e.g. "hook").
            agent: Agent involved, or empty string for non-agent events.
            result: Outcome summary string.
            severity: info | warning | error.
            meta: Optional type-specific structured data (stored as JSON in
                the ``payload`` column).
            lean: Take the cheaper write for events raised on every tool call
                (``write_harness_event_lean``).
        """
        try:
            writer = _import_store_writer()
            workspace = _resolve_workspace()
            write = writer.write_harness_event_lean if lean else writer.write_harness_event
            write(
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

    Events are written to the ``harness_events`` DB table, not to this file, so
    this only reads what an earlier layout left in ``events.jsonl``. Current
    events are read with
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

                try:
                    ts = datetime.fromisoformat(evt.get("ts", ""))
                    if ts < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue

                if event_type and evt.get("type") != event_type:
                    continue

                results.append(evt)

        return results[-limit:]

    except Exception as exc:
        logger.debug("Event read failed (non-fatal): %s", exc)
        return []
