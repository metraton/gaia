"""Tests for M4 state-machine invariants in gaia.store.writer.

Coverage (T4.1 + T4.2):
  * upsert_plan rejects briefs with status=closed or archived
  * upsert_plan accepts briefs with status=draft, open, in-progress
  * set_plan_status to 'closed' emits warnings for unsatisfied ACs
  * set_plan_status to 'closed' returns empty warnings when all ACs are done
  * set_plan_status for non-close transitions returns empty warnings
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch, bootstrapped_db_template):
    """Full-schema DB in tmp_path (copied from the session template).

    Uses the session-scoped ``bootstrapped_db_template`` and copies it per test
    instead of re-running ``scripts/bootstrap_database.sh`` each time. Each test
    still gets its own independent, mutable DB file -- isolation is unchanged.
    """
    from tests.conftest import copy_bootstrapped_db

    db_path = tmp_path / "gaia.db"
    copy_bootstrapped_db(bootstrapped_db_template, db_path)
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    # Unset GAIA_DISPATCH_AGENT so set_plan_status allows human-CLI path.
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    return db_path


def _seed_brief(db_path: Path, brief_name: str, status: str = "draft") -> int:
    """Seed a workspace + brief row directly. Returns brief_id."""
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute("INSERT OR IGNORE INTO workspaces (name) VALUES ('me')")
        con.execute(
            "INSERT INTO briefs (workspace, name, status) VALUES ('me', ?, ?)",
            (brief_name, status),
        )
        brief_id = con.execute(
            "SELECT id FROM briefs WHERE workspace='me' AND name=?",
            (brief_name,),
        ).fetchone()["id"]
        con.commit()
        return brief_id
    finally:
        con.close()


def _seed_ac(db_path: Path, brief_id: int, ac_id: str, status: str = "pending") -> None:
    import sqlite3
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute(
            "INSERT INTO acceptance_criteria (brief_id, ac_id, description, status) "
            "VALUES (?, ?, 'Test AC', ?)",
            (brief_id, ac_id, status),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# T4.1: upsert_plan brief-status guard
# ---------------------------------------------------------------------------

class TestUpsertPlanBriefStatusGuard:
    def test_upsert_plan_rejects_closed_brief(self, tmp_db):
        from gaia.store.writer import upsert_plan
        _seed_brief(tmp_db, "brief-closed", status="closed")
        with pytest.raises(ValueError, match="closed"):
            upsert_plan("me", "brief-closed", content="plan", db_path=tmp_db)

    def test_upsert_plan_rejects_archived_brief(self, tmp_db):
        from gaia.store.writer import upsert_plan
        _seed_brief(tmp_db, "brief-archived", status="archived")
        with pytest.raises(ValueError, match="archived"):
            upsert_plan("me", "brief-archived", content="plan", db_path=tmp_db)

    def test_upsert_plan_accepts_draft_brief(self, tmp_db):
        from gaia.store.writer import upsert_plan
        _seed_brief(tmp_db, "brief-draft", status="draft")
        result = upsert_plan("me", "brief-draft", content="plan body", db_path=tmp_db)
        assert result["action"] in ("inserted", "updated")

    def test_upsert_plan_accepts_open_brief(self, tmp_db):
        from gaia.store.writer import upsert_plan
        _seed_brief(tmp_db, "brief-open", status="open")
        result = upsert_plan("me", "brief-open", content="plan body", db_path=tmp_db)
        assert result["action"] in ("inserted", "updated")

    def test_upsert_plan_accepts_in_progress_brief(self, tmp_db):
        from gaia.store.writer import upsert_plan
        _seed_brief(tmp_db, "brief-inprogress", status="in-progress")
        result = upsert_plan("me", "brief-inprogress", content="plan body", db_path=tmp_db)
        assert result["action"] in ("inserted", "updated")


# ---------------------------------------------------------------------------
# T4.2: set_plan_status warnings on close
# ---------------------------------------------------------------------------

class TestSetPlanStatusWarnings:
    def _create_brief_with_plan(self, db_path: Path, brief_name: str) -> int:
        """Seed brief + draft plan, return brief_id."""
        from gaia.store.writer import upsert_plan
        brief_id = _seed_brief(db_path, brief_name, status="draft")
        upsert_plan("me", brief_name, content="plan content", status="draft", db_path=db_path)
        return brief_id

    def test_close_emits_warnings_for_unsatisfied_acs(self, tmp_db):
        from gaia.store.writer import upsert_plan, set_plan_status
        brief_id = self._create_brief_with_plan(tmp_db, "brief-warn")
        # 3 ACs: 2 done, 1 pending
        _seed_ac(tmp_db, brief_id, "AC-1", status="done")
        _seed_ac(tmp_db, brief_id, "AC-2", status="done")
        _seed_ac(tmp_db, brief_id, "AC-3", status="pending")

        # Advance plan to active first, then close
        upsert_plan("me", "brief-warn", status="active", db_path=tmp_db)
        result = set_plan_status("me", "brief-warn", "closed", db_path=tmp_db)

        assert result["action"] == "updated"
        assert result["new_status"] == "closed"
        assert len(result["warnings"]) == 1
        assert "AC-3" in result["warnings"][0]

    def test_close_no_warnings_when_all_acs_done(self, tmp_db):
        from gaia.store.writer import upsert_plan, set_plan_status
        brief_id = self._create_brief_with_plan(tmp_db, "brief-clean")
        _seed_ac(tmp_db, brief_id, "AC-1", status="done")
        _seed_ac(tmp_db, brief_id, "AC-2", status="done")

        upsert_plan("me", "brief-clean", status="active", db_path=tmp_db)
        result = set_plan_status("me", "brief-clean", "closed", db_path=tmp_db)

        assert result["warnings"] == []

    def test_non_close_transition_returns_empty_warnings(self, tmp_db):
        from gaia.store.writer import set_plan_status
        _seed_brief(tmp_db, "brief-active", status="draft")
        from gaia.store.writer import upsert_plan
        upsert_plan("me", "brief-active", content="c", status="draft", db_path=tmp_db)

        result = set_plan_status("me", "brief-active", "active", db_path=tmp_db)
        assert result["warnings"] == []
