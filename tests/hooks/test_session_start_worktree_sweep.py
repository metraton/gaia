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


if __name__ == "__main__":
    import unittest
    unittest.main()
