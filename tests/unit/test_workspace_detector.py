"""
test_workspace_detector.py -- Bloque 3 verification.

Tests detect_workspace_type() classification across the four observable
cases:

  1. Root has .git                                -> single-repo
  2. Root has no .git, >= 2 children with .git    -> multi-repo-workspace
  3. Root has no .git, 1 child with .git          -> single-repo (deferred to git scanner)
  4. Root has no .git, 0 children with .git       -> organizational-workspace

The organizational case is the new one introduced in Bloque 3: a container
directory like `aaxis/` holding non-git children should register a workspace
row but expose zero projects (repo_dirs=[]).
"""

from __future__ import annotations

from pathlib import Path

from tools.scan.workspace import WorkspaceInfo, detect_workspace_type


def _make_git(dir_: Path) -> None:
    """Materialise the minimum filesystem layout that detect_workspace_type
    inspects -- a `.git/` directory under `dir_`."""
    (dir_ / ".git").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Case 1: root has .git
# ---------------------------------------------------------------------------

def test_detect_single_repo_when_root_has_git(tmp_path: Path) -> None:
    _make_git(tmp_path)
    info = detect_workspace_type(tmp_path)
    assert info.workspace_type == "single-repo"
    assert info.repo_dirs == []
    assert info.is_multi_repo is False
    assert info.is_organizational is False


# ---------------------------------------------------------------------------
# Case 2: root has no .git, >= 2 children with .git
# ---------------------------------------------------------------------------

def test_detect_multi_repo_workspace_when_two_git_descendants(tmp_path: Path) -> None:
    a = tmp_path / "repo-a"
    b = tmp_path / "repo-b"
    a.mkdir(); b.mkdir()
    _make_git(a); _make_git(b)

    info = detect_workspace_type(tmp_path)
    assert info.workspace_type == "multi-repo-workspace"
    assert set(info.repo_dirs) == {a, b}
    assert info.is_multi_repo is True
    assert info.is_organizational is False


# ---------------------------------------------------------------------------
# Case 3: root has no .git, 1 child with .git (qxo / qxo-monorepo case)
# ---------------------------------------------------------------------------

def test_detect_single_repo_with_single_git_descendant(tmp_path: Path) -> None:
    """`qxo/` containing exactly `qxo-monorepo/.git` falls back to single-repo;
    the git scanner's _find_git_in_subdirs picks the nested repo."""
    monorepo = tmp_path / "qxo-monorepo"
    monorepo.mkdir()
    _make_git(monorepo)

    info = detect_workspace_type(tmp_path)
    assert info.workspace_type == "single-repo"
    assert info.repo_dirs == []
    assert info.is_multi_repo is False
    assert info.is_organizational is False


# ---------------------------------------------------------------------------
# Case 4 (NEW in Bloque 3): root has no .git, 0 children with .git
# ---------------------------------------------------------------------------

def test_detect_organizational_workspace_when_no_git_descendants(tmp_path: Path) -> None:
    """`aaxis/` with N children, none of them git-bearing, must classify as
    organizational-workspace and expose an empty repo_dirs list. The previous
    behaviour returned single-repo with no repos, which propagated as 'fake'
    project rows downstream."""
    for name in ("docs", "notes", "scratch", "ideas"):
        (tmp_path / name).mkdir()

    info = detect_workspace_type(tmp_path)
    assert info.workspace_type == "organizational-workspace"
    assert info.repo_dirs == []
    assert info.is_multi_repo is False
    assert info.is_organizational is True


def test_detect_organizational_workspace_when_empty_directory(tmp_path: Path) -> None:
    """An empty directory still classifies as organizational; the scanner row
    is created but no project rows follow."""
    info = detect_workspace_type(tmp_path)
    assert info.workspace_type == "organizational-workspace"
    assert info.repo_dirs == []


# ---------------------------------------------------------------------------
# Filtering: dotted / skip dirs are ignored when counting git descendants
# ---------------------------------------------------------------------------

def test_skip_dirs_do_not_count_as_git_descendants(tmp_path: Path) -> None:
    """A `.cache/.git` or `node_modules/.git` must not turn an organizational
    workspace into a multi-repo workspace."""
    hidden = tmp_path / ".cache"
    nm = tmp_path / "node_modules"
    hidden.mkdir(); nm.mkdir()
    _make_git(hidden); _make_git(nm)

    info = detect_workspace_type(tmp_path)
    # No countable git descendants -> organizational
    assert info.workspace_type == "organizational-workspace"
    assert info.repo_dirs == []


# ---------------------------------------------------------------------------
# Return type contract
# ---------------------------------------------------------------------------

def test_workspace_info_is_frozen_dataclass(tmp_path: Path) -> None:
    info = detect_workspace_type(tmp_path)
    assert isinstance(info, WorkspaceInfo)
