"""
gaia.retention.workspace_leftovers -- the one line SessionStart shows about
what past work left behind in every repo of the declared workspace.

Informative only: nothing here removes, captures or fetches anything. The repo
list comes from the ``projects`` rows of the declared workspace that owns the
session's directory, so it is as fresh as that workspace's last ``gaia scan``
and costs no scan at session start.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from gaia.install_root import owning_root, registered_roots
from gaia.paths import db_path, scratch_dir
from gaia.retention.worktree_reclaim import _checked_out_branches, _remote_default_branch

MAX_REPOS_LISTED = 5

HINT = (
    "`gaia worktree prune-branches --repo <path> --dry-run` lists the branches "
    "that can go; `gaia worktree list --repo <path>` the worktrees"
)


def workspace_repos(start: Path) -> Tuple[Optional[str], List[Path]]:
    """The declared workspace owning *start* and its active repos on disk."""
    roots = registered_roots()
    root = owning_root(Path(start).resolve(), roots)
    if root is None:
        return None, []
    name = roots[root]
    connection = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT path FROM projects "
            "WHERE workspace = ? AND status = 'active' AND path IS NOT NULL ORDER BY name",
            (name,),
        ).fetchall()
    finally:
        connection.close()
    return name, [Path(path) for (path,) in rows if (Path(path) / ".git").exists()]


def _git_lines(repo: Path, *args: str) -> List[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout
    return out.splitlines()


def repo_leftovers(repo: Path) -> Tuple[int, int]:
    """(linked worktrees, local branches that are neither checked out nor the default)."""
    linked = sum(
        1 for line in _git_lines(repo, "worktree", "list", "--porcelain")
        if line.startswith("worktree ")
    ) - 1
    default = _remote_default_branch(repo).split("/", 1)[-1]
    idle = (
        set(_git_lines(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
        - _checked_out_branches(repo) - {default}
    )
    return max(linked, 0), len(idle)


def leftovers_notice(start: Path) -> str:
    """The notice for the workspace owning *start*, or "" when it holds nothing leftover."""
    name, repos = workspace_repos(start)
    if name is None:
        return ""
    counts = []
    for repo in repos:
        try:
            worktrees, branches = repo_leftovers(repo)
        except (subprocess.CalledProcessError, OSError):
            continue
        if worktrees or branches:
            counts.append((repo.name, worktrees, branches))
    scratch = scratch_dir()
    scratch_entries = sum(1 for _ in scratch.iterdir()) if scratch.is_dir() else 0
    if not counts and not scratch_entries:
        return ""

    counts.sort(key=lambda item: item[1] + item[2], reverse=True)
    shown = [f"{repo}: {w} worktrees, {b} branches" for repo, w, b in counts[:MAX_REPOS_LISTED]]
    if len(counts) > MAX_REPOS_LISTED:
        shown.append(f"+{len(counts) - MAX_REPOS_LISTED} more repos")
    if scratch_entries:
        shown.append(f"scratch: {scratch_entries} entries")
    return f"Workspace {name}, nothing is deleted: " + "; ".join(shown) + f". {HINT}."
