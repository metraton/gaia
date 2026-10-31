"""`gaia contract list --grep/--agent-name/--task` and `gaia contract view --row`."""

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
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.chdir(tmp_path)
    from gaia.paths import db_path
    from gaia.store.writer import _connect

    _connect(db_path()).close()
    return db_path()


def _insert(tmp_db: Path, *, agent_id="a" + "0" * 16, contract_id=None, prompt=None,
            description=None, envelope=None, harness_id=None, plan_task_id=None,
            state="COMPLETE") -> int:
    con = sqlite3.connect(str(tmp_db))
    try:
        cur = con.execute(
            "INSERT INTO agent_contract_handoffs (agent_id, workspace, agent_state, "
            "raw_handoff_json, contract_id, dispatch_prompt, dispatch_description, "
            "harness_agent_id, session_id, dispatch_project, plan_task_id) "
            "VALUES (?, 'me', ?, ?, ?, ?, ?, ?, 'sess-1', 'gaia (/repo)', ?)",
            (agent_id, state, json.dumps(envelope or {}), contract_id, prompt,
             description, harness_id, plan_task_id),
        )
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def _run(capsys, *argv) -> tuple[int, str, str]:
    from cli.contract import register

    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="subcommand"))
    args = parser.parse_args(["contract", *argv])
    rc = args.func(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _listed(capsys, *argv) -> set[int]:
    rc, out, err = _run(capsys, "list", "--json", *argv)
    assert rc == 0, err
    return {row["id"] for row in json.loads(out)["handoffs"]}


def test_grep_matches_goal_description_and_conclusion_case_insensitively(tmp_db, capsys):
    by_goal = _insert(tmp_db, prompt="Audit the Approval surface")
    by_description = _insert(tmp_db, description="approval audit")
    by_conclusion = _insert(tmp_db, envelope={"conclusion": "The APPROVAL lane holds."})
    _insert(tmp_db, prompt="unrelated", envelope={"key_outputs": ["approval"]})

    assert _listed(capsys, "--grep", "approval") == {by_goal, by_description, by_conclusion}


def test_grep_treats_like_wildcards_literally(tmp_db, capsys):
    percent = _insert(tmp_db, prompt="raise coverage to 80% now")
    _insert(tmp_db, prompt="raise coverage to 800 now")

    assert _listed(capsys, "--grep", "80%") == {percent}


def test_grep_is_bounded_by_limit(tmp_db, capsys):
    for _ in range(3):
        _insert(tmp_db, prompt="same goal")

    assert len(_listed(capsys, "--grep", "same", "--limit", "2")) == 2


def test_agent_name_filters_on_the_dispatched_name(tmp_db, capsys):
    system = _insert(tmp_db, envelope={"agent_name": "gaia-system"}, state="DISPATCHED")
    _insert(tmp_db, envelope={"agent_name": "developer"}, state="DISPATCHED")

    assert _listed(capsys, "--agent-name", "gaia-system") == {system}


def test_task_is_the_same_filter_as_plan_task(tmp_db, capsys):
    from gaia.briefs import upsert_brief
    from gaia.store.writer import add_task_to_plan, list_plan_tasks, upsert_plan

    upsert_brief("me", "b", {"status": "open", "title": "b"}, db_path=tmp_db)
    upsert_plan("me", "b", content="plan", status="active", db_path=tmp_db)
    add_task_to_plan("me", "b", 1, "t", db_path=tmp_db)
    task_id = list_plan_tasks("me", "b", db_path=tmp_db)[0]["id"]
    bound = _insert(tmp_db, plan_task_id=task_id)
    _insert(tmp_db)

    assert _listed(capsys, "--task", str(task_id)) == _listed(
        capsys, "--plan-task", str(task_id)) == {bound}


def _row(capsys, *argv) -> dict:
    rc, out, err = _run(capsys, "view", "--row", *argv)
    assert rc == 0, err
    return json.loads(out)


def test_view_row_prints_the_dispatch_columns_by_harness_id(tmp_db, capsys):
    _insert(tmp_db, contract_id="a1111111111111111.x", harness_id="a2222222222222222",
            prompt="the goal", description="short label",
            envelope={"agent_name": "gaia-system"}, state="DISPATCHED")

    row = _row(capsys, "--harness-id", "a2222222222222222")

    assert {key: row[key] for key in (
        "goal", "description", "agent_name", "project", "session_id", "agent_state",
        "contract_id")} == {
        "goal": "the goal", "description": "short label", "agent_name": "gaia-system",
        "project": "gaia (/repo)", "session_id": "sess-1", "agent_state": "DISPATCHED",
        "contract_id": "a1111111111111111.x"}


def test_view_row_by_contract_id(tmp_db, capsys):
    _insert(tmp_db, contract_id="a3333333333333333.y", prompt="another goal")

    assert _row(capsys, "--contract-id", "a3333333333333333.y")["goal"] == "another goal"


def test_view_harness_id_resolves_an_agent_id(tmp_db, capsys):
    _insert(tmp_db, agent_id="a4444444444444444", contract_id="a4444444444444444.z",
            harness_id="a5555555555555555", prompt="goal by agent id")

    assert _row(capsys, "--harness-id", "a4444444444444444")["goal"] == "goal by agent id"
    rc, out, err = _run(capsys, "view", "--harness-id", "a4444444444444444")
    assert rc == 0, err
    assert json.loads(out)["contract_id"] == "a4444444444444444.z"


def test_view_row_without_a_coordinate_is_refused(tmp_db, capsys):
    rc, _, err = _run(capsys, "view", "--row")

    assert rc == 1
    assert "--harness-id" in err and "--contract-id" in err
