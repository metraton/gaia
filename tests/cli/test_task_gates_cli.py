"""AC-2 (harness R1-A): `gaia task gate add|list|remove` round-trips the DB.

Matchable by ``pytest tests/ -k task_gates_cli -q``.

Drives the CLI handlers (cli.task._cmd_gate_*) directly against an isolated
substrate DB (GAIA_DATA_DIR -> tmp_path), the same style as
tests/integration/test_plan_cli.py. Asserts a gate added via the CLI is listed
via the CLI, survives a DB round-trip (read back through a fresh connection),
and is removed via the CLI.
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


def _seed_task(tmp_db: Path, brief: str = "gate-brief", order_num: int = 1) -> None:
    """Seed workspace 'me' -> brief -> plan -> one task via the real writers."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan, add_task_to_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, order_num, "do the thing", db_path=tmp_db)


def _count_gates(db_path: Path, task_order_num: int = 1) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM task_gates tg "
            "JOIN tasks t ON t.id = tg.task_id WHERE t.order_num = ?",
            (task_order_num,),
        ).fetchone()[0]
    finally:
        con.close()


def test_task_gates_cli_add_list_remove_round_trip(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_add, _cmd_gate_list, _cmd_gate_remove

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    # add
    add_args = argparse.Namespace(
        brief="gate-brief", order_num=1, type="command",
        evidence_type="pytest", evidence_shape="pytest -q", artifact_path=None,
        status="pending", workspace="me", json=True,
    )
    rc = _cmd_gate_add(add_args)
    assert rc == 0, capsys.readouterr()
    added = json.loads(capsys.readouterr().out)
    assert added["action"] == "inserted"
    assert added["verification_type"] == "command"
    gate_id = added["gate_id"]

    # It really landed in the DB (round-trip through a fresh connection).
    assert _count_gates(tmp_db) == 1

    # list shows it
    list_args = argparse.Namespace(
        brief="gate-brief", order_num=1, workspace="me", json=True,
    )
    rc = _cmd_gate_list(list_args)
    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert listed[0]["id"] == gate_id
    assert listed[0]["verification_type"] == "command"
    assert listed[0]["evidence_shape"] == "pytest -q"
    assert listed[0]["status"] == "pending"

    # remove
    remove_args = argparse.Namespace(
        brief="gate-brief", order_num=1, gate_id=gate_id, workspace="me", json=True,
    )
    rc = _cmd_gate_remove(remove_args)
    assert rc == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["action"] == "deleted"

    # Gone from the DB, and list is empty.
    assert _count_gates(tmp_db) == 0
    rc = _cmd_gate_list(list_args)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_task_gates_cli_persists_multiple_gates_per_task(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_add, _cmd_gate_list

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    for vtype in ("command", "self_review"):
        rc = _cmd_gate_add(argparse.Namespace(
            brief="gate-brief", order_num=1, type=vtype,
            evidence_type=None, evidence_shape="shape", artifact_path=None,
            status="pending", workspace="me", json=True,
        ))
        assert rc == 0
        capsys.readouterr()

    assert _count_gates(tmp_db) == 2
    rc = _cmd_gate_list(argparse.Namespace(
        brief="gate-brief", order_num=1, workspace="me", json=True,
    ))
    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    assert {g["verification_type"] for g in listed} == {"command", "self_review"}


def test_task_gates_cli_add_task_not_found(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_add

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    rc = _cmd_gate_add(argparse.Namespace(
        brief="gate-brief", order_num=99, type="command",
        evidence_type=None, evidence_shape="shape", artifact_path=None,
        status="pending", workspace="me", json=False,
    ))
    assert rc == 1  # missing task -> ValueError -> error exit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
