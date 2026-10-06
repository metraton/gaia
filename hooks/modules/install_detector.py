"""
install_detector.py -- the workspace a hook writes under.

Public API::

    resolve_workspace(cwd: Optional[str] = None) -> str
        Returns the current workspace identity via gaia.project.current().
"""

from __future__ import annotations

from typing import Optional


def resolve_workspace(cwd: Optional[str] = None) -> str:
    """Return the current workspace identity.

    Pure delegation to gaia.project.current() -- no fallback logic
    implemented here. B0 owns the three-level fallback (git remote ->
    directory name -> literal "global").

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
