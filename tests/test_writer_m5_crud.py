"""Tests for M5 granular CRUD writers: ac, milestone, task, brief metadata, verify.

Covers T5.1, T5.2, T5.3, T5.4, T5.6 writer-layer logic. CLI integration is
exercised separately by tests/cli/test_m5_integration.py.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Shared fixture: bootstrapped v5 DB + brief + plan + AC + milestone + task
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    """Bootstrap a v5 DB and seed brief='test-brief' with one of each child."""
    bootstrap = _REPO_ROOT / "scripts" / "bootstrap_database.sh"
    db_path = tmp_path / "gaia.db"
    env = os.environ.copy()
    env["GAIA_DB"] = str(db_path)
    env["WORKSPACE"] = str(tmp_path)
    res = subprocess.run(
        ["bash", str(bootstrap)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert res.returncode == 0, (
        f"bootstrap failed:\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
    )
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO briefs (workspace, name, status) "
            "VALUES ('me', 'test-brief', 'draft')"
        )
        brief_id = con.execute(
            "SELECT id FROM briefs WHERE workspace='me' AND name='test-brief'"
        ).fetchone()["id"]
        con.execute(
            "INSERT INTO plans (brief_id, status, content) VALUES (?, 'draft', 'plan')",
            (brief_id,),
        )
        plan_id = con.execute(
            "SELECT id FROM plans WHERE brief_id=?", (brief_id,)
        ).fetchone()["id"]
        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal, status) "
            "VALUES (?, 1, 'T1', 'pending')",
            (plan_id,),
        )
        con.execute(
            "INSERT INTO acceptance_criteria (brief_id, ac_id, description, status) "
            "VALUES (?, 'AC-1', 'first', 'pending')",
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
    return db_path


# ---------------------------------------------------------------------------
# T5.1 -- AC CRUD writers
# ---------------------------------------------------------------------------

class TestAcCrud:

    def test_add_ac_success(self, seeded_db):
        from gaia.briefs.store import add_ac
        res = add_ac("me", "test-brief", "AC-2",
                     description="Second AC", db_path=seeded_db)
        assert res["action"] == "inserted"
        assert res["ac_id"] == "AC-2"

    def test_add_ac_duplicate_raises(self, seeded_db):
        from gaia.briefs.store import add_ac
        with pytest.raises(ValueError, match="already exists"):
            add_ac("me", "test-brief", "AC-1",
                   description="dup", db_path=seeded_db)

    def test_add_ac_missing_brief_raises(self, seeded_db):
        from gaia.briefs.store import add_ac
        with pytest.raises(ValueError, match="not found"):
            add_ac("me", "no-brief", "AC-9", db_path=seeded_db)

    def test_remove_ac_success(self, seeded_db):
        from gaia.briefs.store import remove_ac
        res = remove_ac("me", "test-brief", "AC-1", db_path=seeded_db)
        assert res["action"] == "deleted"

    def test_remove_ac_missing_raises(self, seeded_db):
        from gaia.briefs.store import remove_ac
        with pytest.raises(ValueError, match="not found"):
            remove_ac("me", "test-brief", "AC-99", db_path=seeded_db)

    def test_update_ac_partial(self, seeded_db):
        from gaia.briefs.store import update_ac
        res = update_ac("me", "test-brief", "AC-1",
                        description="updated text", db_path=seeded_db)
        assert res["action"] == "updated"
        assert "description" in res["fields"]

    def test_update_ac_no_fields_raises(self, seeded_db):
        from gaia.briefs.store import update_ac
        with pytest.raises(ValueError, match="at least one field"):
            update_ac("me", "test-brief", "AC-1", db_path=seeded_db)

    def test_update_ac_missing_raises(self, seeded_db):
        from gaia.briefs.store import update_ac
        with pytest.raises(ValueError, match="not found"):
            update_ac("me", "test-brief", "AC-99",
                      description="x", db_path=seeded_db)


# ---------------------------------------------------------------------------
# T5.2 -- Milestone CRUD writers
# ---------------------------------------------------------------------------

class TestMilestoneCrud:

    def test_add_milestone_success(self, seeded_db):
        from gaia.briefs.store import add_milestone
        res = add_milestone("me", "test-brief", "M2",
                            description="Phase 2", db_path=seeded_db)
        assert res["action"] == "inserted"
        assert res["name"] == "M2"
        assert res["order_num"] == 2  # auto-assigned (M1=1, M2=next)

    def test_add_milestone_explicit_order(self, seeded_db):
        from gaia.briefs.store import add_milestone
        res = add_milestone("me", "test-brief", "M3",
                            order_num=5, db_path=seeded_db)
        assert res["order_num"] == 5

    def test_add_milestone_duplicate_raises(self, seeded_db):
        from gaia.briefs.store import add_milestone
        with pytest.raises(ValueError, match="already exists"):
            add_milestone("me", "test-brief", "M1", db_path=seeded_db)

    def test_remove_milestone_success(self, seeded_db):
        from gaia.briefs.store import remove_milestone
        res = remove_milestone("me", "test-brief", "M1", db_path=seeded_db)
        assert res["action"] == "deleted"

    def test_remove_milestone_missing_raises(self, seeded_db):
        from gaia.briefs.store import remove_milestone
        with pytest.raises(ValueError, match="not found"):
            remove_milestone("me", "test-brief", "M99", db_path=seeded_db)

    def test_update_milestone_new_name(self, seeded_db):
        from gaia.briefs.store import update_milestone
        res = update_milestone("me", "test-brief", "M1",
                               new_name="Phase 1", db_path=seeded_db)
        assert res["action"] == "updated"

    def test_update_milestone_no_fields_raises(self, seeded_db):
        from gaia.briefs.store import update_milestone
        with pytest.raises(ValueError, match="at least one field"):
            update_milestone("me", "test-brief", "M1", db_path=seeded_db)

    def test_subagent_milestone_blocked(self, seeded_db, monkeypatch):
        from gaia.briefs.store import add_milestone
        from gaia.state.permissions import StateTransitionForbidden
        monkeypatch.setenv("GAIA_DISPATCH_AGENT", "developer")
        with pytest.raises(StateTransitionForbidden):
            add_milestone("me", "test-brief", "M-blocked", db_path=seeded_db)


# ---------------------------------------------------------------------------
# T5.3 -- Task CRUD writers in plan
# ---------------------------------------------------------------------------

class TestTaskCrud:

    def test_add_task_to_plan_success(self, seeded_db):
        from gaia.store.writer import add_task_to_plan
        res = add_task_to_plan("me", "test-brief", 2, "T2 goal", db_path=seeded_db)
        assert res["action"] == "inserted"
        assert res["order_num"] == 2

    def test_add_task_duplicate_order_raises(self, seeded_db):
        from gaia.store.writer import add_task_to_plan
        with pytest.raises(ValueError, match="already exists"):
            add_task_to_plan("me", "test-brief", 1, "dup", db_path=seeded_db)

    def test_add_task_empty_goal_raises(self, seeded_db):
        from gaia.store.writer import add_task_to_plan
        with pytest.raises(ValueError, match="cannot be empty"):
            add_task_to_plan("me", "test-brief", 5, "", db_path=seeded_db)

    def test_remove_task_success(self, seeded_db):
        from gaia.store.writer import remove_task_from_plan
        res = remove_task_from_plan("me", "test-brief", 1, db_path=seeded_db)
        assert res["action"] == "deleted"

    def test_remove_task_missing_raises(self, seeded_db):
        from gaia.store.writer import remove_task_from_plan
        with pytest.raises(ValueError, match="not found"):
            remove_task_from_plan("me", "test-brief", 99, db_path=seeded_db)

    def test_reorder_tasks_swap(self, seeded_db):
        from gaia.store.writer import add_task_to_plan, reorder_tasks
        add_task_to_plan("me", "test-brief", 2, "T2 goal", db_path=seeded_db)
        res = reorder_tasks("me", "test-brief", [[1, 2]], db_path=seeded_db)
        assert res["action"] == "reordered"
        # Verify the swap actually happened
        con = sqlite3.connect(str(seeded_db))
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                "SELECT order_num, goal FROM tasks ORDER BY order_num"
            ).fetchall()
        finally:
            con.close()
        # T1 is now at order_num=2, T2 at order_num=1
        assert rows[0]["order_num"] == 1
        assert rows[0]["goal"] == "T2 goal"
        assert rows[1]["order_num"] == 2
        assert rows[1]["goal"] == "T1"

    def test_reorder_missing_task_rolls_back(self, seeded_db):
        from gaia.store.writer import reorder_tasks
        with pytest.raises(ValueError):
            reorder_tasks("me", "test-brief", [[1, 99]], db_path=seeded_db)


# ---------------------------------------------------------------------------
# T5.4 -- Brief metadata patching whitelist
# ---------------------------------------------------------------------------

class TestBriefMetadataPatch:

    def test_patch_surface_type_succeeds(self, seeded_db):
        from gaia.store.writer import update_brief_field
        res = update_brief_field("me", "test-brief", "surface_type",
                                 "cli", db_path=seeded_db)
        assert res["status"] == "applied"

    def test_patch_topic_key_succeeds(self, seeded_db):
        from gaia.store.writer import update_brief_field
        res = update_brief_field("me", "test-brief", "topic_key",
                                 "state-machine", db_path=seeded_db)
        assert res["status"] == "applied"

    def test_patch_unknown_field_raises(self, seeded_db):
        from gaia.store.writer import update_brief_field
        with pytest.raises(ValueError, match="invalid brief field"):
            update_brief_field("me", "test-brief", "nonexistent_field",
                               "x", db_path=seeded_db)


# ---------------------------------------------------------------------------
# T5.6 -- verify_brief invariants
# ---------------------------------------------------------------------------

class TestVerifyBrief:

    def test_clean_brief_passes(self, seeded_db):
        from gaia.briefs.store import verify_brief
        res = verify_brief("me", "test-brief", db_path=seeded_db)
        assert res["pass"] is True
        assert res["inconsistencies"] == []

    def test_orphan_task_ac_ref_detected(self, seeded_db):
        from gaia.briefs.store import verify_brief
        # Add a task whose goal references an AC that doesn't exist
        con = sqlite3.connect(str(seeded_db))
        try:
            plan_id = con.execute(
                "SELECT id FROM plans WHERE brief_id = "
                "(SELECT id FROM briefs WHERE name='test-brief')"
            ).fetchone()[0]
            con.execute(
                "INSERT INTO tasks (plan_id, order_num, goal, status) "
                "VALUES (?, 2, 'Implement AC-99 logic', 'pending')",
                (plan_id,),
            )
            con.commit()
        finally:
            con.close()

        res = verify_brief("me", "test-brief", db_path=seeded_db)
        assert res["pass"] is False
        kinds = {i["kind"] for i in res["inconsistencies"]}
        assert "orphan_task_ac_ref" in kinds

    def test_done_ac_without_artifact_detected(self, seeded_db):
        from gaia.briefs.store import verify_brief
        con = sqlite3.connect(str(seeded_db))
        try:
            con.execute(
                "UPDATE acceptance_criteria "
                "SET status='done', evidence_type='test', artifact_path=NULL "
                "WHERE ac_id='AC-1'"
            )
            con.commit()
        finally:
            con.close()

        res = verify_brief("me", "test-brief", db_path=seeded_db)
        assert res["pass"] is False
        kinds = {i["kind"] for i in res["inconsistencies"]}
        assert "done_ac_without_artifact" in kinds

    def test_empty_plan_detected(self, seeded_db):
        from gaia.briefs.store import verify_brief
        con = sqlite3.connect(str(seeded_db))
        try:
            con.execute("DELETE FROM tasks")
            con.commit()
        finally:
            con.close()

        res = verify_brief("me", "test-brief", db_path=seeded_db)
        assert res["pass"] is False
        kinds = {i["kind"] for i in res["inconsistencies"]}
        assert "empty_plan" in kinds

    def test_active_plan_all_tasks_done_detected(self, seeded_db):
        from gaia.briefs.store import verify_brief
        con = sqlite3.connect(str(seeded_db))
        try:
            con.execute(
                "UPDATE plans SET status='active' WHERE brief_id = "
                "(SELECT id FROM briefs WHERE name='test-brief')"
            )
            con.execute("UPDATE tasks SET status='done'")
            con.commit()
        finally:
            con.close()

        res = verify_brief("me", "test-brief", db_path=seeded_db)
        assert res["pass"] is False
        kinds = {i["kind"] for i in res["inconsistencies"]}
        assert "active_plan_all_tasks_done" in kinds
