"""
Unified path resolution for gaia-ops hooks.

Single source of truth for finding .claude directory and its subdirectories.
"""

import os
import logging
from pathlib import Path
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def find_claude_dir() -> Path:
    """
    Find the .claude directory by searching upward from current location.

    Search order:
    1. If currently in .claude, return it
    2. Check current directory for .claude/
    3. Search parent directories upward
    4. Fall back to current/.claude (without creating it)

    Returns:
        Path to the .claude directory

    Note:
        Result is cached for performance. Clear with find_claude_dir.cache_clear()
    """
    current = Path.cwd()

    # If we're already in a .claude directory, return it
    if current.name == ".claude":
        return current

    # Look for .claude in current directory
    claude_dir = current / ".claude"
    if claude_dir.exists():
        return claude_dir

    # Search upward through parent directories
    for parent in current.parents:
        claude_dir = parent / ".claude"
        if claude_dir.exists():
            return claude_dir

    # Fallback - use current directory's .claude (but don't create it yet)
    logger.warning(f"No .claude directory found, using {current}/.claude")
    return current / ".claude"


def get_logs_dir() -> Path:
    """
    Get the logs directory, creating it if necessary.

    Returns:
        Path to .claude/logs/
    """
    logs_dir = find_claude_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_metrics_dir() -> Path:
    """
    Get the metrics directory, creating it if necessary.

    Returns:
        Path to .claude/metrics/
    """
    metrics_dir = find_claude_dir() / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return metrics_dir


def get_memory_dir(subdir: Optional[str] = None) -> Path:
    """
    Get the memory directory, creating it if necessary.

    Args:
        subdir: Optional subdirectory (e.g., "workflow-episodic")

    Returns:
        Path to .claude/memory/ or .claude/memory/{subdir}/
    """
    memory_dir = find_claude_dir() / "memory"
    if subdir:
        memory_dir = memory_dir / subdir
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def get_session_dir() -> Path:
    """
    Get the active session directory, creating it if necessary.

    Returns:
        Path to .claude/session/active/
    """
    session_dir = find_claude_dir() / "session" / "active"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def get_hooks_config_dir() -> Path:
    """
    Get the hooks config directory.

    Returns:
        Path to hooks/config/
    """
    # Config lives alongside the hooks modules
    hooks_dir = Path(__file__).parent.parent.parent
    config_dir = hooks_dir / "config"
    return config_dir


def clear_path_cache():
    """Clear the cached claude_dir path (useful for testing)."""
    find_claude_dir.cache_clear()
