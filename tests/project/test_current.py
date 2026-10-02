"""Tests for gaia.project.current() -- the declared workspace containing a directory."""

import subprocess
from pathlib import Path

import pytest

from gaia.project import _normalize_remote, current


# ---------------------------------------------------------------------------
# _normalize_remote -- pure normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    # SSH form, mixed case, with .git suffix
    ("git@github.com:metraton/Gaia.git", "github.com/metraton/gaia"),
    # SSH form, alternate org
    ("git@github.com:Metraton/Gaia-Dev.git", "github.com/metraton/gaia-dev"),
    # HTTPS form, mixed case, with .git suffix
    ("https://github.com/Metraton/Gaia.git", "github.com/metraton/gaia"),
    # HTTPS form, alternate host
    ("https://bitbucket.org/aaxisdigital/bildwiz.git", "bitbucket.org/aaxisdigital/bildwiz"),
    # Already canonical
    ("github.com/metraton/gaia", "github.com/metraton/gaia"),
    # HTTP (not HTTPS)
    ("http://gitlab.example.com/team/proj.git", "gitlab.example.com/team/proj"),
    # No .git suffix
    ("https://github.com/Metraton/Gaia", "github.com/metraton/gaia"),
    # Trailing slash
    ("https://github.com/metraton/gaia/", "github.com/metraton/gaia"),
])
def test_normalize_remote_canonical_forms(url, expected):
    assert _normalize_remote(url) == expected


def test_normalize_remote_empty_input():
    assert _normalize_remote("") == ""
    assert _normalize_remote("   ") == ""


# ---------------------------------------------------------------------------
# current() -- the declared root nearest above the directory, else global
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path, remote_url: str | None = None) -> None:
    """Initialize a git repo at `path` with optional origin remote."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    if remote_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=str(path), check=True,
        )


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia.db"))
    return tmp_path / "gaia.db"


@pytest.mark.parametrize("remote", [
    "git@github.com:Metraton/Gaia-Dev.git",
    "https://bitbucket.org/aaxisdigital/bildwiz.git",
    None,
])
def test_a_repo_outside_every_declared_root_is_global(isolated_db, tmp_path, remote):
    repo = tmp_path / "Gaia-Dev"
    _init_git_repo(repo, remote)

    assert current(cwd=repo) == "global"


def test_a_plain_folder_outside_every_declared_root_is_global(isolated_db, tmp_path):
    target = tmp_path / "SomeDir"
    target.mkdir()

    assert current(cwd=target) == "global"


def test_a_repo_inside_a_declared_root_resolves_to_its_name(isolated_db, tmp_path):
    from gaia.store.writer import declare_workspace

    root = tmp_path / "ws"
    repo = root / "group" / "bildwiz"
    _init_git_repo(repo, "https://bitbucket.org/aaxisdigital/bildwiz.git")
    declare_workspace("Clients", root)

    assert current(cwd=repo) == "Clients"


def test_current_global_fallback_for_the_filesystem_root(isolated_db):
    assert current(cwd=Path("/")) == "global"


def test_current_default_cwd(isolated_db, tmp_path, monkeypatch):
    """current() with no arg uses Path.cwd()."""
    from gaia.store.writer import declare_workspace

    declare_workspace("acme", tmp_path)
    monkeypatch.chdir(tmp_path)

    assert current() == "acme"


def test_current_returns_global_for_nonexistent_path(isolated_db):
    """Non-existent path must not raise."""
    assert current(cwd=Path("/nonexistent/path/to/nowhere")) == "global"
