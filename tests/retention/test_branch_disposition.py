"""
Composed deletion-safety verdict for a local branch.

The property under test: a branch is judged deletable only when at least one
of three independent tests proves its content survives its own deletion --
merged into remote main, reachable from any remote ref, or already present
in main's history via a squash/rebase merge. A branch that fails all three
is NEVER marked deletable, regardless of whether its upstream is still
configured -- the composed criterion deliberately never reads that signal.

Three synthetic repositories cover the three real cases the composed
criterion must tell apart:

1. A normal merge: the branch is a direct ancestor of remote main --
   deletable via test 1.
2. A squash merge: the branch's own commits never reach remote main and
   never exist on any remote ref, but their combined diff is already
   present in main under a single new commit -- deletable via test 3 alone,
   the case a reachability-only check cannot see.
3. Upstream disappeared, unique work: the branch was pushed once, then its
   remote copy was deleted (upstream gone), and its commits were never
   merged or squashed into main -- NEVER deletable. This is the exact case
   the "delete branches whose upstream is gone" recipe destroys.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _init_bare_remote(tmp_path: Path, name: str = "origin.git") -> Path:
    remote = tmp_path / name
    remote.mkdir()
    _git(remote, "init", "-q", "--bare")
    return remote


def _init_repo_with_remote(tmp_path: Path, remote: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "remote", "add", "origin", str(remote))
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    _git(repo, "push", "-q", "origin", "main")
    return repo


@pytest.fixture()
def remote(tmp_path):
    return _init_bare_remote(tmp_path)


@pytest.fixture()
def repo(tmp_path, remote):
    return _init_repo_with_remote(tmp_path, remote)


# ---------------------------------------------------------------------------
# Case 1: normal merge -- deletable via test 1, and the other two agree.
# ---------------------------------------------------------------------------

def test_normally_merged_branch_is_deletable(repo):
    from gaia.retention.branch_disposition import branch_deletion_verdict

    _git(repo, "checkout", "-q", "-b", "feature-normal-merge")
    (repo / "feature.txt").write_text("normal merge content\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "add feature")

    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "feature-normal-merge", "-m", "merge feature")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")

    verdict = branch_deletion_verdict(repo, "feature-normal-merge", remote_main="origin/main")

    assert verdict["deletable"] is True
    assert verdict["merged_into_remote_main"] is True
    assert verdict["branch"] == "feature-normal-merge"


# ---------------------------------------------------------------------------
# Case 2: squash merge -- test 1 and test 2 both fail; only test 3 catches
# it. This is the false-positive class a reachability-only check misses.
# ---------------------------------------------------------------------------

def test_squash_merged_branch_is_deletable_via_content_match(repo):
    from gaia.retention.branch_disposition import branch_deletion_verdict

    _git(repo, "checkout", "-q", "-b", "feature-squash")
    (repo / "part_one.txt").write_text("first half of the change\n", encoding="utf-8")
    _git(repo, "add", "part_one.txt")
    _git(repo, "commit", "-q", "-m", "part one")
    (repo / "part_two.txt").write_text("second half of the change\n", encoding="utf-8")
    _git(repo, "add", "part_two.txt")
    _git(repo, "commit", "-q", "-m", "part two")

    # Simulate a GitHub-style "squash and merge": main gets ONE new commit
    # carrying the combined diff. The branch's own two commits are never an
    # ancestor of main and are never pushed anywhere.
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--squash", "feature-squash")
    _git(repo, "commit", "-q", "-m", "squash-merge feature-squash")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "fetch", "-q", "origin")

    verdict = branch_deletion_verdict(repo, "feature-squash", remote_main="origin/main")

    assert verdict["merged_into_remote_main"] is False, "squash rewrites history; no direct ancestry"
    assert verdict["reachable_from_any_remote"] is False, "original commits were never pushed anywhere"
    assert verdict["content_already_in_main"] is True, "same net change is in main under a new hash"
    assert verdict["deletable"] is True


# ---------------------------------------------------------------------------
# Case 3: upstream disappeared, unique work -- the central negative. All
# three tests must independently fail, and the branch must NEVER be marked
# deletable, exactly the case the "upstream gone" recipe destroys.
# ---------------------------------------------------------------------------

def test_unique_work_with_vanished_upstream_is_never_deletable(repo, remote):
    from gaia.retention.branch_disposition import branch_deletion_verdict

    _git(repo, "checkout", "-q", "-b", "feature-orphaned")
    (repo / "irreplaceable.txt").write_text("work that exists nowhere else\n", encoding="utf-8")
    _git(repo, "add", "irreplaceable.txt")
    _git(repo, "commit", "-q", "-m", "unique work")
    _git(repo, "push", "-q", "-u", "origin", "feature-orphaned")

    # The remote deletes its copy (the PR was closed unmerged, or the
    # branch was pruned upstream) -- the classic "upstream gone" trigger.
    _git(repo, "push", "-q", "origin", "--delete", "feature-orphaned")
    _git(repo, "fetch", "-q", "--prune", "origin")

    _git(repo, "checkout", "-q", "main")

    verdict = branch_deletion_verdict(repo, "feature-orphaned", remote_main="origin/main")

    assert verdict["merged_into_remote_main"] is False
    assert verdict["reachable_from_any_remote"] is False
    assert verdict["content_already_in_main"] is False
    assert verdict["deletable"] is False, (
        "a branch with unique work and a vanished upstream must never be "
        "marked deletable -- this is the exact case the upstream-gone "
        "recipe destroys"
    )
