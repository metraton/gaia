"""AC-1 (harness R1-B-0): the plan->task attach mechanism materializes task
rows and round-trips through the NEW `gaia task list --format=count`.

Matchable by ``pytest tests/ -k task_row_materialization -q``.

Drives the CLI handlers (cli.task._cmd_add / _cmd_list) directly against an
isolated substrate DB (GAIA_DATA_DIR -> tmp_path), the same style as
tests/cli/test_task_gates_cli.py. Proves that saving a plan then attaching N
task rows persists them with the correct order_num / goal / status, that a
round-trip via `gaia task list` (count / json) returns them in order, and that
a duplicate order_num is rejected. This is the consolidation test over the
existing mechanism (`gaia task add` -> add_task_to_plan), confirming the
planner-behavior path end-to-end.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Route the substrate DB into ``tmp_path``."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_plan(tmp_db: Path, brief: str = "materialize-brief") -> None:
    """Seed workspace 'me' -> brief -> saved plan (NO tasks yet)."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)


def _count_tasks(db_path: Path, brief: str = "materialize-brief") -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM tasks t "
            "JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id WHERE b.name = ?",
            (brief,),
        ).fetchone()[0]
    finally:
        con.close()


def _add_task(order: int, goal: str, brief: str = "materialize-brief"):
    return argparse.Namespace(
        brief=brief, order=order, goal=goal, workspace="me", json=True,
    )


def _list_args(brief: str = "materialize-brief", status=None, fmt="count"):
    return argparse.Namespace(
        brief=brief, status=status, format=fmt, workspace="me",
    )


def test_task_row_materialization_round_trips_via_task_list_count(
    tmp_db, tmp_path, monkeypatch, capsys
):
    from cli.task import _cmd_add, _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_plan(tmp_db)

    # Empty plan: list count is 0 (a saved plan with no tasks, not an error).
    assert _cmd_list(_list_args(fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "0"

    # Materialize three task rows via the real CLI add path, goals referencing
    # valid AC-ids (the shape the planner directive prescribes for Inv2).
    for order, goal in (
        (1, "Implement AC-1 mechanism"),
        (2, "Teach planner directive AC-3"),
        (3, "No regression AC-2"),
    ):
        assert _cmd_add(_add_task(order, goal)) == 0
        capsys.readouterr()

    # They really landed in the DB (round-trip through a fresh connection).
    assert _count_tasks(tmp_db) == 3

    # Round-trip via the NEW `gaia task list --format=count`.
    assert _cmd_list(_list_args(fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "3"

    # Round-trip via json: order_num / goal / status preserved, in order.
    assert _cmd_list(_list_args(fmt="json")) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [t["order_num"] for t in listed] == [1, 2, 3]
    assert listed[0]["goal"] == "Implement AC-1 mechanism"
    assert all(t["status"] == "pending" for t in listed)


def test_task_row_materialization_rejects_duplicate_order_num(
    tmp_db, tmp_path, monkeypatch, capsys
):
    from cli.task import _cmd_add

    monkeypatch.chdir(tmp_path)
    _seed_plan(tmp_db)

    assert _cmd_add(_add_task(1, "first AC-1")) == 0
    capsys.readouterr()

    # Duplicate order_num within the same plan is rejected (non-zero exit).
    assert _cmd_add(_add_task(1, "collision AC-1")) == 1
    assert _count_tasks(tmp_db) == 1


def test_task_row_materialization_requires_saved_plan_first(
    tmp_db, tmp_path, monkeypatch, capsys
):
    """The hard sequencing rule: `gaia plan save` must precede `gaia task add`."""
    from cli.task import _cmd_add
    from gaia.briefs import upsert_brief

    monkeypatch.chdir(tmp_path)
    # Brief exists but NO plan saved yet.
    upsert_brief("me", "no-plan-brief", {"status": "open", "title": "x"},
                 db_path=tmp_db)

    rc = _cmd_add(_add_task(1, "AC-1", brief="no-plan-brief"))
    assert rc == 1  # add before plan save -> "no plan attached" -> error exit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
