"""
gaia.paths -- Canonical path resolver and layout manager for Gaia storage substrate.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram; patterns lifted with attribution.

Public API::

    from gaia.paths import (
        data_dir,
        db_path,
        snapshot_dir,
        state_dir,
        workspaces_dir,
        logs_dir,
        events_dir,
        cache_dir,
        ensure_layout,
        workspace_id,  # alias for gaia.project.current()
    )

Environment variables:
    GAIA_DATA_DIR: Override the root data directory (default: ~/.gaia).
"""

from gaia.paths.layout import ensure_layout
from gaia.paths.resolver import (
    cache_dir,
    data_dir,
    db_path,
    events_dir,
    logs_dir,
    snapshot_dir,
    state_dir,
    workspaces_dir,
)

__all__ = [
    "data_dir",
    "db_path",
    "snapshot_dir",
    "state_dir",
    "workspaces_dir",
    "logs_dir",
    "events_dir",
    "cache_dir",
    "ensure_layout",
    "workspace_id",
]


def workspace_id(cwd=None):
    """Return the current workspace identity (alias for gaia.project.current).

    Derives workspace identity from the git remote URL (canonical form:
    ``host/owner/repo``), with fallback to directory name, then ``'global'``.

    Args:
        cwd: Directory to resolve identity for. Defaults to Path.cwd().

    Returns:
        Workspace identity string (never empty).
    """
    from gaia.project import current
    return current(cwd=cwd)
