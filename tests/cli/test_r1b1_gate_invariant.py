"""Tests for R1-B-1: the deterministic gate well-formedness invariant.

verify_brief (gaia/briefs/store.py) gains Invariant 9: per plan task, load its
persisted task_gates (R1-A) and run validate_gate (gaia/state/gate_validation.py)
over each. A task with zero gates -> task_missing_gate; a gate that validate_gate
rejects -> task_malformed_gate. The invariant appends to the existing
inconsistencies list and does NOT change verify_brief's signature or return shape.
It inherits the EXISTING dual surface of verify_brief with NO new enforcement:
advisory (exit 0) under `gaia brief close`, exit-2-capable under `gaia brief verify`.

Filename is intentionally neutral (does not contain the -k selector strings) so
each acceptance-criterion selector matches ONLY the function names below:
  * AC-1  -k gate_wellformedness_invariant
  * AC-2  -k gate_wellformedness_valid
  * AC-3  -k gate_invariant_dual_surface
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# bin/ on the path so the CLI handlers (cli.brief) are importable for AC-3.
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


# ---------------------------------------------------------------------------
# Fixture -- bootstrapped tmp DB, routed via GAIA_DATA_DIR so both the
# db_path-explicit calls (AC-1/AC-2) and the CLI-resolved calls (AC-3, which
# call verify_brief WITHOUT db_path) hit the same database.
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

def _seed_brief_and_plan(db_path: Path, brief_name: str, plan_status: str = "draft"):
    """Seed workspace + brief (draft) + plan. Returns (brief_id, plan_id)."""
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


def _add_task(db_path: Path, plan_id: int, order_num: int, goal: str = "do a thing") -> int:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute(
            "INSERT INTO tasks (plan_id, order_num, goal) VALUES (?, ?, ?)",
            (plan_id, order_num, goal),
        )
        task_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        con.commit()
        return task_id
    finally:
        con.close()


def _add_gate(db_path: Path, task_id: int, verification_type: str = "command",
              evidence_shape: str | None = "pytest tests/ -q",
              status: str = "pending") -> None:
    """Insert a task_gates row. evidence_shape=None yields a gate that
    validate_gate rejects (a valid-enum verification_type but empty required
    evidence field) -- the DB CHECK only constrains verification_type."""
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    try:
        con.execute(
            "INSERT INTO task_gates "
            "(task_id, verification_type, evidence_shape, status) "
            "VALUES (?, ?, ?, ?)",
            (task_id, verification_type, evidence_shape, status),
        )
        con.commit()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# AC-1  (-k gate_wellformedness_invariant)
# ---------------------------------------------------------------------------

class TestAc1GateWellformednessInvariant:
    def test_gate_wellformedness_invariant_missing_gate(self, tmp_db):
        """A task with zero task_gates rows -> task_missing_gate."""
        from gaia.briefs.store import verify_brief
        _, plan_id = _seed_brief_and_plan(tmp_db, "r1b1-missing")
        _add_task(tmp_db, plan_id, 1)  # no gate attached
        result = verify_brief("me", "r1b1-missing", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "task_missing_gate" in kinds
        assert result["pass"] is False

    def test_gate_wellformedness_invariant_malformed_gate(self, tmp_db):
        """A gate validate_gate rejects (empty required evidence) -> task_malformed_gate."""
        from gaia.briefs.store import verify_brief
        _, plan_id = _seed_brief_and_plan(tmp_db, "r1b1-malformed")
        task_id = _add_task(tmp_db, plan_id, 1)
        _add_gate(tmp_db, task_id, verification_type="command", evidence_shape=None)
        result = verify_brief("me", "r1b1-malformed", db_path=tmp_db)
        malformed = [i for i in result["inconsistencies"] if i["kind"] == "task_malformed_gate"]
        assert malformed, f"expected task_malformed_gate, got {result['inconsistencies']}"
        # validator errors are embedded in the detail
        assert "evidence_shape" in malformed[0]["detail"]
        # A well-formed gate was not present, so this is not task_missing_gate.
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "task_missing_gate" not in kinds


# ---------------------------------------------------------------------------
# AC-2  (-k gate_wellformedness_valid)
# ---------------------------------------------------------------------------

class TestAc2GateWellformednessValid:
    def test_gate_wellformedness_valid_no_inconsistency(self, tmp_db):
        """Every task carries >=1 well-formed gate -> no gate inconsistency, pass True."""
        from gaia.briefs.store import verify_brief
        _, plan_id = _seed_brief_and_plan(tmp_db, "r1b1-valid")
        t1 = _add_task(tmp_db, plan_id, 1)
        t2 = _add_task(tmp_db, plan_id, 2)
        _add_gate(tmp_db, t1, verification_type="command", evidence_shape="pytest -q")
        _add_gate(tmp_db, t2, verification_type="self_review",
                  evidence_shape="reviewed the diff")
        result = verify_brief("me", "r1b1-valid", db_path=tmp_db)
        kinds = [i["kind"] for i in result["inconsistencies"]]
        assert "task_missing_gate" not in kinds
        assert "task_malformed_gate" not in kinds
        assert result["pass"] is True, (
            f"expected clean pass, got {result['inconsistencies']}"
        )


# ---------------------------------------------------------------------------
# AC-3  (-k gate_invariant_dual_surface)
# ---------------------------------------------------------------------------

class TestAc3GateInvariantDualSurface:
    def test_gate_invariant_dual_surface_close_is_advisory_exit0(self, tmp_db, capsys):
        """`gaia brief close` surfaces the gate inconsistency as an advisory
        stderr warning and still returns 0 (never blocks)."""
        from cli.brief import _cmd_close
        from gaia.briefs import get_brief
        _, plan_id = _seed_brief_and_plan(tmp_db, "r1b1-close")
        _add_task(tmp_db, plan_id, 1)  # gate-less -> task_missing_gate
        rc = _cmd_close(argparse.Namespace(name="r1b1-close", workspace="me"))
        captured = capsys.readouterr()
        assert rc == 0, f"close must be advisory (exit 0), got {rc}; stderr={captured.err}"
        assert get_brief("me", "r1b1-close", db_path=tmp_db)["status"] == "closed"
        assert "Warning:" in captured.err
        assert "task_missing_gate" in captured.err

    def test_gate_invariant_dual_surface_verify_exit2_missing(self, tmp_db):
        """`gaia brief verify` returns exit 2 when a task has no gate."""
        from cli.brief import _cmd_verify
        _, plan_id = _seed_brief_and_plan(tmp_db, "r1b1-verify-missing")
        _add_task(tmp_db, plan_id, 1)  # gate-less
        rc = _cmd_verify(
            argparse.Namespace(name="r1b1-verify-missing", workspace="me", json=False)
        )
        assert rc == 2, f"verify must return exit 2 for a missing gate, got {rc}"

    def test_gate_invariant_dual_surface_verify_exit2_malformed(self, tmp_db):
        """`gaia brief verify` returns exit 2 when a task's gate is malformed."""
        from cli.brief import _cmd_verify
        _, plan_id = _seed_brief_and_plan(tmp_db, "r1b1-verify-malformed")
        task_id = _add_task(tmp_db, plan_id, 1)
        _add_gate(tmp_db, task_id, verification_type="command", evidence_shape=None)
        rc = _cmd_verify(
            argparse.Namespace(name="r1b1-verify-malformed", workspace="me", json=False)
        )
        assert rc == 2, f"verify must return exit 2 for a malformed gate, got {rc}"
