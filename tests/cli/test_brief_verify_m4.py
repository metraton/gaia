"""Tests for M4 verify_brief invariants 5 and 6.

Coverage (T4.3):
  * Invariant 5: closed plan without a COMPLETE handoff -> inconsistency
  * Invariant 5: closed plan WITH a COMPLETE handoff -> no inconsistency
  * Invariant 6: most recent handoff is not COMPLETE -> stalled_handoff
  * Invariant 6: most recent handoff IS COMPLETE -> no inconsistency
  * Invariants 1-4 still pass (regression guard)
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
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
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return db_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_brief_and_plan(db_path: Path, brief_name: str, plan_status: str = "closed"):
    """Seed workspace + brief + plan. Returns (brief_id, plan_id)."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO briefs (workspace, name, status) VALUES ('me', ?, 'draft')",
            (brief_name,),
        )
        brief_id = con.execute(
            "SELECT id FROM briefs WHERE workspace='me' AND name=?", (brief_name,)
        ).fetchone()["id"]
        con.execute(
            "INSERT INTO plans (brief_id, status, content) VALUES (?, ?, 'plan content')",
            (brief_id, plan_status),
        )
        plan_id = con.execute(
            "SELECT id FROM plans WHERE brief_id=?", (brief_id,)
        ).fetchone()["id"]
        con.commit()
        return brief_id, plan_id
    finally:
        con.close()


def _insert_handoff(db_path: Path, brief_id: int, task_status: str,
                    agent_id: str = "test-agent", created_at: str | None = None) -> int:
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        if created_at is not None:
            con.execute(
                "INSERT INTO agent_contract_handoffs "
                "  (agent_id, workspace, brief_id, task_status, raw_handoff_json, created_at) "
                "VALUES (?, 'me', ?, ?, '{}', ?)",
                (agent_id, brief_id, task_status, created_at),
            )
        else:
            con.execute(
                "INSERT INTO agent_contract_handoffs "
                "  (agent_id, workspace, brief_id, task_status, raw_handoff_json) "
                "VALUES (?, 'me', ?, ?, '{}')",
                (agent_id, brief_id, task_status),
            )
        row_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        con.commit()
        return row_id
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Invariant 5
# ---------------------------------------------------------------------------

class TestInvariant5ClosedPlanWithoutCompletionHandoff:
    def test_closed_plan_no_handoff_triggers_inconsistency(self, tmp_db):
        from gaia.briefs.store import verify_brief
        brief_id, _ = _seed_brief_and_plan(tmp_db, "inv5-no-handoff", plan_status="closed")
        # No handoffs inserted
        result = verify_brief("me", "inv5-no-handoff", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "closed_plan_without_completion_handoff" in kinds
        assert result["pass"] is False

    def test_closed_plan_with_complete_handoff_passes(self, tmp_db):
        from gaia.briefs.store import verify_brief
        brief_id, _ = _seed_brief_and_plan(tmp_db, "inv5-with-handoff", plan_status="closed")
        _insert_handoff(tmp_db, brief_id, task_status="COMPLETE")
        result = verify_brief("me", "inv5-with-handoff", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "closed_plan_without_completion_handoff" not in kinds


# ---------------------------------------------------------------------------
# Invariant 6
# ---------------------------------------------------------------------------

class TestInvariant6StalledHandoff:
    def test_most_recent_handoff_not_complete_triggers_stalled(self, tmp_db):
        from gaia.briefs.store import verify_brief
        brief_id, _ = _seed_brief_and_plan(tmp_db, "inv6-stalled", plan_status="active")
        # Use explicit timestamps so ORDER BY created_at DESC picks the later one reliably.
        _insert_handoff(tmp_db, brief_id, task_status="COMPLETE",
                        created_at="2026-01-01T10:00:00Z")
        _insert_handoff(tmp_db, brief_id, task_status="IN_PROGRESS",
                        created_at="2026-01-01T11:00:00Z")
        result = verify_brief("me", "inv6-stalled", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "stalled_handoff" in kinds

    def test_most_recent_handoff_complete_no_stalled(self, tmp_db):
        from gaia.briefs.store import verify_brief
        brief_id, _ = _seed_brief_and_plan(tmp_db, "inv6-complete", plan_status="closed")
        # Most recent is COMPLETE -> no stalled_handoff
        _insert_handoff(tmp_db, brief_id, task_status="IN_PROGRESS",
                        created_at="2026-01-01T10:00:00Z")
        _insert_handoff(tmp_db, brief_id, task_status="COMPLETE",
                        created_at="2026-01-01T11:00:00Z")
        result = verify_brief("me", "inv6-complete", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "stalled_handoff" not in kinds


# ---------------------------------------------------------------------------
# Regression: invariants 1-4 still work
# ---------------------------------------------------------------------------

class TestInvariants1To4Regression:
    def test_empty_plan_detected(self, tmp_db):
        """Invariant 1: plan with zero tasks."""
        from gaia.briefs.store import verify_brief
        _seed_brief_and_plan(tmp_db, "inv1-empty-plan", plan_status="draft")
        result = verify_brief("me", "inv1-empty-plan", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "empty_plan" in kinds

    def test_brief_with_no_plan_no_empty_plan_inconsistency(self, tmp_db):
        """No plan at all means Invariant 1 does not fire (nothing to check)."""
        import sqlite3
        con = sqlite3.connect(str(tmp_db))
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO briefs (workspace, name, status) VALUES ('me', 'no-plan-brief', 'draft')"
        )
        con.commit()
        con.close()
        from gaia.briefs.store import verify_brief
        result = verify_brief("me", "no-plan-brief", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "empty_plan" not in kinds
