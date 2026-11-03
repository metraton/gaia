"""
Context freshness checker.

Determines whether the project-context sections of the current workspace in
gaia.db are newer than a threshold: the newest updated_at that
gaia.store.project_context.workspace_sections returns. No rows reads as
is_fresh=False. Nothing under hooks/, gaia/, bin/ or tools/ calls it.

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

# GAIA_SCAN_STALENESS_HOURS, else CONTEXT_FRESHNESS_HOURS, overrides it.
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
        from gaia.store.project_context import workspace_sections

        con = sqlite3.connect(str(db_path))
        try:
            stamps = [s["updated_at"] for s in workspace_sections(con, workspace).values()]
        finally:
            con.close()
    except sqlite3.Error as exc:
        logger.warning("DB error querying freshness for '%s': %s", workspace, exc)
        return None
    return max(filter(None, stamps), default=None)


def check_freshness(project_root: Path = None) -> FreshnessResult:
    """Check whether project-context rows in gaia.db are fresh for this workspace.

    Args:
        project_root: Unused, kept for API compatibility. Workspace identity
            is resolved via gaia.project.current().

    Returns:
        FreshnessResult with is_fresh, reason, and age_hours. Reasons:
            - "missing": gaia.db absent, no workspace, a DB error or no rows
            - "stale": newest updated_at older than threshold
            - "fresh": newest updated_at within threshold
            - "error": the timestamp could not be parsed or compared
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
        # A naive timestamp is read as UTC.
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
