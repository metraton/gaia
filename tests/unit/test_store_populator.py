"""
Unit tests for store_populator._list_repos.

Verifies that _list_repos:
  - discovers git repos at arbitrary nesting depth (AC-1)
  - treats .git files (worktrees) as valid repos
  - excludes plain directories that have no .git (AC-3)
  - respects the skip-dir set (node_modules, .claude, briefs, plans, ...)
  - returns paths whose .parent can be used by T2.2 to infer group_name
  - is bounded by max_depth to avoid runaway traversal
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_git_dir(path: Path) -> None:
    """Create a minimal .git directory inside path."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


def _make_plain_dir(path: Path) -> None:
    """Create a plain (non-git) directory."""
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Legacy behaviour preserved (depth-1 cases)
# ---------------------------------------------------------------------------

def test_list_repos_filters_subdirs_without_git(tmp_path: Path) -> None:
    """Only subdirs with .git (as dir) are included; others are excluded."""
    from tools.scan.store_populator import _list_repos

    # Two repos with .git dir
    repo_a = tmp_path / "repo_a"
    _make_git_dir(repo_a)

    repo_b = tmp_path / "repo_b"
    _make_git_dir(repo_b)

    # One plain subdir without .git
    plain = tmp_path / "plain_subdir"
    plain.mkdir()

    result = _list_repos(tmp_path)

    assert sorted(result) == [repo_a, repo_b]


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


def test_list_repos_empty_when_no_git_descendants(tmp_path: Path) -> None:
    """Simulates ~/ws/aaxis/ with subdirs that have no .git -- result is []."""
    from tools.scan.store_populator import _list_repos

    for name in ("bildwiz", "nfi", "qxo", "rnd"):
        (tmp_path / name).mkdir()

    result = _list_repos(tmp_path)

    assert result == []


# ---------------------------------------------------------------------------
# AC-1: Recursive discovery -- nested repos in containers
# ---------------------------------------------------------------------------

def test_list_repos_finds_nested_repos_in_container(tmp_path: Path) -> None:
    """Container dir with no .git is descended; repos inside are found."""
    from tools.scan.store_populator import _list_repos

    # Layout: tmp/github-repos/{repo_a, repo_b} -- mirrors real ME workspace
    container = tmp_path / "github-repos"
    container.mkdir()

    repo_a = container / "repo-alpha"
    _make_git_dir(repo_a)

    repo_b = container / "repo-beta"
    _make_git_dir(repo_b)

    plain = container / "not-a-repo"
    plain.mkdir()

    result = _list_repos(tmp_path)

    assert sorted(result) == [repo_a, repo_b]


def test_list_repos_finds_repos_two_levels_deep(tmp_path: Path) -> None:
    """Repos found 2 levels below root (aaxis/bildwiz/platform-repo)."""
    from tools.scan.store_populator import _list_repos

    # Layout: tmp/aaxis/bildwiz/platform-repo/.git
    aaxis = tmp_path / "aaxis"
    bildwiz = aaxis / "bildwiz"
    repo = bildwiz / "platform-repo"
    _make_git_dir(repo)

    result = _list_repos(tmp_path)

    assert result == [repo]


def test_list_repos_finds_repos_three_levels_deep(tmp_path: Path) -> None:
    """Repos found 3 levels below root (group/sub/child/repo)."""
    from tools.scan.store_populator import _list_repos

    deep_repo = tmp_path / "org" / "team" / "project"
    _make_git_dir(deep_repo)

    result = _list_repos(tmp_path)

    assert result == [deep_repo]


def test_list_repos_mixed_depths(tmp_path: Path) -> None:
    """Repos at different depths under the same root are all found."""
    from tools.scan.store_populator import _list_repos

    # depth-1 repo
    shallow = tmp_path / "shallow-repo"
    _make_git_dir(shallow)

    # depth-2 repo
    deep = tmp_path / "container" / "deep-repo"
    _make_git_dir(deep)

    result = _list_repos(tmp_path)

    assert sorted(result) == [deep, shallow]


def test_list_repos_does_not_recurse_into_repo(tmp_path: Path) -> None:
    """A repo is a leaf: inner directories inside a repo are NOT returned."""
    from tools.scan.store_populator import _list_repos

    outer_repo = tmp_path / "outer"
    _make_git_dir(outer_repo)

    # Submodule-like inner repo inside outer_repo -- should NOT appear.
    inner_repo = outer_repo / "vendor" / "submodule"
    inner_repo.mkdir(parents=True)
    (inner_repo / ".git").mkdir()

    result = _list_repos(tmp_path)

    # Only the outer repo should appear.
    assert result == [outer_repo]


