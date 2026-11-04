"""
Shared path-resolution helpers for Gaia episodic memory.

The central function, ``find_highest_claude_root``, walks upward from a
starting directory and returns the ``.claude/``-bearing ancestor that is
*closest to HOME* and looks like a Gaia instance — i.e. the top-most one
that has Gaia-specific subdirectories under ``.claude/``.

This prevents two classes of problems:
1. A nested ``.claude/`` in a sub-repository or dev checkout (e.g.
   ``gaia-dev/.claude/``) shadowing the real Gaia instance.
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

    Walks from *start* (default ``Path.cwd()``) up to ``Path.home()``
    (inclusive) and returns the highest ancestor that has a Gaia-qualified
    ``.claude/``. With none, it falls back to the highest ancestor with any
    ``.claude/``, and with none of those returns ``None``. The result is the
    ancestor, not the ``.claude/`` child. Symlinks are not resolved.
    """
    if start is None:
        start = Path.cwd()

    home = Path.home()

    candidates: list[Path] = [start, *start.parents]

    highest_gaia: Optional[Path] = None
    highest_any: Optional[Path] = None

    for directory in candidates:
        if (directory / ".claude").is_dir():
            highest_any = directory
            if _is_gaia_instance(directory):
                highest_gaia = directory

        if directory == home:
            break

    return highest_gaia if highest_gaia is not None else highest_any
