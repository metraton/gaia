"""Gap 2 (2026-08-03 CLI-gap fix): `gaia task show` -- a single-task read
that legibly prints tasks.id (the row id the dispatch contract's
task_id=<N> token requires) alongside order_num (the plan-position ordinal),
clearly labeled and never conflated. Also covers the TASK_ID column added to
`gaia task list`'s table view, and the --json alias added to `list` for
parity with query.py/memory.py's own --format=table|json|count + --json
convention.

Matchable by ``pytest tests/ -k task_show_cli -q``.
"""

from __future__ import annotations

import argparse
import json
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


def _seed_two_tasks(tmp_db: Path, brief: str = "show-brief"):
    """Seed brief -> plan -> two tasks; return their raw tasks.id values."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan, add_task_to_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, 1, "first task", db_path=tmp_db)
    add_task_to_plan("me", brief, 2, "second task", db_path=tmp_db)

    from gaia.store.writer import list_plan_tasks
    tasks = list_plan_tasks("me", brief, db_path=tmp_db)
    return {t["order_num"]: t["id"] for t in tasks}


def _show_args(order_num, **overrides):
    base = dict(brief="show-brief", order_num=order_num, workspace="me", json=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def _list_args(**overrides):
    base = dict(brief="show-brief", status=None, format="table", json=False, workspace="me")
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# get_task_by_order (writer layer)
# ---------------------------------------------------------------------------

def test_get_task_by_order_returns_row_with_correct_id(tmp_db, tmp_path, monkeypatch):
    from gaia.store.writer import get_task_by_order

    monkeypatch.chdir(tmp_path)
    ids_by_order = _seed_two_tasks(tmp_db)

    task = get_task_by_order("me", "show-brief", 1, db_path=tmp_db)
    assert task is not None
    assert task["order_num"] == 1
    assert task["id"] == ids_by_order[1]
    # order_num and tasks.id are independently tracked fields on the same
    # row -- the gap this closes is confusing the two, not their numeric
    # values happening to differ in any one fixture.
    assert "id" in task and "order_num" in task


def test_get_task_by_order_missing_order_returns_none(tmp_db, tmp_path, monkeypatch):
    from gaia.store.writer import get_task_by_order

    monkeypatch.chdir(tmp_path)
    _seed_two_tasks(tmp_db)

    assert get_task_by_order("me", "show-brief", 99, db_path=tmp_db) is None


def test_get_task_by_order_missing_brief_raises(tmp_db, tmp_path, monkeypatch):
    from gaia.store.writer import get_task_by_order

    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError):
        get_task_by_order("me", "does-not-exist", 1, db_path=tmp_db)


# ---------------------------------------------------------------------------
# `gaia task show` CLI handler
# ---------------------------------------------------------------------------

def test_cmd_show_json_carries_the_row_id(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_show

    monkeypatch.chdir(tmp_path)
    ids_by_order = _seed_two_tasks(tmp_db)

    assert _cmd_show(_show_args(1, json=True)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] == ids_by_order[1]
    assert out["order_num"] == 1


def test_cmd_show_table_labels_both_numbers_unambiguously(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.task import _cmd_show

    monkeypatch.chdir(tmp_path)
    ids_by_order = _seed_two_tasks(tmp_db)

    assert _cmd_show(_show_args(2)) == 0
    out = capsys.readouterr().out
    assert "ORDER_NUM:" in out
    assert "TASK_ID:" in out
    assert str(ids_by_order[2]) in out
    assert f"task_id={ids_by_order[2]}" in out  # the exact dispatch token spelling


def test_cmd_show_missing_task_errors(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_show

    monkeypatch.chdir(tmp_path)
    _seed_two_tasks(tmp_db)

    assert _cmd_show(_show_args(99, json=True)) == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out


def test_task_show_registered_in_parser():
    from cli.task import register

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    register(subparsers)
    args = parser.parse_args(["task", "show", "my-brief", "1"])
    assert args.task_action == "show"
    assert args.order_num == 1


# ---------------------------------------------------------------------------
# `gaia task list` -- TASK_ID column + --json alias
# ---------------------------------------------------------------------------

def test_cmd_list_table_includes_task_id_column(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    ids_by_order = _seed_two_tasks(tmp_db)

    assert _cmd_list(_list_args(format="table")) == 0
    out = capsys.readouterr().out
    assert "TASK_ID" in out
    assert str(ids_by_order[1]) in out
    assert str(ids_by_order[2]) in out


def test_cmd_list_json_alias_forces_json_even_with_format_table(
    tmp_db, tmp_path, monkeypatch, capsys,
):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_two_tasks(tmp_db)

    assert _cmd_list(_list_args(format="table", json=True)) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 2
    assert "id" in listed[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