def test_list_repos_parent_gives_group_name(tmp_path: Path) -> None:
    """Each returned path's .parent gives the container dir for T2.2 group_name."""
    from tools.scan.store_populator import _list_repos

    container = tmp_path / "github-repos"
    repo_a = container / "alpha"
    repo_b = container / "beta"
    _make_git_dir(repo_a)
    _make_git_dir(repo_b)

    result = _list_repos(tmp_path)

    # T2.2 will call path.parent to infer the group: both resolve to "container"
    for path in result:
        assert path.parent == container


# ---------------------------------------------------------------------------
# AC-3: Plain (non-git) sidecar folders are excluded
# ---------------------------------------------------------------------------

def test_list_repos_excludes_briefs_dir(tmp_path: Path) -> None:
    """briefs/ directory is never returned as a repo (AC-3)."""
    from tools.scan.store_populator import _list_repos

    briefs = tmp_path / "briefs"
    briefs.mkdir()
    # Even if someone puts files in briefs/, it should not appear.
    (briefs / "some-brief.md").write_text("# brief")

    result = _list_repos(tmp_path)

    assert result == []


def test_list_repos_excludes_plans_dir(tmp_path: Path) -> None:
    """plans/ directory is never returned as a repo (AC-3)."""
    from tools.scan.store_populator import _list_repos

    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "plan.md").write_text("# plan")

    result = _list_repos(tmp_path)

    assert result == []


def test_list_repos_excludes_plain_dirs_mixed_with_repos(tmp_path: Path) -> None:
    """Plain dirs like briefs/ and plans/ are excluded even when repos are present."""
    from tools.scan.store_populator import _list_repos

    repo = tmp_path / "actual-repo"
    _make_git_dir(repo)

    (tmp_path / "briefs").mkdir()
    (tmp_path / "plans").mkdir()
    (tmp_path / "notes").mkdir()   # another plain dir

    result = _list_repos(tmp_path)

    assert result == [repo]


def test_list_repos_excludes_node_modules(tmp_path: Path) -> None:
    """node_modules is never descended into even if it contains git dirs."""
    from tools.scan.store_populator import _list_repos

    # A .git inside node_modules must not be returned.
    bad = tmp_path / "node_modules" / "some-pkg"
    bad.mkdir(parents=True)
    (bad / ".git").mkdir()

    good = tmp_path / "my-repo"
    _make_git_dir(good)

    result = _list_repos(tmp_path)

    assert result == [good]


def test_list_repos_excludes_dot_claude(tmp_path: Path) -> None:
    """.claude directories are skipped during the walk."""
    from tools.scan.store_populator import _list_repos

    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    # Even if .claude somehow has a .git, it must not appear.
    (claude_dir / ".git").mkdir()

    repo = tmp_path / "real-repo"
    _make_git_dir(repo)

    result = _list_repos(tmp_path)

    assert result == [repo]


# ---------------------------------------------------------------------------
# Root is itself a repo
# ---------------------------------------------------------------------------

def test_list_repos_root_is_repo(tmp_path: Path) -> None:
    """When root itself has .git, return [root] immediately."""
    from tools.scan.store_populator import _list_repos

    (tmp_path / ".git").mkdir()

    # Add a subdirectory repo -- should NOT appear (root short-circuits)
    sub = tmp_path / "sub-repo"
    _make_git_dir(sub)

    result = _list_repos(tmp_path)

    assert result == [tmp_path]


def test_list_repos_nonexistent_root(tmp_path: Path) -> None:
    """Non-existent root returns empty list without raising."""
    from tools.scan.store_populator import _list_repos

    result = _list_repos(tmp_path / "does-not-exist")

    assert result == []


# ---------------------------------------------------------------------------
# Depth bounding
# ---------------------------------------------------------------------------

def test_list_repos_respects_max_depth(tmp_path: Path) -> None:
    """Repos beyond max_depth are not returned."""
    from tools.scan.store_populator import _list_repos

    # Build a chain of 6 levels deep with a repo at the bottom.
    deep = tmp_path
    for part in ("a", "b", "c", "d", "e", "f"):
        deep = deep / part
    _make_git_dir(deep)

    # With max_depth=4, the 6-level-deep repo should NOT be found.
    result = _list_repos(tmp_path, max_depth=4)
    assert result == []

    # With max_depth=6, it IS found.
    result = _list_repos(tmp_path, max_depth=6)
    assert result == [deep]


def test_list_repos_default_max_depth_covers_real_cases(tmp_path: Path) -> None:
    """Default max_depth=4 covers aaxis/bildwiz/rnd/repo layout (depth 3)."""
    from tools.scan.store_populator import _list_repos

    # 3-level container: aaxis/bildwiz/rnd/repo (depth 3 from tmp_path)
    repo = tmp_path / "aaxis" / "bildwiz" / "rnd" / "my-service"
    _make_git_dir(repo)

    result = _list_repos(tmp_path)

    assert result == [repo]
