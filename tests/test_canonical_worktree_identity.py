"""Real-git gate for the central-root canonical worktree identity."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gaia.worktree import (
    WorktreePathError,
    create_canonical_worktree,
    read_worktree_metadata,
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


def test_canonical_identity_is_contained_round_trips_and_leaves_main_unchanged(
    tmp_path, monkeypatch
):
    from gaia.paths import worktrees_dir

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    repo = _repo(tmp_path / "project-checkout")
    before = _git(repo, "status", "--porcelain=v1", "--ignored")
    commit = _git(repo, "rev-parse", "HEAD")

    metadata = create_canonical_worktree(
        repo, "project-checkout", "contract-123", "agent-456", branch="task-branch"
    )
    root = Path(str(worktrees_dir())).resolve()
    target = Path(metadata.path)

    assert target.parent == root
    assert _git(repo, "status", "--porcelain=v1", "--ignored") == before
    assert metadata.commit == commit
    assert read_worktree_metadata(target) == metadata
    assert json.loads((target / ".gaia-worktree.json").read_text()) == metadata.as_dict()


def test_canonical_root_must_be_outside_the_checkout(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia-data"
    data_dir.mkdir()
    repo = _repo(data_dir / "worktrees")
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

    with pytest.raises(WorktreePathError):
        create_canonical_worktree(repo, "project", "contract-x", "agent-x")


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
