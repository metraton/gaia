"""`gaia contract list` filters: --plan-task, --brief, --since/--until.

Rows are seeded with a NULL ``brief_id``, as live dispatch rows carry it, so
``--brief`` is exercised through the brief's plan tasks rather than that column.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
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
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    from gaia.paths import db_path
    return db_path()


def _seed_brief(tmp_db: Path, brief: str, orders: tuple[int, ...]) -> dict[int, int]:
    from gaia.briefs import upsert_brief
    from gaia.store.writer import add_task_to_plan, list_plan_tasks, upsert_plan

    upsert_brief("me", brief, {"status": "open", "title": brief}, db_path=tmp_db)
    upsert_plan("me", brief, content="plan", status="active", db_path=tmp_db)
    for order in orders:
        add_task_to_plan("me", brief, order, f"task {order}", db_path=tmp_db)
    return {t["order_num"]: t["id"]
            for t in list_plan_tasks("me", brief, db_path=tmp_db)}


def _ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _insert_row(tmp_db: Path, *, plan_task_id=None, continues=None,
                created_at=None) -> int:
    con = sqlite3.connect(str(tmp_db))
    try:
        cur = con.execute(
            "INSERT INTO agent_contract_handoffs (agent_id, workspace, "
            "agent_state, raw_handoff_json, created_at, plan_task_id, "
            "continues_handoff_id, brief_id) VALUES (?, 'me', 'COMPLETE', '{}', "
            "?, ?, ?, NULL)",
            ("a" + "0" * 16, created_at or _ago(minutes=5), plan_task_id,
             continues),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _list(capsys, *argv) -> tuple[int, dict | None, str]:
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", "list", "--json", *argv])
    rc = args.func(args)
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if rc == 0 else None
    return rc, payload, captured.out + captured.err


def _ids(payload: dict) -> set[int]:
    return {row["id"] for row in payload["handoffs"]}


def test_plan_task_includes_the_continuation_of_a_bound_row(tmp_db, capsys):
    tasks = _seed_brief(tmp_db, "brief-a", (1, 2))
    bound = _insert_row(tmp_db, plan_task_id=tasks[1])
    resumed = _insert_row(tmp_db, continues=bound)
    _insert_row(tmp_db, plan_task_id=tasks[2])
    _insert_row(tmp_db)

    rc, payload, _ = _list(capsys, "--plan-task", str(tasks[1]))
    assert rc == 0
    assert _ids(payload) == {bound, resumed}


def test_brief_resolves_through_its_plan_tasks(tmp_db, capsys):
    tasks_a = _seed_brief(tmp_db, "brief-a", (1, 2))
    tasks_b = _seed_brief(tmp_db, "brief-b", (1,))
    a1 = _insert_row(tmp_db, plan_task_id=tasks_a[1])
    a2 = _insert_row(tmp_db, plan_task_id=tasks_a[2])
    _insert_row(tmp_db, plan_task_id=tasks_b[1])

    rc, payload, _ = _list(capsys, "--brief", "brief-a")
    assert rc == 0
    assert _ids(payload) == {a1, a2}


def test_since_accepts_a_duration(tmp_db, capsys):
    _seed_brief(tmp_db, "brief-a", (1,))
    recent = _insert_row(tmp_db, created_at=_ago(hours=1))
    _insert_row(tmp_db, created_at=_ago(days=3))

    rc, payload, _ = _list(capsys, "--since", "24h")
    assert rc == 0
    assert _ids(payload) == {recent}


def test_until_is_applied_before_the_default_limit(tmp_db, capsys):
    _seed_brief(tmp_db, "brief-a", (1,))
    old = _insert_row(tmp_db, created_at="2026-01-01T10:00:00Z")
    for minutes in range(25):
        _insert_row(tmp_db, created_at=_ago(minutes=minutes))

    rc, payload, _ = _list(capsys, "--until", "2026-01-02")
    assert rc == 0
    assert _ids(payload) == {old}


def test_malformed_since_names_the_accepted_forms(tmp_db, capsys):
    _seed_brief(tmp_db, "brief-a", (1,))

    rc, _, output = _list(capsys, "--since", "yesterday")
    assert rc != 0
    assert "24h" in output
    assert "YYYY-MM-DD" in output


def test_epilog_leads_with_the_plan_task_example(capsys):
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    with pytest.raises(SystemExit):
        parser.parse_args(["contract", "list", "--help"])
    help_text = capsys.readouterr().out
    examples = help_text.split("Examples:", 1)[1].strip().splitlines()
    assert examples[0].strip().startswith("gaia contract list --plan-task")
    assert "DUR_OR_DATE" in help_text
