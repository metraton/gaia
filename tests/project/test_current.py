"""Tests for gaia.project.current() -- workspace identity resolution."""

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
    ("git@github.com:Metraton/Gaia-Ops-Dev.git", "github.com/metraton/gaia-ops-dev"),
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
# current() -- with git remote (level 1)
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path, remote_url: str | None = None) -> None:
    """Initialize a git repo at `path` with optional origin remote."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(path), check=True)
    if remote_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url],
            cwd=str(path), check=True,
        )


def test_current_with_ssh_remote(tmp_path):
    _init_git_repo(tmp_path, "git@github.com:Metraton/Gaia-Ops-Dev.git")
    assert current(cwd=tmp_path) == "github.com/metraton/gaia-ops-dev"


def test_current_with_https_remote(tmp_path):
    _init_git_repo(tmp_path, "https://github.com/Metraton/Gaia.git")
    assert current(cwd=tmp_path) == "github.com/metraton/gaia"


def test_current_with_bitbucket_remote(tmp_path):
    _init_git_repo(tmp_path, "https://bitbucket.org/aaxisdigital/bildwiz.git")
    assert current(cwd=tmp_path) == "bitbucket.org/aaxisdigital/bildwiz"


# ---------------------------------------------------------------------------
# current() -- directory-name fallback (level 2)
# ---------------------------------------------------------------------------

def test_current_no_git_remote_falls_back_to_dirname(tmp_path):
    """Repo with no `origin` remote falls back to lowercase dirname."""
    target = tmp_path / "MyProject"
    target.mkdir()
    _init_git_repo(target)  # no remote
    assert current(cwd=target) == "myproject"


def test_current_not_a_git_repo_falls_back_to_dirname(tmp_path):
    """Non-git directory falls back to lowercase dirname."""
    target = tmp_path / "SomeDir"
    target.mkdir()
    assert current(cwd=target) == "somedir"


def test_current_dirname_is_lowercased(tmp_path):
    """Mixed-case dirname must be lowercased."""
    target = tmp_path / "MixedCaseDir"
    target.mkdir()
    assert current(cwd=target) == "mixedcasedir"


# ---------------------------------------------------------------------------
# current() -- global fallback (level 3)
# ---------------------------------------------------------------------------

def test_current_global_fallback_when_dirname_empty(monkeypatch):
    """When cwd resolves to '/' (no name component), return 'global'."""
    # Path("/") has empty name on POSIX
    assert current(cwd=Path("/")) == "global"


def test_current_default_cwd(tmp_path, monkeypatch):
    """current() with no arg uses Path.cwd()."""
    monkeypatch.chdir(tmp_path)
    target_name = tmp_path.name.lower()
    # No git in tmp_path -> dirname fallback
    assert current() == target_name


# ---------------------------------------------------------------------------
# current() -- never raises
# ---------------------------------------------------------------------------

def test_current_returns_string_for_nonexistent_path():
    """Non-existent path must not raise; returns either dirname or 'global'."""
    result = current(cwd=Path("/nonexistent/path/to/nowhere"))
    assert isinstance(result, str)
    assert result  # non-empty


def test_current_handles_subprocess_failure(tmp_path, monkeypatch):
    """If git subprocess fails or times out, fall back gracefully."""
    target = tmp_path / "fallback-test"
    target.mkdir()

    # Force git to be 'unavailable' by patching shutil.which
    import gaia.project as proj_mod
    monkeypatch.setattr(proj_mod, "shutil", type("M", (), {"which": staticmethod(lambda _: None)})())

    assert current(cwd=target) == "fallback-test"
