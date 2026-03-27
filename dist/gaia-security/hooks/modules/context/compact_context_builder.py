"""Compact context builder for post-compaction re-injection.

Builds a lightweight context summary from session data sources.
Each source is independent and fail-safe.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..core.paths import get_plugin_data_dir

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_SNAPSHOTS = 5
DEFAULT_ANOMALY_WINDOW_HOURS = 1
DEFAULT_MAX_EVENTS = 5


def build_compact_context(
    *,
    max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
    anomaly_window_hours: int = DEFAULT_ANOMALY_WINDOW_HOURS,
    max_events: int = DEFAULT_MAX_EVENTS,
) -> str:
    """Build compact context for post-compaction re-injection.

    Returns a markdown string with 4 blocks:
    1. Orchestrator identity reminder
    2. Session activity summary (from run-snapshots.jsonl)
    3. Active anomalies (from anomalies.jsonl)
    4. Recent session events (from context.json)

    Each block is independent — if a source fails, the others still produce output.
    """
    blocks = []

    # Block 1: Orchestrator identity (always present, static)
    blocks.append(_build_identity_block())

    # Block 2: Session activity from run-snapshots.jsonl
    activity = _build_activity_block(max_snapshots)
    if activity:
        blocks.append(activity)

    # Block 3: Active anomalies from anomalies.jsonl
    anomalies = _build_anomalies_block(anomaly_window_hours)
    if anomalies:
        blocks.append(anomalies)

    # Block 4: Recent events from context.json
    events = _build_events_block(max_events)
    if events:
        blocks.append(events)

    return "\n\n".join(blocks)


def _build_identity_block() -> str:
    """Static orchestrator identity reminder."""
    return (
        "# Post-Compaction Context Refresh\n\n"
        "You are the orchestrator. Dispatch work via Agent, resume agents via "
        "SendMessage(to: agentId), get user approval via AskUserQuestion. "
        "Never execute infrastructure commands directly.\n"
        "Agents: cloud-troubleshooter, gitops-operator, terraform-architect, "
        "devops-developer, speckit-planner, gaia-system"
    )


def _build_activity_block(max_snapshots: int) -> str | None:
    """Build session activity summary from run-snapshots.jsonl."""
    snapshots_path = (
        get_plugin_data_dir() / "project-context" / "workflow-episodic-memory" / "run-snapshots.jsonl"
    )
    if not snapshots_path.exists():
        return None

    try:
        lines = snapshots_path.read_text().splitlines()
        # Take last N lines
        recent = lines[-max_snapshots:] if len(lines) > max_snapshots else lines

        entries = []
        for line in recent:
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
                agent = snap.get("agent", "unknown")
                status = snap.get("plan_status", "unknown")
                prompt = snap.get("prompt", "")[:80]
                cmd_count = snap.get("commands_executed_count", 0)
                entries.append(f"- {agent} → {status} ({prompt}, {cmd_count} commands)")
            except json.JSONDecodeError:
                continue

        if not entries:
            return None

        return "## Session Activity\n" + "\n".join(entries)

    except Exception as e:
        logger.debug("Failed to build activity block (non-fatal): %s", e)
        return None


def _build_anomalies_block(window_hours: int) -> str | None:
    """Build active anomalies summary from anomalies.jsonl."""
    anomaly_path = (
        get_plugin_data_dir() / "project-context" / "workflow-episodic-memory" / "anomalies.jsonl"
    )
    if not anomaly_path.exists():
        return None

    try:
        lines = anomaly_path.read_text().splitlines()[-20:]
        cutoff = datetime.now().timestamp() - (window_hours * 3600)

        critical_types: list[str] = []
        warning_types: list[str] = []

        for line in lines:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "")
                if ts:
                    try:
                        entry_time = datetime.fromisoformat(ts).timestamp()
                        if entry_time < cutoff:
                            continue
                    except (ValueError, TypeError):
                        continue

                for anomaly in entry.get("anomalies", []):
                    severity = anomaly.get("severity", "")
                    atype = anomaly.get("type", "unknown")
                    if severity == "critical":
                        critical_types.append(atype)
                    elif severity == "warning":
                        warning_types.append(atype)
            except json.JSONDecodeError:
                continue

        if not critical_types and not warning_types:
            return None

        parts = []
        if critical_types:
            unique = sorted(set(critical_types))
            parts.append(f"- {len(critical_types)} critical: {', '.join(unique)}")
        if warning_types:
            unique = sorted(set(warning_types))
            parts.append(f"- {len(warning_types)} warning: {', '.join(unique)}")

        return "## Active Anomalies\n" + "\n".join(parts)

    except Exception as e:
        logger.debug("Failed to build anomalies block (non-fatal): %s", e)
        return None


def _build_events_block(max_events: int) -> str | None:
    """Build recent events summary from session context.json."""
    context_path = Path(".claude/session/active/context.json")
    if not context_path.exists():
        return None

    try:
        with open(context_path) as f:
            context = json.load(f)

        events = context.get("critical_events", [])
        if not events:
            return None

        # Take last N events
        recent = events[-max_events:]

        lines = []
        for event in recent:
            etype = event.get("event_type", "")
            ts = event.get("timestamp", "")[:16]

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

        if not lines:
            return None

        return "## Recent Events\n" + "\n".join(lines)

    except Exception as e:
        logger.debug("Failed to build events block (non-fatal): %s", e)
        return None
