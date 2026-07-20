"""Compact context builder for post-compaction re-injection.

Builds a lightweight context summary from session data sources.
Each source is independent and fail-safe.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    """Minimal post-compaction identity reminder.

    Full identity lives in agents/gaia-orchestrator.md and is injected at
    session start.  This block only restores the core posture after context
    compaction — it intentionally does NOT list specific agents because
    the agent roster can change and a stale list causes drift.
    """
    return (
        "# Post-Compaction Context Refresh\n\n"
        "You are the orchestrator. Dispatch work via Agent, resume agents via "
        "SendMessage(to: agentId), get user approval via AskUserQuestion."
    )


def _build_activity_block(max_snapshots: int) -> str | None:
    """Build session activity summary from episodes table in gaia.db.

    T6 migration: reads from episodes table instead of run-snapshots.jsonl.
    Selects recent episodes ordered by timestamp DESC with agent, plan_status,
    title/prompt and tier columns (equivalent of run-snapshot data).
    """
    try:
        import sys as _sys
        _hooks_dir = Path(__file__).resolve().parent.parent.parent
        _repo_root = _hooks_dir.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return None

    try:
        ws = _project_current()
    except Exception:
        ws = None

    try:
        con = _store_connect()
        try:
            if ws:
                rows = con.execute(
                    "SELECT agent, plan_status, title, prompt, tier, "
                    "output_tokens_approx, timestamp "
                    "FROM episodes "
                    "WHERE workspace = ? AND agent IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (ws, max_snapshots),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT agent, plan_status, title, prompt, tier, "
                    "output_tokens_approx, timestamp "
                    "FROM episodes "
                    "WHERE agent IS NOT NULL "
                    "ORDER BY timestamp DESC LIMIT ?",
                    (max_snapshots,),
                ).fetchall()
        finally:
            con.close()
    except Exception as e:
        logger.debug("Failed to build activity block (non-fatal): %s", e)
        return None

    if not rows:
        return None

    entries = []
    for row in rows:
        d = dict(row)
        agent = d.get("agent", "unknown")
        status = d.get("plan_status", "unknown") or "unknown"
        title = d.get("title") or d.get("prompt") or ""
        prompt = title[:80]
        tier = d.get("tier") or ""
        tier_str = f" [{tier}]" if tier else ""
        entries.append(f"- {agent} → {status}{tier_str} ({prompt})")

    return "## Session Activity\n" + "\n".join(entries)


def _build_anomalies_block(window_hours: int) -> str | None:
    """Build active anomalies summary from episode_anomalies table in gaia.db.

    T6 migration: reads from episode_anomalies table instead of anomalies.jsonl.
    Queries anomalies within the specified time window by severity.
    """
    try:
        import sys as _sys
        _hooks_dir = Path(__file__).resolve().parent.parent.parent
        _repo_root = _hooks_dir.parent
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from gaia.store.writer import _connect as _store_connect
        from gaia.project import current as _project_current
    except ImportError:
        return None

    try:
        ws = _project_current()
    except Exception:
        ws = None

    # Compare UTC-against-UTC: stored timestamps are UTC (writer._now_iso), so
    # the cutoff must be UTC too. datetime.now() (local naive) would skew the
    # window by the machine's TZ offset and over-report anomalies.
    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    cutoff_iso = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S")

    try:
        con = _store_connect()
        try:
            if ws:
                rows = con.execute(
                    "SELECT severity, type FROM episode_anomalies "
                    "WHERE workspace = ? AND timestamp >= ? "
                    "ORDER BY timestamp DESC LIMIT 40",
                    (ws, cutoff_iso),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT severity, type FROM episode_anomalies "
                    "WHERE timestamp >= ? "
                    "ORDER BY timestamp DESC LIMIT 40",
                    (cutoff_iso,),
                ).fetchall()
        finally:
            con.close()
    except Exception as e:
        logger.debug("Failed to build anomalies block (non-fatal): %s", e)
        return None

    critical_types: list[str] = []
    warning_types: list[str] = []
    for row in rows:
        severity = row[0] or ""
        atype = row[1] or "unknown"
        if severity == "critical":
            critical_types.append(atype)
        elif severity == "warning":
            warning_types.append(atype)

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
