"""
Unified path resolution for gaia hooks.

Path resolution follows three base directories:
- PLUGIN_ROOT (.claude/): Code, config, agents, skills, context providers
- DATA HOME (gaia.paths.data_dir(): GAIA_DATA_DIR, default ~/.gaia): logs,
  events and session state, shared by every channel so switching between the
  plugin, npm and inline launches keeps one history
- PLUGIN_DATA (CLAUDE_PLUGIN_DATA, else .claude/): what one install knows about
  itself -- plugin-registry.json, the seeded version, the workflow episodic
  record. Approvals and grants live in gaia.db, not here.
"""

import os
import logging
import sys
from pathlib import Path
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


def ensure_package_root_importable() -> None:
    """Make sibling top-level packages (``tools``, ``gaia``) importable in-process.

    The hooks live under ``hooks/``; the ``tools`` and ``gaia`` packages sit
    as siblings at the same level. Adding the package root to sys.path lets
    ``from tools.context.context_provider import ...`` (and the gaia-substrate
    imports the kernel path needs) resolve regardless of cwd -- the dispatch
    path imports them in-process rather than as a subprocess.
    """
    pkg_root = str(Path(__file__).resolve().parents[3])
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)


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
    Get the directory holding what this install records about itself.

    Resolution order:
    1. CLAUDE_PLUGIN_DATA env var (set by Claude Code >= 2.1.78), one per
       plugin id (gaia-gaia-dev, gaia-gaia-marketplace, gaia-inline)
    2. Fallback to find_claude_dir() for backward compatibility

    Logs, events and session state are not kept here: they live in the data
    home (get_data_home()), which every channel shares.

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


def get_data_home() -> Path:
    """The Gaia home every channel shares (``gaia.paths.data_dir()``), created if needed.

    Read on every call rather than cached, like ``gaia.paths``, so a
    GAIA_DATA_DIR override takes effect without clearing a cache.
    """
    ensure_package_root_importable()
    from gaia.paths import data_dir

    home = data_dir()
    home.mkdir(parents=True, exist_ok=True)
    return home


def legacy_data_dirs() -> list[Path]:
    """Directories where an earlier layout left logs or session state, split per channel.

    Candidates are the current CLAUDE_PLUGIN_DATA, every Gaia plugin id under
    ~/.claude/plugins/data, and the workspace .claude (the npm fallback). They
    are reported, never merged or deleted.
    """
    candidates = []
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        candidates.append(Path(plugin_data))
    plugins_data = Path.home() / ".claude" / "plugins" / "data"
    if plugins_data.is_dir():
        candidates.extend(sorted(plugins_data.glob("gaia*")))
    candidates.append(find_claude_dir())

    home = get_data_home().resolve()
    found = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved == home or resolved in found:
            continue
        if (resolved / "logs").is_dir() or (resolved / "session").is_dir():
            found.append(resolved)
    return found


def get_logs_dir() -> Path:
    """
    Get the logs directory, creating it if necessary.

    Returns:
        Path to <data home>/logs/
    """
    logs_dir = get_data_home() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_memory_dir(subdir: Optional[str] = None) -> Path:
    """
    Get the memory directory, creating it if necessary.

    Args:
        subdir: Optional subdirectory (e.g., "workflow-episodic")

    Returns:
        Path to <data home>/memory/ or <data home>/memory/{subdir}/
    """
    memory_dir = get_data_home() / "memory"
    if subdir:
        memory_dir = memory_dir / subdir
    memory_dir.mkdir(parents=True, exist_ok=True)
    return memory_dir


def get_events_dir() -> Path:
    """
    Get the events directory, creating it if necessary.

    Returns:
        Path to <data home>/events/
    """
    events_dir = get_data_home() / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    return events_dir


def get_session_dir() -> Path:
    """
    Get the active session directory, creating it if necessary.

    Returns:
        Path to <data home>/session/active/
    """
    session_dir = get_data_home() / "session" / "active"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def clear_path_cache():
    """Clear all cached path results (useful for testing)."""
    find_claude_dir.cache_clear()
    get_plugin_data_dir.cache_clear()
