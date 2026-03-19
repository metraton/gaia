"""
Unified path resolution for gaia-ops hooks.

Single source of truth for finding .claude directory and its subdirectories.

Path resolution follows two base directories:
- PLUGIN_ROOT (.claude/): Code, config, agents, skills, context providers
- PLUGIN_DATA (CLAUDE_PLUGIN_DATA or .claude/ fallback): Logs, sessions,
  approval grants, workflow episodic memory — data that survives plugin updates
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


@lru_cache(maxsize=1)
def get_plugin_data_dir() -> Path:
    """
    Get the base directory for persistent plugin data.

    Resolution order:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code >= 2.1.78)
    2. Fallback to find_claude_dir() for backward compatibility

    Data stored here survives plugin updates. Code and config remain
    under PLUGIN_ROOT (find_claude_dir()).

    Returns:
        Path to the plugin data directory (created if needed)

    Note:
        Result is cached for performance. Clear with clear_path_cache()
    """
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        data_dir = Path(plugin_data)
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir
    # Fallback: data co-located with code in .claude/
    return find_claude_dir()


def get_logs_dir() -> Path:
    """
    Get the logs directory, creating it if necessary.

    Returns:
        Path to .claude/logs/
    """
    logs_dir = get_plugin_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_metrics_dir() -> Path:
    """
    Get the metrics directory, creating it if necessary.

    Returns:
        Path to .claude/metrics/
    """
    metrics_dir = get_plugin_data_dir() / "metrics"
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
    memory_dir = get_plugin_data_dir() / "memory"
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
    session_dir = get_plugin_data_dir() / "session" / "active"
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
    """Clear all cached path results (useful for testing)."""
    find_claude_dir.cache_clear()
    get_plugin_data_dir.cache_clear()
    try:
        from .plugin_mode import clear_mode_cache
        clear_mode_cache()
    except ImportError:
        pass
