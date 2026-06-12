"""
Shared path-resolution helpers for Gaia episodic memory.

The central function, ``find_highest_claude_root``, walks upward from a
starting directory and returns the ``.claude/``-bearing ancestor that is
*closest to HOME* and looks like a Gaia instance — i.e. the top-most one
that has Gaia-specific subdirectories under ``.claude/``.

This prevents two classes of problems:
1. A nested ``.claude/`` in a sub-repository or dev checkout (e.g.
   ``gaia-ops-dev/.claude/``) shadowing the real Gaia instance.
2. Claude Code's own user settings dir (``$HOME/.claude/``) being mistaken
   for a Gaia instance — it has a ``.claude/`` but no Gaia-specific layout.
"""

from pathlib import Path
from typing import Optional

# Subdirectories that signal "this .claude/ belongs to a Gaia instance".
# Claude Code's own $HOME/.claude/ never has hooks/ or agents/.
_GAIA_MARKERS = ("hooks", "agents", "skills")


def _is_gaia_instance(directory: Path) -> bool:
    """Return True if *directory*/.claude/ looks like a Gaia instance.

    A directory qualifies as a Gaia instance root if its ``.claude/``
    subdirectory contains at least one of the Gaia-specific marker dirs
    (``hooks/``, ``agents/``, ``skills/``).  This distinguishes Gaia
    instance roots from Claude Code's own ``$HOME/.claude/`` which only
    contains user settings.

    Parameters
    ----------
    directory:
        The candidate root directory (the parent of ``.claude/``).
    """
    claude_dir = directory / ".claude"
    if not claude_dir.is_dir():
        return False
    return any((claude_dir / marker).is_dir() for marker in _GAIA_MARKERS)


def find_highest_claude_root(start: Optional[Path] = None) -> Optional[Path]:
    """Return the highest Gaia instance root at or above *start*.

    Walk from *start* (defaults to ``Path.cwd()``) up to ``Path.home()``
    (inclusive).  Every directory that has a ``.claude/`` child **and**
    passes the Gaia-instance check (has ``hooks/``, ``agents/``, or
    ``skills/`` under ``.claude/``) is collected; the one *closest to HOME*
    (highest in the hierarchy) is returned.

    If no Gaia-qualified ``.claude/`` is found anywhere in the walk, the
    function falls back to returning the highest plain ``.claude/`` ancestor
    (matching the original semantics), so callers that don't have a full
    Gaia layout still get a reasonable result.

    Edge cases
    ----------
    - If no ``.claude/`` directory is found at all, returns ``None``.
    - If *start* is already above ``Path.home()`` the walk is bounded at
      ``Path.home()``; if that yields no candidates the function returns
      ``None``.
    - Symlinks in the path are **not** resolved — the walk uses the logical
      path reported by the OS, consistent with the rest of the project.

    Parameters
    ----------
    start:
        Directory from which to begin the upward walk.  Defaults to
        ``Path.cwd()``.

    Returns
    -------
    Path or None
        The ancestor path (not the ``.claude/`` child itself) that should
        be used as the Gaia instance root, or ``None`` if no such ancestor
        exists within the walk range.
    """
    if start is None:
        start = Path.cwd()

    home = Path.home()

    # Walk from start up to home (inclusive).
    candidates: list[Path] = [start, *start.parents]

    highest_gaia: Optional[Path] = None   # best match: Gaia-qualified
    highest_any: Optional[Path] = None    # fallback: any .claude/

    for directory in candidates:
        if (directory / ".claude").is_dir():
            highest_any = directory  # keep updating — last = highest
            if _is_gaia_instance(directory):
                highest_gaia = directory  # keep updating — last = highest

        # Stop at HOME; don't traverse system directories above it.
        if directory == home:
            break

    # Prefer a Gaia-qualified root; fall back to any .claude/ if none found.
    return highest_gaia if highest_gaia is not None else highest_any
