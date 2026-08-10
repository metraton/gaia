"""
Empirical pin for AC-7 (worktree lifecycle brief, task 12): the agentic
worktree is born OUTSIDE every repository, and its identity survives on the
git lock's *reason* rather than on its directory name or on the repo's own
tracked state.

Run against TWO synthetic repos of opposite configuration for the harness's
native worktree folder (``.claude/worktrees``):

  * ``ignoring``  -- gitignores the folder in block, mirroring Gaia's own repo
  * ``tracking``  -- commits a hundred files under it on purpose, mirroring
    the client repo whose git status this fix must never disturb

What this pins:
  (a) the created worktree's path resolves under ``gaia.paths.worktrees_dir()``,
      never under the repo's own working tree, for BOTH configurations
  (b) each repo's ``git status`` (including ignored entries) is byte-identical
      before and after -- the point that actually matters for ``tracking``,
      since a worktree born inside that repo would show up as untracked
  (c) the lock's reason, read back via ``git worktree list --porcelain``,
      parses to the exact contract_id/agent_id passed to
      ``create_agentic_worktree``
  (d) deleting the worktree's working directory does not drop the lock: the
      entry still lists as locked with its reason intact, and
      ``git worktree prune`` declines to remove it

Test isolation: real ``git init`` / ``git worktree add`` / ``git worktree
lock`` under pytest's ``tmp_path`` (``TMPDIR``-honouring, never the
noexec-mounted system ``/tmp``), with ``GAIA_DATA_DIR`` redirected so
``worktrees_dir()`` never touches the real ``~/.gaia``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from gaia.worktree import create_agentic_worktree, lock_reason, parse_lock_reason

_GIT_IDENTITY = [
    "-c", "user.email=worktree-test@example.invalid",
    "-c", "user.name=worktree-test",
]

_HARNESS_DIR = ".claude/worktrees"
_TRACKED_FILE_COUNT = 100


def _git(path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *_GIT_IDENTITY, *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path, *, track_harness_dir: bool) -> Path:
    """A real repo, one commit in, with opposite handling of ``.claude/worktrees``."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--quiet", str(path)],
        check=True, capture_output=True, text=True,
    )
    if track_harness_dir:
        harness_dir = path / _HARNESS_DIR
        harness_dir.mkdir(parents=True, exist_ok=True)
        for i in range(_TRACKED_FILE_COUNT):
            (harness_dir / f"artifact-{i:03d}.txt").write_text(f"tracked artifact {i}\n")
        _git(path, "add", ".claude")
        _git(path, "commit", "--quiet", "-m", "track the harness worktree folder on purpose")
    else:
        (path / ".gitignore").write_text(".claude/\n")
        _git(path, "add", ".gitignore")
        _git(path, "commit", "--quiet", "-m", "ignore the harness worktree folder in block")
    return path


def _status(path: Path) -> str:
    """Full working-tree status, ignored entries included, for a byte comparison."""
    return _git(path, "status", "--porcelain=v1", "--ignored").stdout


def _porcelain_entries(listing: str) -> list[dict]:
    """Parse ``git worktree list --porcelain`` into one dict per worktree."""
    entries: list[dict] = []
    current: dict = {}
    for line in listing.splitlines():
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"worktree": line[len("worktree "):]}
        elif line.startswith("locked"):
            current["locked"] = line[len("locked"):].strip()
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):]
        elif line == "" and current:
            entries.append(current)
            current = {}
    if current:
        entries.append(current)
    return entries


def _entry_for(listing: str, target: Path) -> dict:
    resolved = str(target.resolve())
    for entry in _porcelain_entries(listing):
        if entry["worktree"] == resolved:
            return entry
    raise AssertionError(f"{resolved} not found in worktree list:\n{listing}")


@pytest.fixture
def gaia_data(tmp_path, monkeypatch):
    data = tmp_path / "gaia-data"
    monkeypatch.setenv("GAIA_DATA_DIR", str(data))
    from gaia.paths import worktrees_dir
    return Path(str(worktrees_dir()))


@pytest.fixture(params=["ignoring", "tracking"])
def synthetic_repo(request, tmp_path):
    repo = tmp_path / f"repo-{request.param}"
    _init_repo(repo, track_harness_dir=(request.param == "tracking"))
    return repo, request.param


CONTRACT_ID = "a" + "1234567890abcdef" + ".deadbeef01"
AGENT_ID = "a" + "1234567890abcdef"


def test_worktree_lifecycle_across_opposite_repo_configs(gaia_data, synthetic_repo):
    repo, _kind = synthetic_repo
    before = _status(repo)

    target = create_agentic_worktree(repo, CONTRACT_ID, AGENT_ID)

    # (a) the worktree lives under the central root, never inside the repo.
    root = gaia_data.resolve()
    repo_real = repo.resolve()
    assert str(target).startswith(str(root) + "/")
    assert not str(target).startswith(str(repo_real) + "/")

    # (b) the repo's git status is untouched, byte for byte.
    after = _status(repo)
    assert after == before

    # (c) the lock's reason parses back to the exact identity passed in.
    listing = _git(repo, "worktree", "list", "--porcelain").stdout
    entry = _entry_for(listing, target)
    assert entry["locked"] == lock_reason(CONTRACT_ID, AGENT_ID)
    assert parse_lock_reason(entry["locked"]) == {
        "contract_id": CONTRACT_ID,
        "agent_id": AGENT_ID,
    }

    # (d) the lock survives the working directory disappearing, and the
    # entry is not collectible while it stands.
    shutil.rmtree(target)
    listing_after_delete = _git(repo, "worktree", "list", "--porcelain").stdout
    entry_after_delete = _entry_for(listing_after_delete, target)
    assert entry_after_delete["locked"] == lock_reason(CONTRACT_ID, AGENT_ID)

    prune_dry_run = _git(repo, "worktree", "prune", "--dry-run", "--verbose").stdout
    assert str(target.resolve()) not in prune_dry_run
