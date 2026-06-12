"""
Session context writer for PostToolUse hook.

Manages the active session context file, appending critical events
(git commits, pushes, etc.) and applying a time-based retention policy.

Public API:
    - SessionContextWriter  (class with update_context method)
"""

import fcntl
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any

from ..core.paths import get_session_dir

logger = logging.getLogger(__name__)

# Default retention period for session events
DEFAULT_RETENTION_HOURS = 24


class SessionContextWriter:
    """Update active session context for critical events.

    Thread-safe via file locking. Applies a configurable retention
    policy (default 24h, override via SESSION_RETENTION_HOURS env var).
    """

    def __init__(self, context_path: Path = None):
        """Initialize with optional custom context path.

        Args:
            context_path: Override path. Defaults to session_dir/context.json.
        """
        self.context_path = context_path or (get_session_dir() / "context.json")

    def update_context(self, event_data: Dict[str, Any]) -> None:
        """Update active context with event data.

        Appends the event to the critical_events list, applies retention
        policy, and writes back atomically with file locking.

        Args:
            event_data: Event dict (must include at least event_type).
        """
        try:
            self.context_path.parent.mkdir(parents=True, exist_ok=True)

            # File lock to prevent race conditions from parallel tool calls
            lock_path = self.context_path.with_suffix(".lock")
            with open(lock_path, "w") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    context: Dict[str, Any] = {}
                    if self.context_path.exists():
                        with open(self.context_path, "r") as f:
                            context = json.load(f)

                    if "critical_events" not in context:
                        context["critical_events"] = []

                    event_data["timestamp"] = datetime.now().isoformat()

                    context["critical_events"].append(event_data)

                    # Keep only events within retention window
                    retention_hours = int(
                        os.environ.get(
                            "SESSION_RETENTION_HOURS",
                            str(DEFAULT_RETENTION_HOURS),
                        )
                    )
                    cutoff = datetime.now() - timedelta(hours=retention_hours)

                    context["critical_events"] = [
                        event
                        for event in context["critical_events"]
                        if event.get("timestamp")
                        and datetime.fromisoformat(event["timestamp"]) > cutoff
                    ]

                    context["last_modified"] = datetime.now().isoformat()

                    with open(self.context_path, "w") as f:
                        json.dump(context, f, indent=2)
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

            logger.info(
                "Updated context with event: %s",
                event_data.get("event_type", "unknown"),
            )

        except Exception as e:
            logger.error("Error updating active context: %s", e)
