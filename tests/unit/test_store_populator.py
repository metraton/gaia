"""
Unit tests for store_populator._list_repos git-filter guard.

Verifies that _list_repos only includes subdirectories that contain a .git
entry (file or directory), filtering out plain directories that are not git
repos (e.g. organizational workspace children with no .git).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# test_list_repos_filters_subdirs_without_git
# ---------------------------------------------------------------------------

def test_list_repos_filters_subdirs_without_git(tmp_path: Path) -> None:
    """Only subdirs with .git (as dir) are included; others are excluded."""
    from tools.scan.store_populator import _list_repos

    # Two repos with .git dir
    repo_a = tmp_path / "repo_a"
    repo_a.mkdir()
    (repo_a / ".git").mkdir()

    repo_b = tmp_path / "repo_b"
    repo_b.mkdir()
    (repo_b / ".git").mkdir()

    # One plain subdir without .git
    plain = tmp_path / "plain_subdir"
    plain.mkdir()

    result = _list_repos(tmp_path)

    assert sorted(result) == [repo_a, repo_b]


# ---------------------------------------------------------------------------
# test_list_repos_handles_git_file  (worktree case)
# ---------------------------------------------------------------------------

def test_list_repos_handles_git_file(tmp_path: Path) -> None:
    """.git as a regular file (worktree) is also accepted."""
    from tools.scan.store_populator import _list_repos

    worktree = tmp_path / "worktree_repo"
    worktree.mkdir()
    # Simulate a worktree: .git is a file containing "gitdir: ..."
    (worktree / ".git").write_text("gitdir: /some/path/.git/worktrees/branch\n")

    no_git = tmp_path / "no_git_dir"
    no_git.mkdir()

    result = _list_repos(tmp_path)

    assert result == [worktree]


# ---------------------------------------------------------------------------
# test_list_repos_empty_when_no_git_descendants  (organizational workspace)
# ---------------------------------------------------------------------------

def test_list_repos_empty_when_no_git_descendants(tmp_path: Path) -> None:
    """Simulates ~/ws/aaxis/ with subdirs that have no .git -- result is []."""
    from tools.scan.store_populator import _list_repos

    for name in ("bildwiz", "nfi", "qxo", "rnd"):
        (tmp_path / name).mkdir()

    result = _list_repos(tmp_path)

    assert result == []
