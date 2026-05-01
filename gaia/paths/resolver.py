"""
gaia.paths.resolver -- Path resolution for Gaia storage substrate.

Resolves canonical paths for all Gaia state directories and files.
All functions read GAIA_DATA_DIR from the environment on each call
(no caching) so that tests using monkeypatch.setenv work correctly.

Patterns inspired by engram (https://github.com/koaning/engram), MIT License.
No runtime dependency on engram; patterns lifted with attribution.

Public API::

    from gaia.paths.resolver import (
        data_dir,
        db_path,
        snapshot_dir,
        state_dir,
        workspaces_dir,
        logs_dir,
        events_dir,
        cache_dir,
    )
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """Return the root Gaia data directory.

    Respects the GAIA_DATA_DIR environment variable. Falls back to
    ``~/.gaia`` when the variable is not set.

    Returns:
        Absolute Path to the root data directory.
    """
    override = os.environ.get("GAIA_DATA_DIR", "")
    if override:
        return Path(override).resolve()
    return Path.home() / ".gaia"


def db_path() -> Path:
    """Return the path to the main Gaia SQLite database.

    Returns:
        ``data_dir() / "gaia.db"``
    """
    return data_dir() / "gaia.db"


def snapshot_dir() -> Path:
    """Return the path to the snapshot directory.

    Returns:
        ``data_dir() / "snapshot"``
    """
    return data_dir() / "snapshot"


def state_dir() -> Path:
    """Return the path to the state directory.

    Returns:
        ``data_dir() / "state"``
    """
    return data_dir() / "state"


def workspaces_dir() -> Path:
    """Return the path to the workspaces directory.

    Workspace-scoped state lives here, keyed by workspace identity
    (canonical form: ``host/owner/repo``).

    Returns:
        ``data_dir() / "workspaces"``
    """
    return data_dir() / "workspaces"


def logs_dir() -> Path:
    """Return the path to the logs directory.

    Returns:
        ``data_dir() / "logs"``
    """
    return data_dir() / "logs"


def events_dir() -> Path:
    """Return the path to the events directory.

    Returns:
        ``data_dir() / "events"``
    """
    return data_dir() / "events"


def cache_dir() -> Path:
    """Return the path to the cache directory.

    Returns:
        ``data_dir() / "cache"``
    """
    return data_dir() / "cache"
