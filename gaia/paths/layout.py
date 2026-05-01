"""
gaia.paths.layout -- Directory layout creation for Gaia storage substrate.

Creates the canonical ``~/.gaia/`` directory tree on first use with
mode 0700 (user-only read/write/execute). Idempotent: safe to call
multiple times.

Public API::

    from gaia.paths.layout import ensure_layout
"""

import os

from gaia.paths.resolver import (
    cache_dir,
    data_dir,
    events_dir,
    logs_dir,
    workspaces_dir,
)


def ensure_layout() -> None:
    """Create the canonical Gaia directory layout with mode 0700.

    Directories created:
    - ``data_dir()``         -- root (``~/.gaia/`` or ``$GAIA_DATA_DIR``)
    - ``workspaces_dir()``   -- workspace-scoped state
    - ``logs_dir()``         -- logs
    - ``events_dir()``       -- events
    - ``cache_dir()``        -- cache

    Mode is forced to 0o700 after creation to override any umask
    interference. The function is idempotent: calling it on an existing
    layout does not remove or downgrade permissions.
    """
    dirs = [
        data_dir(),
        workspaces_dir(),
        logs_dir(),
        events_dir(),
        cache_dir(),
    ]
    for path in dirs:
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
