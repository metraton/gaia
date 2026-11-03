"""
install_detector.py -- the workspace a hook writes under.
"""

from __future__ import annotations

from typing import Optional


def resolve_workspace(cwd: Optional[str] = None) -> str:
    """Return the current workspace identity.

    Pure delegation to gaia.project.current(): the declared workspace
    containing *cwd*, else the literal "global". Unlike
    gaia.project.resolve_workspace it reads no environment variable.

    Args:
        cwd: Optional working directory. When None, gaia.project.current()
             uses Path.cwd() internally.

    Returns:
        Workspace identity string (never empty, never raises).
    """
    from gaia.project import current as _project_current
    if cwd is not None:
        return _project_current(cwd)
    return _project_current()
