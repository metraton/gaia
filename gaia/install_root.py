"""The folder Gaia was installed in, resolved from beneath, and the declared roots above it.

Writes that belong to the installed folder anchor to it rather than to the
current directory, so a session opened in a subfolder reuses the installed
`.claude` instead of seeding its own. A managed worktree is the exception: it
belongs to no installed folder, so a cwd inside one has no workspace to write to.
Installing never declares a workspace; only `gaia workspace declare` records a root.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from gaia.paths import db_path, worktrees_dir

INIT_MARKER = Path(".claude") / ".plugin-initialized"


class InsideManagedWorktree(ValueError):
    """The path lies inside a managed worktree, so no installed workspace owns it."""


def registered_roots(database: Path | None = None) -> dict[Path, str]:
    """Map every recorded workspace root to its workspace name; empty when unreadable."""
    database = database or db_path()
    if not database.is_file():
        return {}
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT name, root_path FROM workspaces "
                "WHERE root_path IS NOT NULL AND root_path != ''"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {Path(root).resolve(): name for name, root in rows}


def owning_root(path: Path, roots: dict[Path, str]) -> Path | None:
    """The nearest recorded root at or above *path*, or None."""
    for candidate in (path, *path.parents):
        if candidate in roots:
            return candidate
    return None


def managed_worktrees_root(path: Path, roots: dict[Path, str]) -> Path | None:
    """The managed worktrees root strictly containing *path*, or None.

    The candidates are the ones `gaia worktree create` places worktrees under:
    each recorded root's ``.project-worktrees`` and the legacy central root.
    """
    from gaia.worktree import _WORKSPACE_ROOT_DIRNAME

    candidates = [root / _WORKSPACE_ROOT_DIRNAME for root in roots]
    candidates.append(Path(os.path.realpath(worktrees_dir())))
    return next((root for root in candidates if root in path.parents), None)


def installed_root(start: Path | None = None, *, database: Path | None = None) -> Path:
    """The installed workspace root containing *start*; *start* itself when none does.

    A root recorded in the registry wins over the on-disk init marker, because a
    `.claude` seeded by an earlier cwd-anchored write carries the same marker.
    The home directory is never a candidate: `~/.claude` is Claude Code's user
    directory, not an install. A *start* inside a managed worktree raises
    `InsideManagedWorktree` instead of resolving to the workspace holding the
    worktree: writing there from a disposable checkout re-points the
    workspace's hooks at a tree that is later deleted.
    """
    here = (start or Path.cwd()).resolve()
    roots = registered_roots(database)
    worktrees = managed_worktrees_root(here, roots)
    if worktrees is not None:
        raise InsideManagedWorktree(f"{here} is inside the managed worktrees root {worktrees}")
    recorded = owning_root(here, roots)
    if recorded is not None:
        return recorded
    home = Path.home().resolve()
    for candidate in (here, *here.parents):
        if candidate == home:
            break
        if (candidate / INIT_MARKER).exists():
            return candidate
    return here


def workspace_status(root: Path, *, database: Path | None = None) -> dict:
    """Report whether *root* lies inside a declared workspace; never registers one.

    Returns ``{"action": "noop" | "skipped", "details": str}``: ``noop`` names the
    declared workspace that owns *root*, ``skipped`` carries the declare command.
    """
    from gaia.project import not_declared_message

    root = root.resolve()
    roots = registered_roots(database)
    owner = owning_root(root, roots)
    if owner is None:
        return {"action": "skipped", "details": not_declared_message(root)}
    return {"action": "noop", "details": f"{root} is in declared workspace {roots[owner]!r}"}
