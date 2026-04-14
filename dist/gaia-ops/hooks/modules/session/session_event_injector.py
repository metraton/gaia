"""Session event injection for agent context.

Subsystem 4 of the pre_tool_use Task/Agent path.

Filters events by agent domain and injects them into agent prompts.
Includes the hardcoded agent-to-event mapping.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Agent-to-event-type mapping: which events each agent should see
AGENT_EVENT_FILTERS = {
    "terraform-architect": ["git_commit", "infrastructure_change"],
    "gitops-operator": ["git_commit", "git_push", "infrastructure_change"],
    "developer": ["git_commit", "file_modifications"],
    "cloud-troubleshooter": "*",  # All events (needs full history for diagnosis)
    "gaia-system": "*",  # All events (workflow analysis)
    "gaia-operator": ["git_commit", "file_modifications", "infrastructure_change"],
    "gaia-planner": ["git_commit", "file_modifications"],
}


def filter_events_for_agent(events: list, agent: str) -> list:
    """
    Filter events relevant to agent domain.

    Args:
        events: List of critical events from session
        agent: Agent type (e.g., "gitops-operator")

    Returns:
        Filtered list of events relevant to agent
    """
    agent_filter = AGENT_EVENT_FILTERS.get(agent, [])

    # Return all events for wildcard agents
    if agent_filter == "*":
        return events[-10:]  # Last 10 events

    # Filter by event type and return max 10
    filtered = [
        e for e in events[-20:]  # Search last 20
        if e.get("event_type") in agent_filter
    ]

    return filtered[:10]  # Return max 10


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


def build_session_events(
    parameters: dict,
    project_agents: list,
) -> str | None:
    """
    Build session events string for agent context without mutating parameters.

    Filters events by agent domain to avoid noise.
    Returns the events string suitable for additionalContext injection,
    or None if no events to inject.

    Args:
        parameters: Task tool parameters (read-only).
        project_agents: List of valid project agent names.

    Returns:
        Session events string, or None if nothing to inject.
    """
    subagent_type = parameters.get("subagent_type", "")

    # Only inject for project agents
    if subagent_type not in project_agents:
        logger.debug(f"Skipping session events for non-project agent: {subagent_type}")
        return None

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

        # Filter by agent domain
        filtered = filter_events_for_agent(events, subagent_type)

        if not filtered:
            logger.debug(f"No relevant events for {subagent_type}")
            return None

        # Format events summary
        events_summary = format_events_summary(filtered)

        events_string = (
            "# Recent Session Events (Auto-Injected, Last 24h)\n"
            f"{events_summary}"
        )
        logger.info(f"Session events built for {subagent_type} ({len(filtered)} events)")

        return events_string

    except Exception as e:
        logger.warning(f"Failed to build session events: {e}")
        return None


