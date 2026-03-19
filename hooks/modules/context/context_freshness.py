"""
Context freshness checker for SessionStart hook.

Determines whether project-context.json is fresh enough to skip a rescan.
Uses metadata.scan_config.last_scan (preferred) or file mtime as fallback.

Public API:
    - check_freshness(project_root: Path) -> FreshnessResult
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..core.paths import find_claude_dir

logger = logging.getLogger(__name__)

# Context freshness threshold (hours) -- env var overrides default
DEFAULT_FRESHNESS_HOURS = 24


@dataclass(frozen=True)
class FreshnessResult:
    """Result of a context freshness check."""

    is_fresh: bool
    reason: str
    age_hours: float = 0.0


def _get_context_path() -> Path:
    """Return path to project-context.json."""
    claude_dir = find_claude_dir()
    return claude_dir / "project-context" / "project-context.json"


def _read_staleness_from_context(context_path: Path) -> Optional[int]:
    """Read staleness_hours from metadata.scan_config in the context file.

    Returns None if the file cannot be read or the field is absent.
    """
    if not context_path.is_file():
        return None
    try:
        with open(context_path, "r") as f:
            data = json.load(f)
        return (
            int(
                data.get("metadata", {})
                .get("scan_config", {})
                .get("staleness_hours", 0)
            )
            or None
        )
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        return None


def _get_effective_threshold() -> int:
    """Determine the effective freshness threshold in hours."""
    return int(
        os.environ.get(
            "GAIA_SCAN_STALENESS_HOURS",
            os.environ.get("CONTEXT_FRESHNESS_HOURS", str(DEFAULT_FRESHNESS_HOURS)),
        )
    )


def check_freshness(project_root: Path = None) -> FreshnessResult:
    """Check if project-context.json exists and is fresh (< threshold).

    Args:
        project_root: Unused, kept for API compatibility. Context path
            is resolved via find_claude_dir().

    Returns:
        FreshnessResult with is_fresh, reason, and age_hours.
    """
    context_path = _get_context_path()

    if not context_path.exists():
        logger.info("project-context.json not found at %s", context_path)
        return FreshnessResult(is_fresh=False, reason="missing", age_hours=0.0)

    # Determine effective threshold: env var > context file > default
    effective_hours = _get_effective_threshold()
    ctx_hours = _read_staleness_from_context(context_path)
    if ctx_hours and not os.environ.get("GAIA_SCAN_STALENESS_HOURS"):
        effective_hours = ctx_hours

    try:
        # Try metadata.scan_config.last_scan first (more accurate)
        with open(context_path, "r") as f:
            data = json.load(f)
        last_scan = data.get("metadata", {}).get("scan_config", {}).get("last_scan")

        if last_scan:
            scan_dt = datetime.fromisoformat(last_scan)
            now = datetime.now(timezone.utc)
            age = now - scan_dt
            age_hours = age.total_seconds() / 3600.0
            threshold = timedelta(hours=effective_hours)

            if age > threshold:
                logger.info(
                    "project-context.json is stale (last_scan age: %s, threshold: %sh)",
                    age,
                    effective_hours,
                )
                return FreshnessResult(
                    is_fresh=False, reason="stale", age_hours=age_hours
                )

            logger.debug("project-context.json is fresh (last_scan age: %s)", age)
            return FreshnessResult(
                is_fresh=True, reason="fresh", age_hours=age_hours
            )

        # Fallback: use file mtime
        mtime = datetime.fromtimestamp(context_path.stat().st_mtime)
        age = datetime.now() - mtime
        age_hours = age.total_seconds() / 3600.0
        threshold = timedelta(hours=effective_hours)

        if age > threshold:
            logger.info(
                "project-context.json is stale (mtime age: %s, threshold: %sh)",
                age,
                effective_hours,
            )
            return FreshnessResult(
                is_fresh=False, reason="stale", age_hours=age_hours
            )

        logger.debug("project-context.json is fresh (mtime age: %s)", age)
        return FreshnessResult(is_fresh=True, reason="fresh", age_hours=age_hours)

    except Exception as e:
        logger.warning("Error checking context freshness: %s", e)
        return FreshnessResult(is_fresh=False, reason="error", age_hours=0.0)
