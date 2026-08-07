"""Session event injection for agent context.

Subsystem 4 of the pre_tool_use Task/Agent path.

Kernel injection is agnostic to the receiving agent: every dispatch gets the
same digest -- the most recent events, bounded, with no filter keyed to the
agent's name or type. This module used to carry a per-agent event-type
allowlist (``AGENT_EVENT_FILTERS``) plus an upstream "known project agent"
gate; both branched on agent identity, which is exactly the shape the kernel
must not take. An agent absent from every registry now receives the same
digest a named specialist receives.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Max events any dispatch receives, regardless of who it is.
_MAX_EVENTS = 10


def recent_events(events: list) -> list:
    """Bound the event list to the most recent ``_MAX_EVENTS``, agent-agnostic."""
    return events[-_MAX_EVENTS:]


def format_events_summary(events: list) -> str:
    """
    Format events as readable summary for agent context.

    Args:
        events: List of filtered events

    Returns:
        Formatted markdown string
    """
    if not events:
        return "No recent events"

    lines = []

    for event in events:
        etype = event.get("event_type", "")
        ts = event.get("timestamp", "")[:16]  # YYYY-MM-DDTHH:MM

        if etype == "git_commit":
            msg = event.get("commit_message", "")
            hash_val = event.get("commit_hash", "")[:7]
            if hash_val and msg:
                lines.append(f"- [{ts}] Commit {hash_val}: {msg}")

        elif etype == "git_push":
            branch = event.get("branch", "")
            if branch:
                lines.append(f"- [{ts}] Pushed to {branch}")

        elif etype == "file_modifications":
            count = event.get("modification_count", 0)
            if count:
                lines.append(f"- [{ts}] Modified {count} files")

        elif etype == "infrastructure_change":
            cmd = event.get("command", "")
            if cmd:
                lines.append(f"- [{ts}] Infrastructure: {cmd}")

    return "\n".join(lines) if lines else "No recent events"


def build_session_events(parameters: dict) -> str | None:
    """
    Build session events string for agent context without mutating parameters.

    Every dispatch receives the identical last-``_MAX_EVENTS`` digest --
    unfiltered by event type, agent name, or agent type. Returns the events
    string suitable for additionalContext injection, or None if no events to
    inject.

    Args:
        parameters: Task tool parameters (read-only; kept for API
            continuity with the PreToolUse:Task call site, unused here now
            that injection no longer keys off ``subagent_type``).

    Returns:
        Session events string, or None if nothing to inject.
    """
    # Get session events
    from ..core.paths import get_session_dir
    context_path = get_session_dir() / "context.json"
    if not context_path.exists():
        logger.debug("No session context file found")
        return None

    try:
        with open(context_path, 'r') as f:
            context = json.load(f)

        events = context.get("critical_events", [])
        if not events:
            logger.debug("No critical events in session")
            return None

        bounded = recent_events(events)
        if not bounded:
            return None

        # Format events summary
        events_summary = format_events_summary(bounded)

        events_string = (
            "# Recent Session Events (last 24h)\n"
            f"{events_summary}"
        )
        logger.info(f"Session events built ({len(bounded)} events)")

        return events_string

    except Exception as e:
        logger.warning(f"Failed to build session events: {e}")
        return None
