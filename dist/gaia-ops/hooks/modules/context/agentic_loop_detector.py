"""Agentic loop state detector.

Lightweight, fail-safe detection of an active agentic-loop session in the
current working directory.  Used by UserPromptSubmit and PreCompact hooks to
inject recovery context when the loop is running.

Design constraints:
- Non-blocking: never raises, returns empty string on failure
- Lightweight: only file existence + small JSON reads
- Safe: malformed or missing files are silently skipped
- Staleness guard: states older than 24 hours are ignored
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# How old a state file can be before it's considered stale
STALE_HOURS = 24

# Candidate locations to search for loop state, relative to cwd
_STATE_CANDIDATES = [
    "agentic_loop_state.json",
    "state.json",
    "tests/agentic_loop_state.json",
    "tests/state.json",
]


def _parse_state(path: Path) -> Optional[dict]:
    """Read and parse the JSON state file.  Returns None on any error."""
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    except Exception:
        return None


def _is_stale(state: dict) -> bool:
    """Return True if the state's timestamp is older than STALE_HOURS."""
    ts = state.get("timestamp")
    if not ts:
        # No timestamp -- treat as fresh so we don't silently skip real state
        return False
    try:
        ts_clean = ts.replace("Z", "+00:00")
        state_time = datetime.fromisoformat(ts_clean)
        if state_time.tzinfo is None:
            state_time = state_time.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        age_hours = (now - state_time).total_seconds() / 3600
        return age_hours > STALE_HOURS
    except Exception:
        return False


def _is_agentic_loop_state(state: dict) -> bool:
    """Return True if the state dict looks like an agentic-loop state file.

    Checks for the presence of key fields that distinguish agentic-loop
    state from unrelated state.json files (e.g., Node.js build state).
    """
    loop_fields = {"eval_command", "metric", "threshold", "iteration"}
    return bool(loop_fields & state.keys())


def find_active_loop(cwd: Optional[Path] = None) -> Optional[dict]:
    """Search for an active agentic-loop state file.

    Returns the parsed state dict if found, active (not terminal status),
    and not stale.  Returns None otherwise.

    Args:
        cwd: Working directory to search.  Defaults to Path.cwd().
    """
    base = cwd or Path.cwd()

    for candidate in _STATE_CANDIDATES:
        path = base / candidate
        if not path.exists():
            continue

        state = _parse_state(path)
        if state is None:
            continue

        if not _is_agentic_loop_state(state):
            continue

        status = state.get("status", "")
        if status in ("complete", "threshold_reached", "max_iterations", "stopped"):
            logger.debug("Agentic loop at %s is terminal (status=%s), skipping", path, status)
            continue

        if _is_stale(state):
            logger.debug("Agentic loop at %s is stale (>%dh), skipping", path, STALE_HOURS)
            continue

        logger.info("Active agentic loop found: %s (status=%s, iter=%s)", path, status, state.get("iteration"))
        return state

    return None


def build_resume_context(cwd: Optional[Path] = None) -> str:
    """Build the additionalContext block for an active agentic loop.

    Returns an empty string if no active loop is found.
    Silently handles all errors.
    """
    try:
        state = find_active_loop(cwd)
        if state is None:
            return ""

        iteration = state.get("iteration", "?")
        current = state.get("current", state.get("best", "?"))
        threshold = state.get("threshold", "?")
        metric = state.get("metric", "metric")
        goal = state.get("goal", "")

        lines = [
            "## Active Agentic Loop",
        ]
        if goal:
            lines.append(f"Goal: {goal}")
        lines.extend([
            "You are in an agentic-loop session. Read state.json for your objective and progress.",
            f"Current iteration: {iteration}, {metric}: {current}, target: {threshold}. NEVER STOP until threshold or max_iterations.",
        ])

        return "\n".join(lines)

    except Exception as e:
        logger.debug("build_resume_context failed (non-fatal): %s", e)
        return ""


def build_precompact_prompt(cwd: Optional[Path] = None) -> str:
    """Build the pre-compaction instruction block for an active agentic loop.

    Returns an empty string if no active loop is found.
    Silently handles all errors.
    """
    try:
        state = find_active_loop(cwd)
        if state is None:
            return ""

        return (
            "IMPORTANT: You are in an agentic-loop. Before context compaction:\n"
            "1. Write continue.md with: completed work, remaining steps, current iteration, decisions made, next action\n"
            "2. Update state.json with current progress\n"
            "3. Update worklog.md with latest run if not already logged\n"
            "Do this NOW before your context is compacted."
        )

    except Exception as e:
        logger.debug("build_precompact_prompt failed (non-fatal): %s", e)
        return ""
