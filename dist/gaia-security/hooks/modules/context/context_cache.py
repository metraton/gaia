"""
Context cache for PreToolUse -> SubagentStart handoff.

PreToolUse:Agent builds context (needs the prompt for surface routing) but the
context must reach the subagent, not the orchestrator.  SubagentStart is where
context should be injected, but SubagentStart does not receive the prompt.

Solution: PreToolUse caches the built context to a temp file keyed by
session_id.  SubagentStart reads and consumes the cache (one-shot).

Cache location: /tmp/gaia-context-cache/{session_id}-{timestamp}.json
TTL: 60 seconds (stale files cleaned on write).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_DIR = Path("/tmp/gaia-context-cache")
CACHE_TTL_SECONDS = 60


def write_context_cache(
    session_id: str,
    context: str,
    agent_type: str = "",
) -> Path:
    """Write context to a cache file for later consumption by SubagentStart.

    Args:
        session_id: Hook session identifier (shared between PreToolUse and SubagentStart).
        context: The full additionalContext string to inject into the subagent.
        agent_type: The agent type (for logging/diagnostics).

    Returns:
        Path to the written cache file.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Clean stale files first
    _cleanup_stale_caches()

    timestamp = int(time.time() * 1000)
    cache_file = CACHE_DIR / f"{session_id}-{timestamp}.json"
    payload = {
        "context": context,
        "agent_type": agent_type,
        "timestamp": timestamp,
    }

    cache_file.write_text(json.dumps(payload))
    logger.info(
        "Cached context for session=%s agent=%s (%d bytes) -> %s",
        session_id, agent_type, len(context), cache_file.name,
    )
    return cache_file


def read_context_cache(session_id: str) -> Optional[dict]:
    """Read and consume the most recent context cache for a session.

    Returns the cache payload dict if found, or None if no cache exists.
    The cache file is deleted after reading (one-shot consumption).

    Args:
        session_id: Hook session identifier.

    Returns:
        Dict with keys: context, agent_type, timestamp.  Or None.
    """
    if not CACHE_DIR.exists():
        logger.debug("No cache directory found")
        return None

    # Find all cache files for this session, sorted by timestamp (newest first)
    prefix = f"{session_id}-"
    candidates = sorted(
        [f for f in CACHE_DIR.iterdir() if f.name.startswith(prefix) and f.suffix == ".json"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not candidates:
        logger.debug("No cache files found for session=%s", session_id)
        return None

    cache_file = candidates[0]
    try:
        payload = json.loads(cache_file.read_text())
        logger.info(
            "Read context cache for session=%s agent=%s from %s",
            session_id, payload.get("agent_type", "unknown"), cache_file.name,
        )
        # One-shot: delete after reading
        cache_file.unlink(missing_ok=True)

        # Clean up any older duplicates for this session
        for stale in candidates[1:]:
            stale.unlink(missing_ok=True)

        return payload

    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read cache file %s: %s", cache_file, exc)
        cache_file.unlink(missing_ok=True)
        return None


def _cleanup_stale_caches() -> None:
    """Remove cache files older than CACHE_TTL_SECONDS."""
    if not CACHE_DIR.exists():
        return

    cutoff = time.time() - CACHE_TTL_SECONDS
    for f in CACHE_DIR.iterdir():
        if f.suffix == ".json":
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    logger.debug("Cleaned stale cache: %s", f.name)
            except OSError:
                pass
