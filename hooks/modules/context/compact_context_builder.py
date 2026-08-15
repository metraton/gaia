"""Compact context builder for post-compaction re-injection.

Builds a lightweight context summary from session data sources.
Each source is independent and fail-safe.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_MAX_SNAPSHOTS = 5


def build_compact_context(
    *,
    max_snapshots: int = DEFAULT_MAX_SNAPSHOTS,
) -> str:
    """Build compact context for post-compaction re-injection.

    Returns a markdown string with 2 blocks:
    1. Orchestrator identity reminder
    2. Session activity summary (from the episodes table)

    Each block is independent — if a source fails, the others still produce output.

    Two blocks were removed on 2026-08-14 by the user's decision. ACTIVE
    ANOMALIES arrived with eight warnings that the orchestrator ignored for an
    entire session: an alert nobody attends is noise wearing the appearance of
    an alert, and the honest options were to make it actionable or withdraw it.
    RECENT EVENTS restated what already reaches the session by another route.
    Session activity stays: after compaction it is the only thing that says
    what happened before the context was lost.
    """
    blocks = []

    # Block 1: Orchestrator identity (always present, static)
    blocks.append(_build_identity_block())

    # Block 2: Session activity from the episodes table
    activity = _build_activity_block(max_snapshots)
    if activity:
        blocks.append(activity)

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
