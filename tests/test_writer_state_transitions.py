"""Tests for set_task_status, set_ac_status, set_milestone_status writer functions.

Coverage:
  * Normal transition: pending -> done
  * Noop (same status): returns action='noop', no DB write
  * Illegal transition: raises ValueError
  * Missing entity: raises ValueError
  * Cross-brief isolation: status change on one brief does not affect another
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Bootstrap a minimal v5 DB in tmp_path."""
    import subprocess
    import os

    bootstrap = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
    db_path = tmp_path / "gaia.db"

    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    result = subprocess.run(
        ["bash", str(bootstrap)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"bootstrap failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    return db_path


def _seed_brief_with_plan_task_ac_ms(db_path: Path, brief_name: str = "test-brief"):
    """Seed a brief + plan + task + AC + milestone directly into the DB."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        # Workspace
        con.execute(
            "INSERT OR IGNORE INTO workspaces (name) VALUES ('me')"
        )
        # Brief
        con.execute(
            "INSERT INTO briefs (workspace, name, status) VALUES ('me', ?, 'draft')",
            (brief_name,),
        )
        brief_id = con.execute(
            "SELECT id FROM briefs WHERE workspace='me' AND name=?",
            (brief_name,),
        ).fetchone()["id"]
        # Plan
        con.execute(
            "INSERT INTO plans (brief_id, status, content) VALUES (?, 'draft', 'plan body')",
            (brief_id,),
        )
        plan_id = con.execute(
            "SELECT id FROM plans WHERE brief_id=?",
            (brief_id,),
        ).fetchone()["id"]
        # Task
        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal, status) VALUES (?, 1, 'T1 goal', 'pending')",
            (plan_id,),
        )
        # AC
        con.execute(
            "INSERT INTO acceptance_criteria "
            "(brief_id, ac_id, description, status) "
            "VALUES (?, 'AC-1', 'First AC', 'pending')",
            (brief_id,),
        )
        # Milestone
        con.execute(
            "INSERT INTO milestones (brief_id, order_num, name, status) VALUES (?, 1, 'M1', 'pending')",
            (brief_id,),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# set_task_status
# ---------------------------------------------------------------------------

class TestSetTaskStatus:

    def test_normal_transition_pending_to_done(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_task_status
        result = set_task_status("me", "test-brief", 1, "done", db_path=tmp_db)
        assert result["action"] == "updated"
        assert result["old_status"] == "pending"
        assert result["new_status"] == "done"
        assert result["entity_id"] == 1
        assert result["brief_name"] == "test-brief"

    def test_noop_same_status(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_task_status
        result = set_task_status("me", "test-brief", 1, "pending", db_path=tmp_db)
        assert result["action"] == "noop"
        assert result["old_status"] == "pending"
        assert result["new_status"] == "pending"

    def test_illegal_transition_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_task_status
        # Mark done, then try to transition done -> skipped (illegal; done can only go to pending)
        set_task_status("me", "test-brief", 1, "done", db_path=tmp_db)
        with pytest.raises(ValueError, match="illegal task lifecycle transition"):
            set_task_status("me", "test-brief", 1, "skipped", db_path=tmp_db)

    def test_missing_brief_raises(self, tmp_db):
        from gaia.store.writer import set_task_status
        with pytest.raises(ValueError, match="not found in workspace"):
            set_task_status("me", "no-such-brief", 1, "done", db_path=tmp_db)

    def test_missing_task_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_task_status
        with pytest.raises(ValueError, match="not found in plan"):
            set_task_status("me", "test-brief", 99, "done", db_path=tmp_db)

    def test_invalid_status_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_task_status
        with pytest.raises(ValueError, match="invalid task status"):
            set_task_status("me", "test-brief", 1, "flying", db_path=tmp_db)


# ---------------------------------------------------------------------------
# set_ac_status
# ---------------------------------------------------------------------------

class TestSetAcStatus:

    def test_normal_transition_pending_to_done(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_ac_status
        result = set_ac_status("me", "test-brief", "AC-1", "done", db_path=tmp_db)
        assert result["action"] == "updated"
        assert result["old_status"] == "pending"
        assert result["new_status"] == "done"
        assert result["entity_id"] == "AC-1"

    def test_transition_to_blocked(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_ac_status
        result = set_ac_status("me", "test-brief", "AC-1", "blocked", db_path=tmp_db)
        assert result["action"] == "updated"
        assert result["new_status"] == "blocked"

    def test_noop_same_status(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_ac_status
        result = set_ac_status("me", "test-brief", "AC-1", "pending", db_path=tmp_db)
        assert result["action"] == "noop"

    def test_illegal_transition_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_ac_status
        # done -> blocked is illegal (done can only go back to pending)
        set_ac_status("me", "test-brief", "AC-1", "done", db_path=tmp_db)
        with pytest.raises(ValueError, match="illegal AC lifecycle transition"):
            set_ac_status("me", "test-brief", "AC-1", "blocked", db_path=tmp_db)

    def test_missing_ac_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_ac_status
        with pytest.raises(ValueError, match="not found in brief"):
            set_ac_status("me", "test-brief", "AC-999", "done", db_path=tmp_db)

    def test_cross_brief_isolation(self, tmp_db):
        """Changing AC status on brief-A does not affect brief-B's ACs."""
        _seed_brief_with_plan_task_ac_ms(tmp_db, brief_name="brief-a")
        _seed_brief_with_plan_task_ac_ms(tmp_db, brief_name="brief-b")
        from gaia.store.writer import set_ac_status
        import sqlite3
        set_ac_status("me", "brief-a", "AC-1", "done", db_path=tmp_db)
        con = sqlite3.connect(str(tmp_db))
        con.row_factory = sqlite3.Row
        try:
            brief_b_id = con.execute(
                "SELECT id FROM briefs WHERE workspace='me' AND name='brief-b'"
            ).fetchone()["id"]
            row = con.execute(
                "SELECT status FROM acceptance_criteria WHERE brief_id=? AND ac_id='AC-1'",
                (brief_b_id,),
            ).fetchone()
        finally:
            con.close()
        assert row["status"] == "pending", (
            f"brief-b AC status should remain 'pending', got {row['status']!r}"
        )


# ---------------------------------------------------------------------------
# set_milestone_status
# ---------------------------------------------------------------------------

class TestSetMilestoneStatus:

    def test_normal_transition_pending_to_done(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_milestone_status
        result = set_milestone_status("me", "test-brief", "M1", "done", db_path=tmp_db)
        assert result["action"] == "updated"
        assert result["old_status"] == "pending"
        assert result["new_status"] == "done"
        assert result["entity_id"] == "M1"

    def test_transition_to_blocked(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_milestone_status
        result = set_milestone_status("me", "test-brief", "M1", "blocked", db_path=tmp_db)
        assert result["new_status"] == "blocked"

    def test_reopen_done_to_pending(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_milestone_status
        set_milestone_status("me", "test-brief", "M1", "done", db_path=tmp_db)
        result = set_milestone_status("me", "test-brief", "M1", "pending", db_path=tmp_db)
        assert result["action"] == "updated"
        assert result["new_status"] == "pending"

    def test_noop_same_status(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_milestone_status
        result = set_milestone_status("me", "test-brief", "M1", "pending", db_path=tmp_db)
        assert result["action"] == "noop"

    def test_illegal_transition_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_milestone_status
        # done -> blocked is illegal
        set_milestone_status("me", "test-brief", "M1", "done", db_path=tmp_db)
        with pytest.raises(ValueError, match="illegal milestone lifecycle transition"):
            set_milestone_status("me", "test-brief", "M1", "blocked", db_path=tmp_db)

    def test_missing_milestone_raises(self, tmp_db):
        _seed_brief_with_plan_task_ac_ms(tmp_db)
        from gaia.store.writer import set_milestone_status
        with pytest.raises(ValueError, match="not found in brief"):
            set_milestone_status("me", "test-brief", "M99", "done", db_path=tmp_db)
