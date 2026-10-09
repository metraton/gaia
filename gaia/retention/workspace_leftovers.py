"""
gaia.retention.workspace_leftovers -- the one line SessionStart shows about
what past work left behind in every repo of the declared workspaces around the
session's directory.

Informative only: nothing here removes, captures or fetches anything. The repo
list comes from the ``projects`` rows of the declared workspaces, so it is as
fresh as their last ``gaia scan`` and costs no scan at session start.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from typing import List, Tuple

from gaia.install_root import owning_root, registered_roots
from gaia.paths import db_path, scratch_dir
from gaia.retention.worktree_reclaim import _remote_default_branch

MAX_REPOS_LISTED = 5

HINT = (
    "`gaia worktree prune-branches --repo <path> --dry-run` lists the branches "
    "that can go; `gaia worktree list --repo <path>` the worktrees"
)


def declared_workspaces_around(start: Path) -> List[str]:
    """Names of the declared workspace owning *start* and of every one rooted beneath it."""
    start = Path(start).resolve()
    roots = registered_roots()
    owner = owning_root(start, roots)
    names = [roots[owner]] if owner is not None else []
    names += [name for root, name in roots.items() if start in root.parents and name not in names]
    return names


def workspace_repos(start: Path) -> List[Path]:
    """Active repos on disk of every declared workspace around *start*."""
    names = declared_workspaces_around(start)
    if not names:
        return []
    connection = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT path FROM projects WHERE status = 'active' AND path IS NOT NULL "
            f"AND workspace IN ({','.join('?' * len(names))}) ORDER BY workspace, name",
            names,
        ).fetchall()
    finally:
        connection.close()
    return [Path(path) for (path,) in rows if (Path(path) / ".git").exists()]


def _git_lines(repo: Path, *args: str) -> List[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout
    return out.splitlines()


def repo_leftovers(repo: Path) -> Tuple[int, int]:
    """(linked worktrees, local branches that are neither checked out nor the default)."""
    listing = _git_lines(repo, "worktree", "list", "--porcelain")
    linked = sum(1 for line in listing if line.startswith("worktree ")) - 1
    checked_out = {line[len("branch refs/heads/"):] for line in listing if line.startswith("branch refs/heads/")}
    default = _remote_default_branch(repo).split("/", 1)[-1]
    local = set(_git_lines(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads"))
    return max(linked, 0), len(local - checked_out - {default})


def leftovers_notice(start: Path) -> str:
    """The notice for the declared workspaces around *start*, or "" when nothing is leftover."""
    names = declared_workspaces_around(start)
    if not names:
        return ""
    counts = []
    for repo in workspace_repos(start):
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
    label = "Workspace " + names[0] if len(names) == 1 else "Workspaces " + ", ".join(names)
    return f"{label}, nothing is deleted: " + "; ".join(shown) + f". {HINT}."
