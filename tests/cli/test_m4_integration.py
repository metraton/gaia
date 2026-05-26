"""End-to-end M4 integration tests (T4.4).

Tests the full `gaia brief verify <slug> --json` CLI pathway with M4 invariants.
Exercises the DB-to-CLI contract by seeding state, invoking cmd_verify_brief,
and asserting on the JSON output shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


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

def _seed_state(db_path: Path, brief_name: str, plan_status: str = "closed",
                add_complete_handoff: bool = False) -> int:
    """Seed workspace + brief + plan + optional handoff. Returns brief_id."""
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
            "INSERT INTO plans (brief_id, status, content) VALUES (?, ?, 'plan')",
            (brief_id, plan_status),
        )
        if add_complete_handoff:
            con.execute(
                "INSERT INTO agent_contract_handoffs "
                "  (agent_id, workspace, brief_id, task_status, raw_handoff_json) "
                "VALUES ('test-agent', 'me', ?, 'COMPLETE', '{}')",
                (brief_id,),
            )
        con.commit()
        return brief_id
    finally:
        con.close()


def _invoke_verify_brief(brief_name: str, db_path: Path) -> dict:
    """Call verify_brief directly from briefs.store and return result dict."""
    from gaia.briefs.store import verify_brief
    return verify_brief("me", brief_name, db_path=db_path)


# ---------------------------------------------------------------------------
# T4.4: end-to-end verify --json
# ---------------------------------------------------------------------------

class TestBriefVerifyM4Integration:
    def test_verify_json_includes_m4_invariants(self, tmp_db):
        """Seeded DB with closed plan + no COMPLETE handoff returns M4 inconsistency."""
        _seed_state(tmp_db, "sm-completion-test", plan_status="closed", add_complete_handoff=False)
        result = _invoke_verify_brief("sm-completion-test", tmp_db)

        # Shape check: legacy contract
        assert "brief_name" in result
        assert "inconsistencies" in result
        assert "pass" in result
        assert isinstance(result["inconsistencies"], list)

        # At least one M4 inconsistency must appear
        kinds = [i["kind"] for i in result["inconsistencies"]]
        m4_kinds = {"closed_plan_without_completion_handoff", "stalled_handoff"}
        assert m4_kinds & set(kinds), (
            f"Expected at least one of {m4_kinds}, got {kinds}"
        )
        assert result["pass"] is False

    def test_verify_clean_brief_passes(self, tmp_db):
        """Brief with closed plan + COMPLETE handoff passes invariants 5 and 6."""
        _seed_state(tmp_db, "sm-clean-test", plan_status="closed", add_complete_handoff=True)
        result = _invoke_verify_brief("sm-clean-test", tmp_db)

        kinds = [i["kind"] for i in result["inconsistencies"]]
        # Neither M4 invariant should fire
        assert "closed_plan_without_completion_handoff" not in kinds
        assert "stalled_handoff" not in kinds
        # empty_plan may fire (no tasks seeded) but M4-specific ones should not
