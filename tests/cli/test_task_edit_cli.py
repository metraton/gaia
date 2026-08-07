"""`gaia task edit` and `gaia task gate edit` -- in-place content editors that
close the "remove + add destroys task_gates" gap: `task_gates.task_id` carries
ON DELETE CASCADE from `tasks.id` (schema.sql), so the only prior way to
adjust a task's goal cascaded away every gate attached to it. These verbs
wrap `gaia.store.writer.update_task` / `update_gate`, mirroring the
`gaia brief ac edit` in-place convention (tests/cli/test_brief_ac_edit_cli.py)
and the `gaia task gate add|list|remove` round-trip style
(tests/cli/test_task_gates_cli.py).

Matchable by ``pytest tests/ -k task_edit_cli -q``.
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
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    from gaia.paths import db_path
    return db_path()


def _seed_task(tmp_db: Path, brief: str = "edit-brief", order_num: int = 1,
               goal: str = "original goal") -> None:
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan, add_task_to_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, order_num, goal, db_path=tmp_db)


def _raw_task_row(tmp_db, brief="edit-brief", order_num=1):
    con = sqlite3.connect(str(tmp_db))
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT t.id, t.goal, t.status, t.order_num "
            "FROM tasks t JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.workspace = 'me' AND b.name = ? AND t.order_num = ?",
            (brief, order_num),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        con.close()


def _raw_gate_rows(tmp_db, task_id):
    con = sqlite3.connect(str(tmp_db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, verification_type, evidence_type, evidence_shape, "
            "artifact_path, status FROM task_gates WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def _task_edit_args(**overrides):
    base = dict(
        brief="edit-brief", order_num=1, goal=None, goal_file=None,
        workspace="me", json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _gate_edit_args(**overrides):
    base = dict(
        brief="edit-brief", order_num=1, gate_id=None,
        verification_type=None, evidence_type=None,
        evidence_shape=None, evidence_shape_file=None,
        artifact_path=None, workspace="me", json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _add_gate_args(**overrides):
    base = dict(
        brief="edit-brief", order_num=1, type="command",
        evidence_type="pytest", evidence_shape="pytest -q", artifact_path=None,
        status="pending", workspace="me", json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# gaia task edit
# ---------------------------------------------------------------------------

def test_task_edit_registered_in_parser():
    """`edit` must appear alongside add/remove/reorder in the registered CLI."""
    from cli.task import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    register(subparsers)
    args = parser.parse_args([
        "task", "edit", "some-brief", "3", "--goal=revised goal",
    ])
    assert args.task_action == "edit"
    assert args.order_num == 3
    assert args.goal == "revised goal"


def test_task_edit_goal_preserves_gates_and_id(tmp_db, tmp_path, monkeypatch):
    """The central property: editing a task's goal must never destroy its
    task_gates rows -- unlike remove + add, which cascades them away."""
    from cli.task import _cmd_edit, _cmd_gate_add

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    before = _raw_task_row(tmp_db)
    task_id_before = before["id"]

    assert _cmd_gate_add(_add_gate_args()) == 0

    gates_before = _raw_gate_rows(tmp_db, task_id_before)
    assert len(gates_before) == 1
    gate_id_before = gates_before[0]["id"]

    args = _task_edit_args(goal="revised goal")
    assert _cmd_edit(args) == 0

    after = _raw_task_row(tmp_db)
    assert after["id"] == task_id_before
    assert after["goal"] == "revised goal"
    assert after["status"] == "pending"  # untouched
    assert after["order_num"] == 1

    gates_after = _raw_gate_rows(tmp_db, task_id_before)
    assert len(gates_after) == 1
    assert gates_after[0]["id"] == gate_id_before
    assert gates_after[0]["evidence_shape"] == "pytest -q"


def test_task_edit_goal_from_file(tmp_db, tmp_path, monkeypatch):
    from cli.task import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    goal_file = tmp_path / "goal.txt"
    goal_file.write_text("goal from file", encoding="utf-8")

    args = _task_edit_args(goal_file=str(goal_file))
    assert _cmd_edit(args) == 0

    row = _raw_task_row(tmp_db)
    assert row["goal"] == "goal from file"


def test_task_edit_requires_a_goal(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    args = _task_edit_args(json=True)
    assert _cmd_edit(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


def test_task_edit_missing_order_num_fails_clearly(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    args = _task_edit_args(order_num=99, goal="new goal", json=True)
    rc = _cmd_edit(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert "99" in out["error"]


# ---------------------------------------------------------------------------
# gaia task gate edit
# ---------------------------------------------------------------------------

def test_task_gate_edit_registered_in_parser():
    from cli.task import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    register(subparsers)
    args = parser.parse_args([
        "task", "gate", "edit", "some-brief", "1", "3",
        "--evidence-shape=pytest -q -k foo",
    ])
    assert args.gate_action == "edit"
    assert args.gate_id == 3
    assert args.evidence_shape == "pytest -q -k foo"


def test_task_gate_edit_partial_update_preserves_id_and_other_fields(
    tmp_db, tmp_path, monkeypatch,
):
    from cli.task import _cmd_gate_add, _cmd_gate_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    assert _cmd_gate_add(_add_gate_args()) == 0

    task_id = _raw_task_row(tmp_db)["id"]
    gate_before = _raw_gate_rows(tmp_db, task_id)[0]
    gate_id = gate_before["id"]

    args = _gate_edit_args(gate_id=gate_id, evidence_shape="pytest -q -k new")
    assert _cmd_gate_edit(args) == 0

    gate_after = _raw_gate_rows(tmp_db, task_id)[0]
    assert gate_after["id"] == gate_id
    assert gate_after["evidence_shape"] == "pytest -q -k new"
    # Untouched fields survive the partial edit.
    assert gate_after["verification_type"] == "command"
    assert gate_after["evidence_type"] == "pytest"
    assert gate_after["status"] == "pending"


def test_task_gate_edit_evidence_shape_from_file(tmp_db, tmp_path, monkeypatch):
    from cli.task import _cmd_gate_add, _cmd_gate_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    assert _cmd_gate_add(_add_gate_args(evidence_type=None)) == 0
    task_id = _raw_task_row(tmp_db)["id"]
    gate_id = _raw_gate_rows(tmp_db, task_id)[0]["id"]

    shape_file = tmp_path / "shape.txt"
    shape_text = "kubectl get pods -n <ns>; expect Running"
    shape_file.write_text(shape_text, encoding="utf-8")

    args = _gate_edit_args(gate_id=gate_id, evidence_shape_file=str(shape_file))
    assert _cmd_gate_edit(args) == 0

    gate_after = _raw_gate_rows(tmp_db, task_id)[0]
    assert gate_after["evidence_shape"] == shape_text


def test_task_gate_edit_requires_at_least_one_field(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_add, _cmd_gate_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    assert _cmd_gate_add(_add_gate_args()) == 0
    capsys.readouterr()  # discard the add's own stdout before asserting on edit's
    task_id = _raw_task_row(tmp_db)["id"]
    gate_id = _raw_gate_rows(tmp_db, task_id)[0]["id"]

    args = _gate_edit_args(gate_id=gate_id, json=True)
    assert _cmd_gate_edit(args) == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


def test_task_gate_edit_missing_gate_id_fails_clearly(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_edit

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    args = _gate_edit_args(gate_id=999, evidence_shape="x", json=True)
    rc = _cmd_gate_edit(args)
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert "999" in out["error"]


def test_task_gate_edit_never_touches_status(tmp_db, tmp_path, monkeypatch):
    """update_gate accepts no `status` kwarg; a gate already marked pass/fail
    must not silently revert to pending as a side effect of an unrelated edit."""
    from cli.task import _cmd_gate_add, _cmd_gate_edit, _cmd_gate_set_status

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    assert _cmd_gate_add(_add_gate_args()) == 0
    task_id = _raw_task_row(tmp_db)["id"]
    gate_id = _raw_gate_rows(tmp_db, task_id)[0]["id"]

    rc = _cmd_gate_set_status(argparse.Namespace(
        brief="edit-brief", order_num=1, gate_id=gate_id, status="pass",
        workspace="me", json=False,
    ))
    assert rc == 0
    assert _raw_gate_rows(tmp_db, task_id)[0]["status"] == "pass"

    args = _gate_edit_args(gate_id=gate_id, artifact_path="/tmp/evidence.txt")
    assert _cmd_gate_edit(args) == 0

    gate_after = _raw_gate_rows(tmp_db, task_id)[0]
    assert gate_after["status"] == "pass"  # untouched by the content edit
    assert gate_after["artifact_path"] == "/tmp/evidence.txt"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
