"""
Integrating a task releases its worktree and deletes its branch; after the
PR merges, the branches it integrated are removed too.

The property under test: a branch is disposed of without a signature exactly
when every commit that exists only on it has a verbatim patch-id twin in
another ref (a cherry-pick integration) or is reachable from the default
branch. A branch with any commit whose content is nowhere else, or with
uncommitted work, is kept or captured as before.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _commit_file(cwd: Path, name: str, content: str, message: str) -> str:
    (cwd / name).write_text(content, encoding="utf-8")
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", message)
    return _git(cwd, "rev-parse", "HEAD").strip()


def _branches(repo: Path) -> list:
    return _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "gaia.worktree.workspace_worktrees_root", lambda _repo: tmp_path / ".project-worktrees"
    )
    remote = tmp_path / "origin.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare")
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "test")
    _git(path, "remote", "add", "origin", str(remote))
    _commit_file(path, "README.md", "hello\n", "initial")
    _git(path, "push", "-q", "origin", "main")
    return path


def _task_worktree(repo: Path, branch: str) -> Path:
    from gaia.worktree import create_agentic_worktree

    return create_agentic_worktree(
        repo, f"a{branch}deadbeefdeadbeef.cafef00d", f"a{branch}deadbeefdeadbeef",
        branch=branch,
    )


def _cherry_pick_onto_accumulating_branch(repo: Path, commits: list) -> None:
    """Integrate *commits* the way the orchestrator does: onto a local branch
    that already moved on, so the picks get new hashes."""
    _git(repo, "checkout", "-q", "-b", "accumulating")
    _commit_file(repo, "sibling.txt", "a sibling task landed first\n", "sibling task")
    for sha in commits:
        _git(repo, "cherry-pick", sha)
    _git(repo, "checkout", "-q", "main")


def test_cherry_picked_task_branch_releases_and_its_branch_is_deleted(repo):
    from gaia.retention.worktree_reclaim import reclaim_worktree

    worktree = _task_worktree(repo, "task-a")
    sha = _commit_file(worktree, "feature.txt", "feature\n", "task work")
    _cherry_pick_onto_accumulating_branch(repo, [sha])
    assert sha not in _git(repo, "rev-list", "accumulating")

    result = reclaim_worktree(repo, worktree)

    assert result["status"] == "recycled"
    assert result["branch_deleted"] is True
    assert not worktree.exists()
    assert "task-a" not in _branches(repo)
    assert "accumulating" in _branches(repo)


def test_branch_with_a_commit_not_upstream_by_content_is_kept(repo):
    from gaia.retention.worktree_reclaim import reclaim_worktree

    worktree = _task_worktree(repo, "task-b")
    first = _commit_file(worktree, "one.txt", "one\n", "integrated commit")
    _commit_file(worktree, "two.txt", "two\n", "never integrated")
    _cherry_pick_onto_accumulating_branch(repo, [first])

    result = reclaim_worktree(repo, worktree)

    assert result["status"] == "capture_args_missing"
    assert worktree.exists()
    assert "task-b" in _branches(repo)


def test_whitespace_only_difference_is_not_integrated(repo):
    from gaia.retention.worktree_reclaim import reclaim_worktree

    worktree = _task_worktree(repo, "task-c")
    _commit_file(worktree, "spaced.txt", "a  b\n", "double space")
    _git(repo, "checkout", "-q", "-b", "accumulating")
    _commit_file(repo, "spaced.txt", "a b\n", "single space")
    _git(repo, "checkout", "-q", "main")

    result = reclaim_worktree(repo, worktree)

    assert result["status"] == "capture_args_missing"
    assert "task-c" in _branches(repo)


def test_integrated_commits_with_uncommitted_work_still_capture(repo):
    from gaia.retention.worktree_reclaim import reclaim_worktree

    worktree = _task_worktree(repo, "task-d")
    sha = _commit_file(worktree, "feature.txt", "feature\n", "task work")
    _cherry_pick_onto_accumulating_branch(repo, [sha])
    (worktree / "scratch.txt").write_text("not committed\n", encoding="utf-8")

    result = reclaim_worktree(repo, worktree)

    assert result["status"] == "capture_args_missing"
    assert worktree.exists()
    assert "task-d" in _branches(repo)


def test_post_merge_pass_removes_merged_branches_and_keeps_unmerged_ones(repo):
    from gaia.retention.worktree_reclaim import remove_integrated_branches

    _git(repo, "checkout", "-q", "-b", "merged-task")
    _commit_file(repo, "merged.txt", "merged\n", "merged work")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--ff-only", "merged-task")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "-b", "unmerged-task")
    _commit_file(repo, "unmerged.txt", "only here\n", "unmerged work")
    _git(repo, "checkout", "-q", "main")

    remove_integrated_branches(repo)

    assert sorted(_branches(repo)) == ["main", "unmerged-task"]


def test_verdict_reports_content_integration_in_a_local_branch(repo):
    from gaia.retention.branch_disposition import branch_deletion_verdict

    _git(repo, "checkout", "-q", "-b", "task-e")
    sha = _commit_file(repo, "feature.txt", "feature\n", "task work")
    _git(repo, "checkout", "-q", "main")
    _cherry_pick_onto_accumulating_branch(repo, [sha])

    verdict = branch_deletion_verdict(repo, "task-e")

    assert verdict["deletable"] is True
    assert verdict["content_integrated_in_other_refs"] is True
    assert verdict["merged_into_remote_main"] is False
