"""
M2-T7 (AC-9): gaia.project.current() resolves workspace/project identity by
PATH, not git-remote-first, and is independent of remote state.

AC-9 properties proven here:
  * Path-based resolution: identity comes from the repository's location on
    disk (the repo-root basename), not from the git remote URL.
  * Convergence: two different working-directory paths of the SAME repo (the
    root and a nested subdirectory) resolve to the SAME identity -- no
    divergence.
  * Remote-independent: adding, changing, or removing the git remote does NOT
    change the resolved identity for a given path.

These complement (do not replace) tests/project/test_current.py, which owns
the fallback-ladder and normalization cases.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from gaia.project import current


def _init_git_repo(path: Path, remote_url: str | None = None) -> None:
    """Initialize a real git repo at ``path`` with an optional origin remote."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    if remote_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=str(path), check=True,
        )


# ---------------------------------------------------------------------------
# Path-based: the repo-root basename decides, not the remote
# ---------------------------------------------------------------------------

def test_identity_is_repo_root_basename_not_remote(tmp_path):
    """A repo whose remote points at a DIFFERENT name resolves to the repo-root
    path basename -- the remote does NOT decide the identity (AC-9)."""
    repo = tmp_path / "my-local-repo"
    repo.mkdir()
    # Remote deliberately names something other than the directory.
    _init_git_repo(repo, "git@github.com:someorg/totally-different-name.git")

    # Path-based: the directory basename wins, not "github.com/someorg/...".
    assert current(cwd=repo) == "my-local-repo"


def test_remote_does_not_appear_in_identity(tmp_path):
    """The resolved identity must not be the normalized remote form."""
    repo = tmp_path / "gaia"
    repo.mkdir()
    _init_git_repo(repo, "git@github.com:metraton/Gaia.git")

    result = current(cwd=repo)
    assert result == "gaia"
    assert "github.com" not in result
    assert "/" not in result  # a path-based basename, not host/owner/repo


# ---------------------------------------------------------------------------
# Convergence: two paths of the same repo -> same identity
# ---------------------------------------------------------------------------

def test_root_and_subdirectory_converge(tmp_path):
    """The repo root and a nested subdirectory of the SAME repo resolve to the
    same identity -- two vantage paths never diverge (AC-9)."""
    repo = tmp_path / "converge-repo"
    repo.mkdir()
    _init_git_repo(repo, "https://github.com/metraton/converge-repo.git")
    subdir = repo / "pkg" / "deep" / "nested"
    subdir.mkdir(parents=True)

    id_root = current(cwd=repo)
    id_sub = current(cwd=subdir)

    assert id_root == id_sub == "converge-repo", (id_root, id_sub)


def test_convergence_holds_without_remote(tmp_path):
    """Convergence is a property of the PATH, so it holds even with no remote:
    root and subdir of a remote-less repo still resolve identically."""
    repo = tmp_path / "no-remote-repo"
    repo.mkdir()
    _init_git_repo(repo)  # no remote
    subdir = repo / "src"
    subdir.mkdir()

    assert current(cwd=repo) == current(cwd=subdir) == "no-remote-repo"


# ---------------------------------------------------------------------------
# Remote-independent: identity is stable regardless of remote state
# ---------------------------------------------------------------------------

def test_identity_unchanged_when_remote_added(tmp_path):
    """The same repo path resolves the same way before and after a remote is
    configured -- remote state does not change the identity (AC-9)."""
    repo = tmp_path / "stable-repo"
    repo.mkdir()
    _init_git_repo(repo)  # no remote yet

    before = current(cwd=repo)

    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:x/y.git"],
        cwd=str(repo), check=True,
    )
    after = current(cwd=repo)

    assert before == after == "stable-repo", (before, after)


def test_identity_unchanged_when_remote_changed(tmp_path):
    """Changing the remote URL does not change the path-based identity."""
    repo = tmp_path / "rewire-repo"
    repo.mkdir()
    _init_git_repo(repo, "git@github.com:one/first.git")
    first = current(cwd=repo)

    subprocess.run(
        ["git", "remote", "set-url", "origin", "git@github.com:two/second.git"],
        cwd=str(repo), check=True,
    )
    second = current(cwd=repo)

    assert first == second == "rewire-repo", (first, second)


def test_lowercased_repo_root(tmp_path):
    """The repo-root basename is lowercased, consistent with the model."""
    repo = tmp_path / "MixedCaseRepo"
    repo.mkdir()
    _init_git_repo(repo, "git@github.com:org/MixedCaseRepo.git")

    assert current(cwd=repo) == "mixedcaserepo"
