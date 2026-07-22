"""AC-3 (harness B3/T3): `gaia task gate set-status` -- the write-path for
task_gates.status.

Matchable by ``pytest tests/ -k gate_status_write -q``.

Prior to this task, task_gates.status could only be set at INSERT time
(add_gate_to_task's ``status`` kwarg); there was no way to transition an
existing gate's status. This covers:

  * add / set-status / list round-trip through the CLI handlers
    (cli.task._cmd_gate_*), against an isolated substrate DB
    (GAIA_DATA_DIR -> tmp_path), mirroring tests/cli/test_task_gates_cli.py.
  * the pending -> pass -> fail vocabulary transitions.
  * the code-level guard (gaia.store.writer._assert_valid_gate_status /
    gaia.state.VALID_GATE_STATUSES) rejects an out-of-vocabulary status at
    BOTH write paths: add_gate_to_task (initial status) and set_gate_status
    (transition) -- exercised both through the writer directly and through
    the CLI handler.
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


def _seed_task(tmp_db: Path, brief: str = "gate-status-brief", order_num: int = 1) -> None:
    """Seed workspace 'me' -> brief -> plan -> one task via the real writers."""
    from gaia.briefs import upsert_brief
    from gaia.store.writer import upsert_plan, add_task_to_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan body", status="active", db_path=tmp_db)
    add_task_to_plan("me", brief, order_num, "do the thing", db_path=tmp_db)


def _gate_status(db_path: Path, gate_id: int) -> str:
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT status FROM task_gates WHERE id = ?", (gate_id,)
        ).fetchone()
        return row[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Writer-level: add / set_gate_status / list round-trip + invalid rejection
# ---------------------------------------------------------------------------

def test_gate_status_write_writer_add_set_status_list_round_trip(tmp_db, monkeypatch):
    from gaia.store.writer import add_gate_to_task, set_gate_status, list_task_gates

    _seed_task(tmp_db)

    added = add_gate_to_task(
        "me", "gate-status-brief", 1, "command",
        evidence_shape="pytest -q", status="pending", db_path=tmp_db,
    )
    gate_id = added["gate_id"]
    assert _gate_status(tmp_db, gate_id) == "pending"

    res = set_gate_status("me", "gate-status-brief", 1, gate_id, "pass", db_path=tmp_db)
    assert res["status"] == "applied"
    assert res["action"] == "status_updated"
    assert res["old_status"] == "pending"
    assert res["new_status"] == "pass"
    assert _gate_status(tmp_db, gate_id) == "pass"

    gates = list_task_gates("me", "gate-status-brief", 1, db_path=tmp_db)
    assert len(gates) == 1
    assert gates[0]["id"] == gate_id
    assert gates[0]["status"] == "pass"

    # A further transition to 'fail' is also accepted (membership-only guard,
    # no transition-legality state machine for this column -- see schema.sql).
    res2 = set_gate_status("me", "gate-status-brief", 1, gate_id, "fail", db_path=tmp_db)
    assert res2["old_status"] == "pass"
    assert res2["new_status"] == "fail"
    assert _gate_status(tmp_db, gate_id) == "fail"


def test_gate_status_write_writer_rejects_invalid_status_on_set_status(tmp_db):
    from gaia.store.writer import add_gate_to_task, set_gate_status

    _seed_task(tmp_db)
    added = add_gate_to_task(
        "me", "gate-status-brief", 1, "command", status="pending", db_path=tmp_db,
    )
    gate_id = added["gate_id"]

    with pytest.raises(ValueError):
        set_gate_status("me", "gate-status-brief", 1, gate_id, "bogus", db_path=tmp_db)

    # Rejected write did not land.
    assert _gate_status(tmp_db, gate_id) == "pending"


def test_gate_status_write_writer_rejects_invalid_status_on_add(tmp_db):
    from gaia.store.writer import add_gate_to_task

    _seed_task(tmp_db)
    with pytest.raises(ValueError):
        add_gate_to_task(
            "me", "gate-status-brief", 1, "command", status="bogus", db_path=tmp_db,
        )


# ---------------------------------------------------------------------------
# CLI-level: `gaia task gate add|set-status|list` round-trip + rejection
# ---------------------------------------------------------------------------

def test_gate_status_write_cli_add_set_status_list_round_trip(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_add, _cmd_gate_set_status, _cmd_gate_list

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    add_args = argparse.Namespace(
        brief="gate-status-brief", order_num=1, type="command",
        evidence_type="pytest", evidence_shape="pytest -q", artifact_path=None,
        status="pending", workspace="me", json=True,
    )
    rc = _cmd_gate_add(add_args)
    assert rc == 0
    added = json.loads(capsys.readouterr().out)
    gate_id = added["gate_id"]

    set_status_args = argparse.Namespace(
        brief="gate-status-brief", order_num=1, gate_id=gate_id, status="pass",
        workspace="me", json=True,
    )
    rc = _cmd_gate_set_status(set_status_args)
    assert rc == 0, capsys.readouterr()
    updated = json.loads(capsys.readouterr().out)
    assert updated["action"] == "status_updated"
    assert updated["old_status"] == "pending"
    assert updated["new_status"] == "pass"

    list_args = argparse.Namespace(
        brief="gate-status-brief", order_num=1, workspace="me", json=True,
    )
    rc = _cmd_gate_list(list_args)
    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 1
    assert listed[0]["id"] == gate_id
    assert listed[0]["status"] == "pass"


def test_gate_status_write_cli_set_status_rejects_invalid_status(tmp_db, tmp_path, monkeypatch, capsys):
    from cli.task import _cmd_gate_add, _cmd_gate_set_status

    monkeypatch.chdir(tmp_path)
    _seed_task(tmp_db)

    add_args = argparse.Namespace(
        brief="gate-status-brief", order_num=1, type="command",
        evidence_type=None, evidence_shape=None, artifact_path=None,
        status="pending", workspace="me", json=True,
    )
    rc = _cmd_gate_add(add_args)
    assert rc == 0
    added = json.loads(capsys.readouterr().out)
    gate_id = added["gate_id"]

    bad_args = argparse.Namespace(
        brief="gate-status-brief", order_num=1, gate_id=gate_id, status="bogus",
        workspace="me", json=False,
    )
    rc = _cmd_gate_set_status(bad_args)
    assert rc == 1  # invalid status -> ValueError -> error exit

    # DB unchanged.
    assert _gate_status(tmp_db, gate_id) == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
