"""AC-4 (harness R1-B-0): `gaia task list <brief>` lists the tasks of ONE plan
with --status and --format=table|json|count, mirroring `gaia brief list`.

Matchable by ``pytest tests/ -k task_list -q``.

Drives the CLI handler (cli.task._cmd_list) directly against an isolated
substrate DB (GAIA_DATA_DIR -> tmp_path), the same style as
tests/cli/test_task_gates_cli.py. Asserts the three output formats and the
--status filter, and that the reader is scoped to a single plan.
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


def _seed_tasks(tmp_db: Path, brief: str = "list-brief") -> None:
    """Seed brief -> plan -> three tasks with mixed statuses."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import (
        upsert_plan, add_task_to_plan, set_task_status,
    )

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, 1, "pending task AC-1", db_path=tmp_db)
    add_task_to_plan("me", brief, 2, "done task AC-2", db_path=tmp_db)
    add_task_to_plan("me", brief, 3, "skipped task AC-3", db_path=tmp_db)
    set_task_status("me", brief, 2, "done", db_path=tmp_db)
    set_task_status("me", brief, 3, "skipped", db_path=tmp_db)


def _args(brief="list-brief", status=None, fmt="table"):
    return argparse.Namespace(
        brief=brief, status=status, format=fmt, workspace="me",
    )


def test_task_list_count_returns_only_the_number(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_tasks(tmp_db)

    assert _cmd_list(_args(fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "3"


def test_task_list_count_with_status_filter(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_tasks(tmp_db)

    assert _cmd_list(_args(status="pending", fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert _cmd_list(_args(status="done", fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "1"
    assert _cmd_list(_args(status="skipped", fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_task_list_json_shape_and_order(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_tasks(tmp_db)

    assert _cmd_list(_args(fmt="json")) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [t["order_num"] for t in listed] == [1, 2, 3]
    assert [t["status"] for t in listed] == ["pending", "done", "skipped"]
    assert listed[0]["goal"] == "pending task AC-1"


def test_task_list_json_status_filter(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_tasks(tmp_db)

    assert _cmd_list(_args(status="done", fmt="json")) == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert listed[0]["order_num"] == 2
    assert listed[0]["status"] == "done"


def test_task_list_table_format(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    _seed_tasks(tmp_db)

    assert _cmd_list(_args(fmt="table")) == 0
    out = capsys.readouterr().out
    assert "ORDER" in out and "STATUS" in out and "GOAL" in out
    assert "pending task AC-1" in out


def test_task_list_empty_plan(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan

    monkeypatch.chdir(tmp_path)
    upsert_brief("me", "empty-brief", {"status": "open", "title": "x"},
                 db_path=tmp_db)
    upsert_plan("me", "empty-brief", content="body", status="active",
                db_path=tmp_db)

    assert _cmd_list(_args(brief="empty-brief", fmt="count")) == 0
    assert capsys.readouterr().out.strip() == "0"
    assert _cmd_list(_args(brief="empty-brief", fmt="table")) == 0
    assert "(no tasks)" in capsys.readouterr().out


def test_task_list_missing_brief_errors(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_list

    monkeypatch.chdir(tmp_path)
    assert _cmd_list(_args(brief="does-not-exist", fmt="count")) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
