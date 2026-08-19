"""Real-git gate for the workspace-owned canonical worktree identity."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.worktree import (
    WorktreePathError,
    create_canonical_worktree,
    read_worktree_metadata,
    workspace_worktrees_root,
)


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "canonical-worktree-test")
    (path / "README.md").write_text("main\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def test_canonical_identity_is_contained_round_trips_and_leaves_main_unchanged(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo = _repo(workspace / "project-checkout")
    before = _git(repo, "status", "--porcelain=v1", "--ignored")
    commit = _git(repo, "rev-parse", "HEAD")

    metadata = create_canonical_worktree(
        repo, workspace, "project-checkout", "contract-123", "agent-456", branch="task-branch"
    )
    root = workspace_worktrees_root(workspace, "project-checkout").resolve()
    target = Path(metadata.path)

    assert target.parent == root
    assert root == workspace / ".project-worktrees" / "project-checkout"
    assert _git(repo, "status", "--porcelain=v1", "--ignored") == before
    assert metadata.commit == commit
    assert read_worktree_metadata(target) == metadata
    assert json.loads((target / ".gaia-worktree.json").read_text()) == metadata.as_dict()


@pytest.mark.parametrize("project", ["..", ".", "../escape", "/tmp/escape", "a/b", "a\\b"])
def test_path_components_fail_closed(tmp_path, project):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(WorktreePathError):
        workspace_worktrees_root(workspace, project)


def test_symlinked_project_root_cannot_escape_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    managed = workspace / ".project-worktrees"
    managed.mkdir()
    (managed / "project").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorktreePathError):
        workspace_worktrees_root(workspace, "project")


def test_legacy_lock_identity_is_read_without_migration(tmp_path, monkeypatch):
    from gaia.worktree import create_agentic_worktree

    repo = _repo(tmp_path / "legacy-repo")
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    target = create_agentic_worktree(repo, "legacy-contract", "legacy-agent")

    metadata = read_worktree_metadata(target)
    assert metadata is not None
    assert metadata.contract_id == "legacy-contract"
    assert metadata.agent_id == "legacy-agent"
    assert metadata.lifecycle == "legacy"
    assert not (target / ".gaia-worktree.json").exists()
