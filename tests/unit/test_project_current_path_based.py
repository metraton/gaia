"""
gaia.project.current() resolves a repository to the declared workspace whose
root contains it, by path, independent of the repository's name and remote.

Properties proven here:
  * The remote never appears in the answer and never changes it.
  * Convergence: the repo root and a nested subdirectory resolve the same.
  * Outside every declared root the answer is ``global``, never the repo name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gaia.project import current


def _init_git_repo(path: Path, remote_url: str | None = None) -> None:
    """Initialize a real git repo at ``path`` with an optional origin remote."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    if remote_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=str(path), check=True,
        )


@pytest.fixture()
def declared(tmp_path, monkeypatch):
    """A declared workspace ``acme`` rooted at ``tmp_path/acme`` in an isolated DB."""
    from gaia.store.writer import declare_workspace

    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia.db"))
    root = tmp_path / "acme"
    root.mkdir()
    declare_workspace("acme", root)
    return root


def test_a_repo_outside_every_declared_root_is_global_not_its_name(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia.db"))
    repo = tmp_path / "my-local-repo"
    _init_git_repo(repo, "git@github.com:someone/other-name.git")

    assert current(cwd=repo) == "global"


def test_remote_does_not_appear_in_the_answer(declared):
    repo = declared / "gaia"
    _init_git_repo(repo, "git@github.com:metraton/gaia.git")

    assert current(cwd=repo) == "acme"


def test_root_and_subdirectory_converge(declared):
    repo = declared / "converge-repo"
    _init_git_repo(repo, "https://github.com/o/converge-repo.git")
    subdir = repo / "src" / "deep"
    subdir.mkdir(parents=True)

    assert current(cwd=repo) == current(cwd=subdir) == "acme"


def test_answer_unchanged_when_remote_added_or_changed(declared):
    repo = declared / "stable-repo"
    _init_git_repo(repo)
    before = current(cwd=repo)

    subprocess.run(["git", "remote", "add", "origin", "git@github.com:o/a.git"],
                   cwd=str(repo), check=True)
    added = current(cwd=repo)
    subprocess.run(["git", "remote", "set-url", "origin", "git@github.com:o/b.git"],
                   cwd=str(repo), check=True)
    changed = current(cwd=repo)

    assert before == added == changed == "acme"
