"""
Isolation property pin for task 17 (AC-12, part b): when two background
agents are dispatched at the same time over the same repo, they must land in
distinct working copies, and a commit made in one must never surface on the
other's branch.

Brief: lo-que-gaia-crea-gaia-lo-limpia-evidencia-copiada-scratch-ensenado-
retencion-por-estado.

This does not spin up two live Claude Code background sessions -- that
mechanism is the harness's own (`worktree.bgIsolation`, gated behind a
`.claude/settings.local.json` value this agent cannot write; see this task's
config finding). What IS this agent's own mechanism, and what the harness's
`bgIsolation="worktree"` setting relies on under the hood, is
`gaia.worktree.create_agentic_worktree`: a real `git worktree add` per
dispatch. This test exercises that real mechanism twice against one shared
repo -- the same git primitive either dispatch path ultimately uses -- and
proves the isolation property git worktrees are supposed to give: separate
working directories, separate branches, and no cross-contamination of
commits between them.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.worktree import create_agentic_worktree  # noqa: E402


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_two_concurrent_dispatches_get_distinct_copies_and_commits_do_not_cross(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    repo = _init_repo(tmp_path)

    # Two "background agents dispatched at the same time" -- each gets its
    # own isolated worktree off the same repo, exactly as bgIsolation would
    # hand out one per background session.
    wt_a = create_agentic_worktree(repo, "contract-a", "agent-a", branch="agent-a-branch")
    wt_b = create_agentic_worktree(repo, "contract-b", "agent-b", branch="agent-b-branch")

    # (1) Distinct working copies on disk.
    assert wt_a != wt_b
    assert wt_a.exists() and wt_b.exists()

    # (2) Each is checked out on its own branch, not sharing HEAD.
    branch_a = _git(wt_a, "rev-parse", "--abbrev-ref", "HEAD")
    branch_b = _git(wt_b, "rev-parse", "--abbrev-ref", "HEAD")
    assert branch_a == "agent-a-branch"
    assert branch_b == "agent-b-branch"
    assert branch_a != branch_b

    # Agent A does real work: a new file, committed on its own branch.
    (wt_a / "agent-a-work.txt").write_text("work only agent A did\n", encoding="utf-8")
    _git(wt_a, "add", "agent-a-work.txt")
    _git(wt_a, "commit", "-q", "-m", "agent A's commit")
    commit_a = _git(wt_a, "rev-parse", "HEAD")

    # (3) Agent B's working copy never sees A's file -- separate working trees.
    assert not (wt_b / "agent-a-work.txt").exists()

    # (4) Agent A's commit is not reachable from B's branch: it never
    # "appears on the other's branch," which is the literal property AC-12
    # names. merge-base --is-ancestor exits 1 (non-zero) when NOT an
    # ancestor -- checked directly rather than through _git's check=True.
    result = subprocess.run(
        ["git", "-C", str(wt_b), "merge-base", "--is-ancestor", commit_a, "HEAD"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        "agent A's commit is reachable from agent B's branch -- the two "
        "dispatches were not actually isolated"
    )

    # (5) B's own branch log never names A's commit.
    log_b = _git(wt_b, "log", "--oneline", "HEAD")
    assert commit_a[:7] not in log_b
