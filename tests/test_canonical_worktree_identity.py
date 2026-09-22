"""Real-git gate for canonical worktree location, base, identity and managed roots."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.worktree import (
    WorktreePathError,
    create_canonical_worktree,
    is_valid_own_metadata_sidecar,
    legacy_metadata_path,
    managed_root_containing,
    read_worktree_metadata,
    workspace_worktrees_root,
    worktree_metadata_path,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(path: Path, name: str) -> str:
    (path / f"{name}.txt").write_text(f"{name}\n", encoding="utf-8")
    _git(path, "add", f"{name}.txt")
    _git(path, "commit", "-q", "-m", name)
    return _git(path, "rev-parse", "HEAD")


def _init(path: Path, branch: str = "trunk") -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", branch)
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "canonical-worktree-test")
    return path


def _repo(path: Path) -> Path:
    repo = _init(path)
    _commit(repo, "initial")
    return repo


def _register(workspace: str, root: Path | None, *repos: Path) -> None:
    """Record a workspace root and its projects the way a scan leaves them."""
    from gaia.store.writer import _connect

    con = _connect()
    try:
        con.execute(
            "INSERT INTO workspaces (name, root_path) VALUES (?, ?)",
            (workspace, None if root is None else str(root)),
        )
        for repo in repos:
            con.execute(
                "INSERT INTO projects (workspace, name, path) VALUES (?, ?, ?)",
                (workspace, repo.name, str(repo)),
            )
        con.commit()
    finally:
        con.close()


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("GAIA_DB", raising=False)


@pytest.fixture
def workspace(tmp_path):
    """A registered workspace that is itself a git repo, holding one project repo."""
    ws = _init(tmp_path / "ws")
    _commit(ws, "workspace-file")
    repo = _repo(ws / "proj")
    _register("ws", ws, repo)
    return ws, repo


def test_worktree_is_born_under_workspace_project_root_invisible_to_both_repos(workspace):
    ws, repo = workspace
    ws_before = _git(ws, "status", "--porcelain=v1", "--untracked-files=all")
    repo_before = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")

    metadata = create_canonical_worktree(
        repo, "proj", "contract-123", "agent-456", branch="task-branch", base="HEAD"
    )
    target = Path(metadata.path)

    assert target.parent == (ws / ".project-worktrees" / "proj").resolve()
    assert _git(ws, "status", "--porcelain=v1", "--untracked-files=all") == ws_before
    assert _git(repo, "status", "--porcelain=v1", "--untracked-files=all") == repo_before
    assert metadata.commit == _git(repo, "rev-parse", "HEAD")
    assert read_worktree_metadata(target) == metadata
    assert managed_root_containing(target) == (ws / ".project-worktrees").resolve()


def test_registered_repo_nested_in_subfolders_uses_the_recorded_root(tmp_path):
    root = tmp_path / "company"
    repo = _repo(root / "team" / "service" / "api")
    _register("company", root, repo)

    assert workspace_worktrees_root(repo) == root.resolve() / ".project-worktrees"


def test_workspace_name_need_not_match_its_folder(tmp_path):
    root = tmp_path / "acme-folder"
    repo = _repo(root / "billing")
    _register("clients", root, repo)

    assert workspace_worktrees_root(repo) == root.resolve() / ".project-worktrees"


def test_homonymous_folders_resolve_to_the_recorded_one_not_the_nearest(tmp_path):
    outer = tmp_path / "ws"
    repo = _repo(outer / "team" / "ws" / "proj")
    _register("ws", outer, repo)

    assert workspace_worktrees_root(repo) == outer.resolve() / ".project-worktrees"


def test_unregistered_repo_fails_closed_and_creates_nothing(tmp_path):
    other = _repo(tmp_path / "github-repos" / "tools" / "registered")
    _register("github-repos", tmp_path / "github-repos", other)
    chisle = _repo(tmp_path / "github-repos" / "harness" / "Chisle")

    with pytest.raises(WorktreePathError, match="not a project of any registered workspace.*gaia scan"):
        create_canonical_worktree(chisle, "Chisle", "c-x", "a-x", base="HEAD")
    assert not (chisle / ".project-worktrees").exists()
    assert not (tmp_path / "github-repos" / ".project-worktrees").exists()
    assert _git(chisle, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_workspace_without_recorded_root_fails_closed(tmp_path):
    repo = _repo(tmp_path / "ws" / "proj")
    _register("ws", None, repo)

    with pytest.raises(WorktreePathError, match="no recorded root"):
        workspace_worktrees_root(repo)


def test_scan_records_the_root_that_resolution_uses(tmp_path):
    from gaia.paths import db_path
    from tools.scan.classify import scan

    root = tmp_path / "Org Folder"
    repo = _repo(root / "group" / "svc")

    report = scan(root, "Org Folder", db_path=db_path())

    assert report.resolved_workspace == "Org Folder"
    assert workspace_worktrees_root(repo) == root.resolve() / ".project-worktrees"


def test_sidecar_lives_in_git_dir_and_no_add_can_stage_it(workspace):
    _ws, repo = workspace
    metadata = create_canonical_worktree(repo, "proj", "c-sidecar", "a-sidecar", base="HEAD")
    target = Path(metadata.path)

    sidecar = worktree_metadata_path(target)
    assert json.loads(sidecar.read_text()) == metadata.as_dict()
    assert target not in sidecar.parents
    _git(target, "add", "-A", "--force")
    assert _git(target, "diff", "--cached", "--name-only") == ""


def test_default_base_is_the_fetched_remote_default_branch_not_head(tmp_path):
    upstream = _init(tmp_path / "upstream", branch="trunk")
    _commit(upstream, "first")
    repo = tmp_path / "ws" / "proj"
    repo.parent.mkdir()
    subprocess.run(["git", "clone", "-q", str(upstream), str(repo)], check=True)
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "canonical-worktree-test")
    _git(repo, "checkout", "-q", "-b", "stale-local")
    local_head = _commit(repo, "local-only")
    remote_tip = _commit(upstream, "landed-after-clone")
    _register("ws", tmp_path / "ws", repo)

    metadata = create_canonical_worktree(repo, "proj", "c-base", "a-base", branch="task")

    assert metadata.commit == remote_tip != local_head
    assert _git(repo, "rev-parse", "origin/trunk") == remote_tip


def test_explicit_base_is_honoured(workspace):
    _ws, repo = workspace
    first = _git(repo, "rev-parse", "HEAD")
    _commit(repo, "second")

    metadata = create_canonical_worktree(repo, "proj", "c-explicit", "a-explicit", base=first)

    assert metadata.commit == first


def test_no_remote_and_no_base_fails_closed_without_a_worktree(workspace):
    _ws, repo = workspace
    with pytest.raises(WorktreePathError):
        create_canonical_worktree(repo, "proj", "c-noremote", "a-noremote")
    assert _git(repo, "worktree", "list", "--porcelain").count("worktree ") == 1


def test_project_must_be_a_single_path_segment(workspace):
    _ws, repo = workspace
    with pytest.raises(WorktreePathError):
        create_canonical_worktree(repo, "../escape", "c-x", "a-x", base="HEAD")


def test_managed_roots_do_not_cover_lookalikes_or_the_root_itself(workspace, tmp_path):
    ws, _repo_path = workspace
    lookalike = _repo(tmp_path / "elsewhere" / ".project-worktrees" / "proj" / "wt")

    assert managed_root_containing(lookalike) is None
    assert managed_root_containing(ws / ".project-worktrees") is None


def test_legacy_in_tree_sidecar_is_still_read_and_exempt(workspace):
    _ws, repo = workspace
    metadata = create_canonical_worktree(repo, "proj", "c-legacy", "a-legacy", base="HEAD")
    target = Path(metadata.path)
    worktree_metadata_path(target).rename(legacy_metadata_path(target))

    assert read_worktree_metadata(target) == metadata
    assert is_valid_own_metadata_sidecar(target) is True


def test_legacy_lock_identity_is_read_without_migration(tmp_path):
    from gaia.worktree import create_agentic_worktree

    repo = _repo(tmp_path / "legacy-repo")
    target = create_agentic_worktree(repo, "legacy-contract", "legacy-agent")

    metadata = read_worktree_metadata(target)
    assert metadata is not None
    assert metadata.contract_id == "legacy-contract"
    assert metadata.agent_id == "legacy-agent"
    assert metadata.lifecycle == "legacy"
    assert managed_root_containing(target) == (tmp_path / "gaia-data" / "worktrees").resolve()
