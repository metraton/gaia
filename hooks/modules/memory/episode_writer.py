"""
Episodic memory capture for workflow episodes.

Provides:
    - write(): Store workflow as episodic memory
    - get_session_events(): Read context.json, categorize events
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _record_persist_failure(metrics: Dict[str, Any], reason: str) -> None:
    """Record a rejected episode write to ``harness_events``.

    Appends one row an operator can find via
    ``gaia query --surface harness_events``. Never raises: a failure to
    record the failure must not mask the original one or crash the hook.
    """
    try:
        from ..events.event_writer import EventWriter

        EventWriter().write_event(
            "episode.persist_failed",
            "hook",
            metrics.get("agent", "unknown"),
            reason[:200],
            severity="error",
            meta={
                "task_id": metrics.get("task_id"),
                "session_id": metrics.get("session_id"),
                "plan_status": metrics.get("plan_status"),
            },
        )
    except Exception:
        pass


def get_session_events() -> Dict[str, Any]:
    """
    Get critical events from active session context.

    Returns:
        Dict with categorized session events (commits, pushes, file_mods)
    """
    from ..core.paths import get_session_dir

    context_path = get_session_dir() / "context.json"

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

        result = {}
        if commits:
            result["git_commits"] = commits
        if pushes:
            result["git_pushes"] = pushes
        if file_mods:
            result["file_modifications"] = file_mods

        if result:
            logger.info(f"Found {len(critical_events)} session events")

        return result

    except Exception as e:
        logger.warning(f"Failed to read session events: {e}")
        return {}


def write(
    metrics: Dict[str, Any],
    anomalies: Optional[List[Dict[str, str]]] = None,
    commands_executed: Optional[List[str]] = None,
    outcome_override: Optional[str] = None,
) -> Optional[str]:
    """
    Capture workflow as episodic memory.

    Args:
        metrics: Subagent metrics from workflow; this reads agent, prompt,
            plan_status, exit_code, duration_ms, tags and context_anchor_hits
        anomalies: Detected anomalies from audit(), stored in episode context
        commands_executed: List of commands the turn ran
        outcome_override: Force the recorded outcome instead of deriving it from
            plan_status/exit_code. The derivation reads what the turn CLAIMED
            about itself, so a turn whose envelope says COMPLETE records
            'success' even when the hook refused that claim. A caller that
            knows the turn failed states it here.

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

        prompt = metrics.get("prompt", "")
        if not prompt:
            prompt = f"Task for {metrics.get('agent', 'unknown')}"

        subagent_type = metrics.get("agent", "unknown")
        duration_seconds = metrics.get("duration_ms", 0) / 1000.0 if metrics.get("duration_ms") else None

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
                # IN_PROGRESS, APPROVAL_REQUEST, NEEDS_INPUT and NEEDS_VERIFICATION
                # contain neither substring above.
                outcome = "partial"
                success = None
        elif exit_code == 0:
            outcome = "success"
            success = True
        else:
            outcome = "failed"
            success = False

        if outcome_override:
            outcome = outcome_override
            success = {"success": True, "failed": False}.get(outcome_override)

        tags = [t for t in metrics.get("tags", []) if t]
        if not tags and subagent_type and subagent_type != "unknown":
            tags = [subagent_type]

        session_events = get_session_events()
        context = {"metrics": metrics}
        if session_events:
            context["session_events"] = session_events
            logger.info(f"Enriched episode with session events: {list(session_events.keys())}")
        if anomalies:
            context["anomalies"] = anomalies
            logger.info(f"Episode has {len(anomalies)} anomaly/anomalies")

        anchor_hits = metrics.get("context_anchor_hits")
        if anchor_hits:
            context["context_anchor_hits"] = anchor_hits
            logger.info(
                "Episode anchor hits: %d/%d (%.0f%%)",
                anchor_hits.get("hits", 0),
                anchor_hits.get("total_checked", 0),
                anchor_hits.get("hit_rate", 0) * 100,
            )

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

    except RuntimeError as e:
        # store_episode raises RuntimeError when the DB write fails. Logged at
        # ERROR and recorded in harness_events, which an operator can query;
        # a log line nobody tails cannot be.
        logger.error(f"Failed to persist episodic memory: {e}")
        _record_persist_failure(metrics, str(e))
        return None
    except Exception as e:
        logger.debug(f"Failed to capture episodic memory: {e}")
        _record_persist_failure(metrics, str(e))
        return None
