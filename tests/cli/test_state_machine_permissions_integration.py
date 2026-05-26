"""Integration tests for state-machine permission enforcement via CLI subprocess.

Mirrors the memory subagent enforcement pattern: invoke `gaia <verb>` with
`GAIA_DISPATCH_AGENT` set to a non-curator value, then assert exit code,
clean error (no traceback), and forbidden message.

Coverage:
  * Subagent developer can transition tasks/AC status (allowed)
  * Subagent developer cannot transition milestone/brief/plan status (forbidden)
  * Human caller (no env var) can transition all statuses
  * Curator (orchestrator) can transition all statuses
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_GAIA_BIN = _REPO_ROOT / "bin" / "gaia"
_BOOTSTRAP_SH = _REPO_ROOT / "scripts" / "bootstrap_database.sh"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_db_with_data(tmp_path):
    """Bootstrap a fresh v5 DB and seed it with brief+plan+task+AC+milestone."""
    db_path = tmp_path / "gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    res = subprocess.run(
        ["bash", str(_BOOTSTRAP_SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode == 0, (
        f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )

    # Seed the DB directly with sqlite3 (a brief, plan, task, AC, milestone)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO briefs (workspace, name, status) VALUES ('me', 'test-brief', 'draft')"
        )
        brief_id = con.execute(
            "SELECT id FROM briefs WHERE workspace='me' AND name='test-brief'"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO plans (brief_id, status, content) VALUES (?, 'draft', 'plan body')",
            (brief_id,),
        )
        plan_id = con.execute(
            "SELECT id FROM plans WHERE brief_id=?", (brief_id,)
        ).fetchone()[0]
        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal, status) "
            "VALUES (?, 1, 'T1', 'pending')",
            (plan_id,),
        )
        con.execute(
            "INSERT INTO acceptance_criteria (brief_id, ac_id, description, status) "
            "VALUES (?, 'AC-1', 'desc', 'pending')",
            (brief_id,),
        )
        con.execute(
            "INSERT INTO milestones (brief_id, order_num, name, status) "
            "VALUES (?, 1, 'M1', 'pending')",
            (brief_id,),
        )
        con.commit()
    finally:
        con.close()

    return db_path, tmp_path


def _run_gaia(args: list[str], db_path: Path, workspace: Path,
              dispatch_agent: str | None = None) -> subprocess.CompletedProcess:
    """Invoke `python3 bin/gaia <args>` with custom env."""
    env = os.environ.copy()
    env["GAIA_DATA_DIR"] = str(db_path.parent)
    # Ensure project resolution lands on 'me'
    env.pop("GAIA_DISPATCH_AGENT", None)
    if dispatch_agent is not None:
        env["GAIA_DISPATCH_AGENT"] = dispatch_agent
    return subprocess.run(
        [sys.executable, str(_GAIA_BIN), *args, "--workspace", "me"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        cwd=str(workspace),
    )


# ---------------------------------------------------------------------------
# Subagent (developer) -- allowed on tasks and AC
# ---------------------------------------------------------------------------

class TestSubagentAllowed:

    def test_developer_can_set_task_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["task", "set-status", "test-brief", "1", "done"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode == 0, (
            f"task set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )

    def test_developer_can_set_ac_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["ac", "set-status", "test-brief", "AC-1", "done"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode == 0, (
            f"ac set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )


# ---------------------------------------------------------------------------
# Subagent (developer) -- blocked on milestones / briefs / plans
# ---------------------------------------------------------------------------

class TestSubagentBlocked:

    def test_developer_cannot_set_milestone_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["milestone", "set-status", "test-brief", "M1", "done"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode == 1, (
            f"expected exit code 1, got {res.returncode}\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        # Combined output for matching (stderr is primary)
        combined = res.stdout + res.stderr
        assert "forbidden" in combined.lower(), (
            f"expected 'forbidden' in output, got:\n{combined}"
        )
        # No raw traceback
        assert "Traceback" not in combined, (
            f"unexpected traceback in error output:\n{combined}"
        )

    def test_developer_cannot_set_brief_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["brief", "set-status", "test-brief", "open"],
            db_path, workspace, dispatch_agent="developer",
        )
        # brief set-status is in bin/cli/brief.py -- it should now propagate
        # the StateTransitionForbidden as a ValueError-like error
        assert res.returncode != 0, (
            f"expected non-zero exit, got 0\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        combined = res.stdout + res.stderr
        assert ("forbidden" in combined.lower()
                or "restricted to curator" in combined.lower()), (
            f"expected forbidden message, got:\n{combined}"
        )

    def test_developer_cannot_set_plan_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["plan", "set-status", "test-brief", "active"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode != 0, (
            f"expected non-zero exit, got 0\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        combined = res.stdout + res.stderr
        assert ("forbidden" in combined.lower()
                or "restricted to curator" in combined.lower()), (
            f"expected forbidden message, got:\n{combined}"
        )


# ---------------------------------------------------------------------------
# Human caller -- allowed on all tables
# ---------------------------------------------------------------------------

class TestHumanCaller:

    def test_human_can_set_milestone_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["milestone", "set-status", "test-brief", "M1", "done"],
            db_path, workspace, dispatch_agent=None,
        )
        assert res.returncode == 0, (
            f"milestone set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )

    def test_human_can_set_brief_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["brief", "set-status", "test-brief", "open"],
            db_path, workspace, dispatch_agent=None,
        )
        assert res.returncode == 0, (
            f"brief set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )


# ---------------------------------------------------------------------------
# Curator (orchestrator) -- allowed on all tables
# ---------------------------------------------------------------------------

class TestCurator:

    def test_orchestrator_can_set_milestone_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["milestone", "set-status", "test-brief", "M1", "done"],
            db_path, workspace, dispatch_agent="orchestrator",
        )
        assert res.returncode == 0, (
            f"milestone set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )

    def test_gaia_orchestrator_can_set_plan_status(self, fresh_db_with_data):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["plan", "set-status", "test-brief", "active"],
            db_path, workspace, dispatch_agent="gaia-orchestrator",
        )
        assert res.returncode == 0, (
            f"plan set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
