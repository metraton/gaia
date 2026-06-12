"""
Context freshness checker for SessionStart hook.

Determines whether project-context rows in ~/.gaia/gaia.db are fresh enough
to skip a rescan. Queries the max(updated_at) over project_context_contracts
for the current workspace. When no rows exist (workspace not yet seeded), the
result is is_fresh=False so the SessionStart hook triggers a scan.

Public API:
    - check_freshness(project_root: Path = None) -> FreshnessResult
"""

import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Context freshness threshold (hours) -- env var overrides default
DEFAULT_FRESHNESS_HOURS = 24


@dataclass(frozen=True)
class FreshnessResult:
    """Result of a context freshness check."""

    is_fresh: bool
    reason: str
    age_hours: float = 0.0


def _get_db_path() -> Optional[Path]:
    """Return path to ~/.gaia/gaia.db, preferring the canonical resolver."""
    try:
        from gaia.paths import db_path
        return db_path()
    except Exception:
        return Path.home() / ".gaia" / "gaia.db"


def _resolve_workspace() -> Optional[str]:
    """Return the current workspace identity, or None if it cannot be resolved."""
    try:
        from gaia.project import current as _project_current
        return _project_current()
    except Exception as exc:
        logger.debug("workspace resolution failed: %s", exc)
        return None


def _get_effective_threshold() -> int:
    """Determine the effective freshness threshold in hours."""
    return int(
        os.environ.get(
            "GAIA_SCAN_STALENESS_HOURS",
            os.environ.get("CONTEXT_FRESHNESS_HOURS", str(DEFAULT_FRESHNESS_HOURS)),
        )
    )


def _query_max_updated_at(db_path: Path, workspace: str) -> Optional[str]:
    """Return MAX(updated_at) ISO string for the workspace, or None if no rows."""
    try:
        con = sqlite3.connect(str(db_path))
        row = con.execute(
            "SELECT MAX(updated_at) FROM project_context_contracts WHERE workspace = ?",
            (workspace,),
        ).fetchone()
        con.close()
    except sqlite3.Error as exc:
        logger.warning("DB error querying freshness for '%s': %s", workspace, exc)
        return None
    return row[0] if row and row[0] else None


def check_freshness(project_root: Path = None) -> FreshnessResult:
    """Check whether project-context rows in gaia.db are fresh for this workspace.

    Args:
        project_root: Unused, kept for API compatibility. Workspace identity
            is resolved via gaia.project.current().

    Returns:
        FreshnessResult with is_fresh, reason, and age_hours. Reasons:
            - "missing": gaia.db unavailable or no rows for this workspace
            - "stale": MAX(updated_at) older than threshold
            - "fresh": MAX(updated_at) within threshold
            - "error": exception during evaluation
    """
    db_path = _get_db_path()
    if db_path is None or not db_path.exists():
        logger.info("gaia.db not found at %s", db_path)
        return FreshnessResult(is_fresh=False, reason="missing", age_hours=0.0)

    workspace = _resolve_workspace()
    if not workspace:
        return FreshnessResult(is_fresh=False, reason="missing", age_hours=0.0)

    max_updated_at = _query_max_updated_at(db_path, workspace)
    if not max_updated_at:
        logger.info(
            "no project_context_contracts rows for workspace '%s'", workspace
        )
        return FreshnessResult(is_fresh=False, reason="missing", age_hours=0.0)

    effective_hours = _get_effective_threshold()

    try:
        last_scan_dt = datetime.fromisoformat(max_updated_at)
        # Normalize to UTC: rows written by the scanner use timezone-aware
        # isoformat, but legacy migrations may write naive timestamps.
        if last_scan_dt.tzinfo is None:
            last_scan_dt = last_scan_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        age = now - last_scan_dt
        age_hours = age.total_seconds() / 3600.0
        threshold = timedelta(hours=effective_hours)

        if age > threshold:
            logger.info(
                "workspace '%s' context is stale (age: %s, threshold: %sh)",
                workspace,
                age,
                effective_hours,
            )
            return FreshnessResult(
                is_fresh=False, reason="stale", age_hours=age_hours
            )

        logger.debug(
            "workspace '%s' context is fresh (age: %s)", workspace, age
        )
        return FreshnessResult(is_fresh=True, reason="fresh", age_hours=age_hours)

    except Exception as e:
        logger.warning("Error checking context freshness: %s", e)
        return FreshnessResult(is_fresh=False, reason="error", age_hours=0.0)
