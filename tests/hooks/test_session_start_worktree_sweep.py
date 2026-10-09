#!/usr/bin/env python3
"""SessionStart's automatic worktree sweep (agent-protocol's missing trigger,
gaia.retention.worktree_collector.sweep_repo_worktrees).

Drives the REAL hooks/session_start.py the way Claude Code does -- pipe a
SessionStart event on stdin, in a subprocess, with GAIA_DATA_DIR/HOME
redirected into a sandbox -- exactly like test_session_start_db_backup_e2e.py
does for the DB-backup sweep. No manual `gaia cleanup` invocation anywhere in
this file: the sweep must fire from the hook alone.

Two worktrees, one call:
  - an ABANDONED one (owning contract carries an explicit death-proving
    cut_reason) must be gone after a single SessionStart.
  - an ALIVE one (owning contract's session_id is the SAME session_id this
    SessionStart call registers) must be untouched -- proving the ordering
    documented in worktree_collector's module docstring: register_session()
    runs before the sweep, so a worktree the current session owns reads
    ALIVE and protects itself.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[2] / "hooks" / "session_start.py"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    (repo / ".claude").mkdir()
    return repo


def _seed_rows(db: Path, rows) -> None:
    con = sqlite3.connect(str(db))
    con.execute(
        "create table if not exists agent_contract_handoffs "
        "(id integer primary key, contract_id text, session_id text, "
        " agent_state text, cut_reason text)"
    )
    con.executemany(
        "insert into agent_contract_handoffs "
        "(contract_id, session_id, agent_state, cut_reason) values (?, ?, ?, ?)",
        list(rows),
    )
    con.commit()
    con.close()


def _run_session_start(cwd: Path, env: dict, sid: str) -> subprocess.CompletedProcess:
    payload = json.dumps(
        {"hook_event_name": "SessionStart", "session_id": sid, "matcher": "startup"}
    )
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=30,
    )


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data_dir = tmp_path / "gaia-data"
    data_dir.mkdir()
    plugin_data = tmp_path / "plugin-data"
    plugin_data.mkdir()

    # In-process env, so creating the fixture worktrees below (this test's
    # own process) targets the sandbox, not the real ~/.gaia.
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

    repo = _init_repo(tmp_path)

    env = os.environ.copy()
    env["GAIA_DATA_DIR"] = str(data_dir)
    env["HOME"] = str(tmp_path)
    env["CLAUDE_PLUGIN_DATA"] = str(plugin_data)
    return repo, data_dir, env


def test_abandoned_worktree_collected_no_manual_invocation(sandbox):
    """AC 2: an abandoned worktree is gone after one real SessionStart --
    nothing in this test calls `gaia cleanup` or the collector directly."""
    repo, data_dir, env = sandbox

    from gaia.worktree import create_agentic_worktree

    wt_abandoned = create_agentic_worktree(
        repo, "abandoned-1", "agent-abandoned", branch="wt-abandoned-1"
    )
    assert wt_abandoned.exists()

    _seed_rows(data_dir / "gaia.db", [
        ("abandoned-1", "sess-abandoned", "IN_PROGRESS", "reaped"),
    ])

    result = _run_session_start(repo, env, "sess-live-unrelated")
    assert result.returncode == 0, f"hook failed: {result.stderr[-800:]!r}"

    assert not wt_abandoned.exists(), (
        "the abandoned worktree must be collected by the SessionStart hook "
        "alone, with no manual invocation"
    )


def test_live_session_worktree_never_touched(sandbox):
    """AC 3 -- the half that matters: a worktree owned by THIS session's own
    contract must survive the same automatic sweep, because register_session()
    (run earlier in this same hook) already marks it ALIVE."""
    repo, data_dir, env = sandbox

    from gaia.worktree import create_agentic_worktree

    wt_alive = create_agentic_worktree(
        repo, "alive-1", "agent-alive", branch="wt-alive-1"
    )
    assert wt_alive.exists()

    _seed_rows(data_dir / "gaia.db", [
        ("alive-1", "sess-alive-1", "DISPATCHED", "never_finalized"),
    ])

    # The SAME session_id as the owning contract's session_id -- this exact
    # SessionStart call is what freshens that session's heartbeat.
    result = _run_session_start(repo, env, "sess-alive-1")
    assert result.returncode == 0, f"hook failed: {result.stderr[-800:]!r}"

    assert wt_alive.exists(), (
        "a worktree whose owning session is alive right now must never be "
        "touched by the automatic sweep"
    )


def _declare_workspace(data_dir: Path, root: Path, repos, name: str = "ws") -> None:
    con = sqlite3.connect(str(data_dir / "gaia.db"))
    con.execute("create table if not exists workspaces (name text primary key, root_path text)")
    con.execute(
        "create table if not exists projects (workspace text, name text, path text, status text)"
    )
    con.execute("insert into workspaces values (?, ?)", (name, str(root)))
    con.executemany(
        "insert into projects values (?, ?, ?, 'active')",
        [(name, repo.name, str(repo)) for repo in repos],
    )
    con.commit()
    con.close()


def _repo_with_leftovers(root: Path, name: str, idle_branches: int) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "initial")
    for index in range(idle_branches):
        _git(repo, "branch", f"old-{index}")
    _git(repo, "worktree", "add", "-q", str(root / f"{name}-wt"), "-b", f"live-{name}")
    return repo


def test_workspace_notice_counts_the_leftovers_of_every_repo(sandbox, tmp_path):
    """The notice reaches repos other than the cwd one, and deletes nothing."""
    _, data_dir, _ = sandbox
    root = tmp_path / "workspace"
    first = _repo_with_leftovers(root, "first", idle_branches=2)
    second = _repo_with_leftovers(root, "second", idle_branches=3)
    _declare_workspace(data_dir, root, [first, second])

    from gaia.retention.workspace_leftovers import build_notice

    notice = build_notice(root)

    assert "first: 1 worktrees, 2 branches" in notice
    assert "second: 1 worktrees, 3 branches" in notice
    assert "gaia worktree prune-branches" in notice
    assert "\n" not in notice
    for repo, idle in ((first, 2), (second, 3)):
        assert (root / f"{repo.name}-wt").exists()
        assert len(_git(repo, "for-each-ref", "refs/heads").splitlines()) == idle + 2


def test_workspace_notice_stays_short_with_many_repos(sandbox, tmp_path):
    _, data_dir, _ = sandbox
    root = tmp_path / "workspace"
    repos = [_repo_with_leftovers(root, f"repo{n}", idle_branches=n) for n in range(7)]
    _declare_workspace(data_dir, root, repos)

    from gaia.retention.workspace_leftovers import build_notice

    notice = build_notice(root)

    assert "+2 more repos" in notice
    assert len(notice) < 600


def test_workspace_notice_from_a_directory_holding_declared_roots(sandbox, tmp_path):
    """Opened above several declared workspaces, the session sees all their repos."""
    _, data_dir, _ = sandbox
    parent = tmp_path / "parent"
    left = _repo_with_leftovers(parent / "left-ws", "left-repo", idle_branches=2)
    right = _repo_with_leftovers(parent / "right-ws", "right-repo", idle_branches=4)
    _declare_workspace(data_dir, parent / "left-ws", [left], name="left")
    _declare_workspace(data_dir, parent / "right-ws", [right], name="right")

    from gaia.retention.workspace_leftovers import build_notice

    notice = build_notice(parent)

    assert "left-repo: 1 worktrees, 2 branches" in notice
    assert "right-repo: 1 worktrees, 4 branches" in notice
    assert build_notice(tmp_path / "elsewhere") == ""


def test_workspace_notice_is_empty_outside_a_declared_workspace(sandbox, tmp_path):
    _, data_dir, _ = sandbox
    declared = tmp_path / "workspace"
    repo = _repo_with_leftovers(declared, "only", idle_branches=1)
    _declare_workspace(data_dir, declared, [repo])

    from gaia.retention.workspace_leftovers import build_notice

    assert build_notice(tmp_path / "elsewhere") == ""


@pytest.fixture
def declared(sandbox, tmp_path, monkeypatch):
    """A declared workspace with one leftover-bearing repo; refreshes are recorded, not run."""
    _, data_dir, _ = sandbox
    root = tmp_path / "workspace"
    repo = _repo_with_leftovers(root, "only", idle_branches=2)
    _declare_workspace(data_dir, root, [repo])
    spawned = []
    monkeypatch.setattr(
        "gaia.retention.workspace_leftovers._spawn_refresh", lambda start: spawned.append(start)
    )
    return root, spawned


def test_session_start_shows_nothing_and_starts_a_refresh_when_nothing_is_stored(declared):
    from gaia.retention.workspace_leftovers import leftovers_notice

    root, spawned = declared

    assert leftovers_notice(root) == ""
    assert spawned == [root.resolve()]


def test_a_refresh_in_flight_is_not_started_twice(declared):
    from gaia.retention.workspace_leftovers import leftovers_notice

    root, spawned = declared

    leftovers_notice(root)
    leftovers_notice(root)

    assert len(spawned) == 1


def test_refresh_stores_the_counts_and_the_next_start_shows_them_without_counting(declared, monkeypatch):
    from gaia.retention import workspace_leftovers

    root, spawned = declared
    workspace_leftovers.refresh(root)
    monkeypatch.setattr(
        workspace_leftovers, "build_notice", lambda start: pytest.fail("session start must not count")
    )

    notice = workspace_leftovers.leftovers_notice(root)

    assert "only: 1 worktrees, 2 branches" in notice
    assert spawned == []


def test_a_stored_notice_past_its_age_is_shown_and_refreshed(declared, monkeypatch):
    from gaia.retention import workspace_leftovers

    root, spawned = declared
    workspace_leftovers.refresh(root)
    later = workspace_leftovers.time.time() + workspace_leftovers.REFRESH_AFTER_SECONDS + 1
    monkeypatch.setattr(workspace_leftovers.time, "time", lambda: later)

    notice = workspace_leftovers.leftovers_notice(root)

    assert "only: 1 worktrees, 2 branches" in notice
    assert spawned == [root.resolve()]


if __name__ == "__main__":
    import unittest
    unittest.main()
