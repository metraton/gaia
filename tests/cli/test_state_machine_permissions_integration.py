"""Integration tests for state-machine permission enforcement via CLI subprocess.

Mirrors the memory subagent enforcement pattern: invoke `gaia <verb>` with
`GAIA_DISPATCH_AGENT` set to a non-curator value, then assert exit code,
clean error (no traceback), and forbidden message.

Coverage:
  * Subagent developer can transition tasks/AC status (allowed)
  * Subagent developer cannot transition milestone/brief/plan status (forbidden)
  * Human caller (no env var) can transition all statuses
  * Curator (orchestrator) can transition all statuses
  * A close is separately conditioned on the task's gates, and that refusal
    reaches an operator end to end through the same subprocess seam

Permission and closure condition are two different decisions and are asserted
as such: being allowed to move a task does not make any particular close
backed, and a close being unbacked is not a permission failure.
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fresh_db_with_data(tmp_path, bootstrapped_db_template):
    """Copy the session-scoped bootstrapped v5 DB and seed it with
    brief+plan+task+AC+milestone.

    Uses ``bootstrapped_db_template`` (built once via
    ``scripts/bootstrap_database.sh``) copied per test instead of re-running
    the multi-second bootstrap subprocess. Each test still gets its own
    independent, mutable DB file -- isolation is unchanged.
    """
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "gaia.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)

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


def _approve_the_task_gate(db_path: Path) -> None:
    """Record an approving gate verdict for the seeded task, by direct INSERT.

    Direct sqlite, like ``fresh_db_with_data`` itself: what this file exercises
    is the CLI, so its setup stays outside the CLI under test. The evidence is
    needed because a close is conditioned on an approving verdict
    (``gaia.state.task_closure_condition``) and a task with zero gates carries
    none -- so a test asserting that a subagent MAY close a task has to supply
    it, or it asserts a free close instead of a permitted one.
    """
    con = sqlite3.connect(str(db_path))
    try:
        task_id = con.execute(
            "SELECT t.id FROM tasks t "
            "JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.workspace = 'me' AND b.name = 'test-brief' "
            "  AND t.order_num = 1"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO task_gates "
            "(task_id, verification_type, evidence_shape, status) "
            "VALUES (?, 'command', 'run: true | expect: exit 0', 'pass')",
            (task_id,),
        )
        con.commit()
    finally:
        con.close()


def _task_status(db_path: Path, brief: str, order_num: int) -> str:
    """Read one task's persisted status, out of band from the CLI."""
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT t.status FROM tasks t "
            "JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.workspace = 'me' AND b.name = ? AND t.order_num = ?",
            (brief, order_num),
        ).fetchone()[0]
    finally:
        con.close()


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
        # The premise under test is the permission one: a subagent MAY move a
        # task's status. Closing is separately conditioned on the task's gates,
        # so the gate verdict is recorded first and the assertion stays about
        # who is allowed -- not about whether a close needs evidence.
        db_path, workspace = fresh_db_with_data
        _approve_the_task_gate(db_path)
        res = _run_gaia(
            ["task", "set-status", "test-brief", "1", "done"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode == 0, (
            f"task set-status failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        assert _task_status(db_path, "test-brief", 1) == "done"

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
# The closure condition, observed through the real CLI as a subprocess. Mirror
# of TestSubagentAllowed.test_developer_can_set_task_status: that one shows a
# subagent closing a task whose gate approves, this one shows the same subagent
# refused when no gate does. The pair is what keeps the two decisions distinct --
# the permission layer answers WHO may move a task, the closure condition
# answers WHETHER this particular close is backed by anything. Passing through
# `python3 bin/gaia` matters: the refusal is observed the way an operator
# receives it, exit code and message included, rather than as an exception
# caught in-process.
# ---------------------------------------------------------------------------

class TestSubagentClosureCondition:

    def test_developer_cannot_close_a_task_with_no_approving_gate(
        self, fresh_db_with_data
    ):
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["task", "set-status", "test-brief", "1", "done"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode == 1, (
            f"expected exit code 1, got {res.returncode}\n"
            f"stdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        combined = res.stdout + res.stderr
        # An operator has to be able to act on the refusal, so it names both
        # ways out: record the verdict, or close it on the record.
        assert "gate" in combined.lower(), (
            f"expected the refusal to say what is missing, got:\n{combined}"
        )
        assert "--override" in combined and "--reason" in combined, (
            f"expected the refusal to name the override exit, got:\n{combined}"
        )
        assert "Traceback" not in combined, (
            f"unexpected traceback in error output:\n{combined}"
        )
        # A refusal that still wrote would be the worst of both: the message is
        # not the assertion, the unmoved row is.
        assert _task_status(db_path, "test-brief", 1) == "pending"

    def test_developer_can_close_it_by_stating_a_reason_instead(
        self, fresh_db_with_data
    ):
        # The other exit the refusal names, exercised through the same seam: no
        # gate approves, so accountability carries the close in evidence's
        # place. This is what keeps the refusal above a condition rather than a
        # dead end for a subagent.
        db_path, workspace = fresh_db_with_data
        res = _run_gaia(
            ["task", "set-status", "test-brief", "1", "done",
             "--override", "--reason", "the gate runner is offline"],
            db_path, workspace, dispatch_agent="developer",
        )
        assert res.returncode == 0, (
            f"override close failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
        assert _task_status(db_path, "test-brief", 1) == "done"


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
