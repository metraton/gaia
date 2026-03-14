"""
Episodic memory capture for workflow episodes.

Renamed from episode_capture.py. Absorbs get_session_events() from
session_state.py directly into this module.

Provides:
    - write(): Store workflow as episodic memory
    - capture_episodic_memory(): Backward-compatible alias for write()
    - get_session_events(): Read context.json, categorize events
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Session events (absorbed from session_state.py)
# ============================================================================

def get_session_events() -> Dict[str, Any]:
    """
    Get critical events from active session context.

    Returns:
        Dict with categorized session events (commits, pushes, file_mods, speckit)
    """
    context_path = Path(".claude/session/active/context.json")

    if not context_path.exists():
        logger.debug("No session context found")
        return {}

    try:
        with open(context_path, "r") as f:
            context = json.load(f)

        critical_events = context.get("critical_events", [])

        if not critical_events:
            return {}

        commits = [
            {
                "hash": e.get("commit_hash", ""),
                "message": e.get("commit_message", ""),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "git_commit" and e.get("commit_hash")
        ]

        pushes = [
            {
                "branch": e.get("branch", ""),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "git_push" and e.get("branch")
        ]

        file_mods = [
            {
                "count": e.get("modification_count", 0),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "file_modifications"
        ]

        speckit = [
            {
                "command": e.get("command", ""),
                "timestamp": e.get("timestamp", "")
            }
            for e in critical_events
            if e.get("event_type") == "speckit_milestone"
        ]

        result = {}
        if commits:
            result["git_commits"] = commits
        if pushes:
            result["git_pushes"] = pushes
        if file_mods:
            result["file_modifications"] = file_mods
        if speckit:
            result["speckit_milestones"] = speckit

        if result:
            logger.info(f"Found {len(critical_events)} session events")

        return result

    except Exception as e:
        logger.warning(f"Failed to read session events: {e}")
        return {}


# ============================================================================
# Episodic memory capture
# ============================================================================

def write(
    metrics: Dict[str, Any],
    anomalies: Optional[List[Dict[str, str]]] = None,
    commands_executed: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Capture workflow as episodic memory.

    Args:
        metrics: Subagent metrics from workflow (includes plan_status, tier, task description)
        anomalies: Detected anomalies from audit(), stored in episode context
        commands_executed: List of commands extracted from EVIDENCE_REPORT

    Returns:
        Episode ID if stored, None otherwise
    """
    try:
        import importlib.util

        candidates = [
            Path(__file__).parent.parent.parent.parent / "tools" / "memory" / "episodic.py",
            Path(".claude/tools/memory/episodic.py"),
        ]

        episodic_module = None
        for path in candidates:
            if path.exists():
                try:
                    spec = importlib.util.spec_from_file_location("episodic", path)
                    if spec and spec.loader:
                        episodic_module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(episodic_module)
                        logger.debug(f"Loaded episodic module from {path}")
                        break
                except Exception as e:
                    logger.debug(f"Could not load episodic from {path}: {e}")
                    continue

        if not episodic_module:
            logger.debug("Episodic memory module not found - skipping episode capture")
            return None

        memory = episodic_module.EpisodicMemory()

        # Use the real task description captured from the transcript.
        # metrics["prompt"] now holds the first user message (task description)
        # rather than the generic "SubagentStop for <agent>".
        prompt = metrics.get("prompt", "")
        if not prompt:
            prompt = f"Task for {metrics.get('agent', 'unknown')}"

        subagent_type = metrics.get("agent", "unknown")
        duration_seconds = metrics.get("duration_ms", 0) / 1000.0 if metrics.get("duration_ms") else None

        # Determine outcome: prefer plan_status string, fall back to exit_code
        plan_status = metrics.get("plan_status", "")
        exit_code = metrics.get("exit_code", 0)
        if plan_status:
            if "COMPLETE" in plan_status:
                outcome = "success"
                success = True
            elif "BLOCKED" in plan_status or "ERROR" in plan_status:
                outcome = "failed"
                success = False
            else:
                # INVESTIGATING, PLANNING, NEEDS_INPUT -> partial
                outcome = "partial"
                success = None
        elif exit_code == 0:
            outcome = "success"
            success = True
        else:
            outcome = "failed"
            success = False

        # Tags from metrics -- filter empty strings defensively
        tags = [t for t in metrics.get("tags", []) if t]
        if not tags and subagent_type and subagent_type != "unknown":
            tags = [subagent_type]

        # Enrich with session events and anomalies
        session_events = get_session_events()
        context = {"metrics": metrics}
        if session_events:
            context["session_events"] = session_events
            logger.info(f"Enriched episode with session events: {list(session_events.keys())}")
        if anomalies:
            context["anomalies"] = anomalies
            logger.info(f"Episode has {len(anomalies)} anomaly/anomalies")

        # Include context anchor hit tracking if available
        anchor_hits = metrics.get("context_anchor_hits")
        if anchor_hits:
            context["context_anchor_hits"] = anchor_hits
            logger.info(
                "Episode anchor hits: %d/%d (%.0f%%)",
                anchor_hits.get("hits", 0),
                anchor_hits.get("total_checked", 0),
                anchor_hits.get("hit_rate", 0) * 100,
            )

        # P3 CLI compatibility fields
        episode_id = memory.store_episode(
            prompt=prompt,
            clarifications={},
            enriched_prompt=prompt,
            context=context,
            tags=tags,
            outcome=outcome,
            success=success,
            duration_seconds=duration_seconds,
            commands_executed=commands_executed or [],
            workflow_metrics=metrics,
        )

        logger.info(f"Captured episode: {episode_id} (outcome: {outcome}, plan_status: {plan_status})")
        return episode_id

    except Exception as e:
        logger.debug(f"Failed to capture episodic memory: {e}")
        return None


# Backward-compatible alias
capture_episodic_memory = write
