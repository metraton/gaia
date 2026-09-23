"""Managed briefs and plans, phase A: staleness, derived states, structure,
evidence, decisions.

Matchable as one suite::

    pytest tests/test_brief_plan_managed.py -q

Every test runs against a disposable substrate (GAIA_DATA_DIR -> tmp_path) and
goes through the real writers or the real CLI handlers, so what is asserted is
what an agent reaches -- never a hand-built row.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

_WS = "me"
_BRIEF = "managed-brief"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.delenv("GAIA_DB", raising=False)
    from gaia.paths import db_path

    return db_path()


def _seed(db: Path, tasks: int = 1, acs: tuple[str, ...] = ("AC-1",)) -> list[int]:
    """Brief with ``acs``, an active plan, ``tasks`` tasks with one gate each.

    Returns the gate ids, one per task in order.
    """
    from gaia.briefs import add_ac, upsert_brief
    from gaia.store.writer import add_gate_to_task, add_task_to_plan, upsert_plan

    upsert_brief(_WS, _BRIEF, {"status": "open", "title": _BRIEF}, db_path=db)
    for ac in acs:
        add_ac(_WS, _BRIEF, ac, description=f"{ac} holds", db_path=db)
    upsert_plan(_WS, _BRIEF, content="plan", status="active", db_path=db)
    gate_ids = []
    for order in range(1, tasks + 1):
        add_task_to_plan(_WS, _BRIEF, order, f"task {order}", db_path=db)
        gate_ids.append(
            add_gate_to_task(_WS, _BRIEF, order, "command",
                             evidence_shape="pytest -q", db_path=db)["gate_id"]
        )
    return gate_ids


def _gate(db: Path, gate_id: int) -> dict:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute("SELECT * FROM task_gates WHERE id = ?",
                                (gate_id,)).fetchone())
    finally:
        con.close()


def _task_status(db: Path, order: int) -> str:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT t.status FROM tasks t JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id WHERE b.name = ? AND t.order_num = ?",
            (_BRIEF, order),
        ).fetchone()[0]
    finally:
        con.close()


def _pass(db: Path, order: int, gate_id: int) -> None:
    from gaia.store.writer import set_gate_status

    set_gate_status(_WS, _BRIEF, order, gate_id, "pass", db_path=db)


def _cli(module: str, argv: list[str]) -> int:
    import importlib

    mod = importlib.import_module(f"cli.{module}")
    parser = argparse.ArgumentParser()
    mod.register(parser.add_subparsers(dest="command"))
    return getattr(mod, f"cmd_{module}")(parser.parse_args([*argv, "--workspace", _WS]))


# ---------------------------------------------------------------------------
# 1. Staleness: the old verdict is kept, marked stale, and the task follows.
# ---------------------------------------------------------------------------

def test_editing_a_passed_gate_marks_the_verdict_stale_and_reopens_the_task(db):
    from gaia.store.writer import update_gate

    (gate_id,) = _seed(db)
    _pass(db, 1, gate_id)
    assert _task_status(db, 1) == "done"

    update_gate(_WS, _BRIEF, 1, gate_id, evidence_shape="pytest -q -k new", db_path=db)

    gate = _gate(db, gate_id)
    assert gate["status"] == "pass", "the old verdict is kept"
    assert gate["stale_at"] is not None
    assert "gate" in gate["stale_reason"]
    assert _task_status(db, 1) == "pending"

    _pass(db, 1, gate_id)
    gate = _gate(db, gate_id)
    assert gate["stale_at"] is None and gate["stale_reason"] is None
    assert _task_status(db, 1) == "done"


def test_editing_a_pending_gate_marks_nothing_stale(db):
    from gaia.store.writer import update_gate

    (gate_id,) = _seed(db)
    update_gate(_WS, _BRIEF, 1, gate_id, evidence_shape="pytest -q -k x", db_path=db)
    assert _gate(db, gate_id)["stale_at"] is None


def test_changing_the_task_goal_marks_its_verdicts_stale(db):
    from gaia.store.writer import update_task

    (gate_id,) = _seed(db)
    _pass(db, 1, gate_id)
    update_task(_WS, _BRIEF, 1, goal="a different outcome", db_path=db)
    assert _gate(db, gate_id)["stale_at"] is not None
    assert _task_status(db, 1) == "pending"


def test_rewriting_the_goal_with_the_same_text_keeps_the_verdict(db):
    from gaia.store.writer import update_task

    (gate_id,) = _seed(db)
    _pass(db, 1, gate_id)
    update_task(_WS, _BRIEF, 1, goal="task 1", db_path=db)
    assert _gate(db, gate_id)["stale_at"] is None
    assert _task_status(db, 1) == "done"


def test_changing_a_covered_ac_marks_the_covering_verdicts_stale(db):
    from gaia.briefs import update_ac
    from gaia.store.writer import link_task_criteria

    gate_1, gate_2 = _seed(db, tasks=2, acs=("AC-1", "AC-2"))
    link_task_criteria(_WS, _BRIEF, 1, ["AC-1"], db_path=db)
    link_task_criteria(_WS, _BRIEF, 2, ["AC-2"], db_path=db)
    _pass(db, 1, gate_1)
    _pass(db, 2, gate_2)

    update_ac(_WS, _BRIEF, "AC-1", description="AC-1 now means more", db_path=db)

    assert _gate(db, gate_1)["stale_at"] is not None
    assert "AC-1" in _gate(db, gate_1)["stale_reason"]
    assert _gate(db, gate_2)["stale_at"] is None, "only the covering task goes stale"
    assert _task_status(db, 1) == "pending"
    assert _task_status(db, 2) == "done"


def test_changing_a_covered_ac_through_a_brief_rewrite_marks_verdicts_stale(db):
    from gaia.briefs import upsert_brief
    from gaia.store.writer import link_task_criteria

    (gate_id,) = _seed(db)
    link_task_criteria(_WS, _BRIEF, 1, ["AC-1"], db_path=db)
    _pass(db, 1, gate_id)

    upsert_brief(_WS, _BRIEF, {
        "status": "open", "title": _BRIEF,
        "acceptance_criteria": [{"ac_id": "AC-1", "description": "rewritten"}],
    }, db_path=db)

    assert _gate(db, gate_id)["stale_at"] is not None


def test_a_stale_gate_keeps_an_override_closure(db):
    from gaia.store.writer import set_task_status, update_gate

    (gate_id,) = _seed(db)
    set_task_status(_WS, _BRIEF, 1, "done", override_reason="runner offline",
                    db_path=db)
    _pass(db, 1, gate_id)
    update_gate(_WS, _BRIEF, 1, gate_id, evidence_shape="changed", db_path=db)
    assert _gate(db, gate_id)["stale_at"] is not None
    assert _task_status(db, 1) == "done", "reopen preserves the override"


# ---------------------------------------------------------------------------
# 2-3. Structure (coverage, dependencies) and derived states.
# ---------------------------------------------------------------------------

def test_a_task_depending_on_an_unfinished_task_is_blocked(db):
    from gaia.briefs.store import derive_brief_state
    from gaia.store.writer import link_task_dependencies

    gate_1, _ = _seed(db, tasks=2)
    link_task_dependencies(_WS, _BRIEF, 2, [1], db_path=db)

    tasks = {t["order_num"]: t for t in derive_brief_state(_WS, _BRIEF, db_path=db)["tasks"]}
    assert tasks[2]["blocked"] is True and tasks[2]["blocked_by"] == [1]
    assert tasks[1]["blocked"] is False

    _pass(db, 1, gate_1)
    tasks = {t["order_num"]: t for t in derive_brief_state(_WS, _BRIEF, db_path=db)["tasks"]}
    assert tasks[1]["done"] is True
    assert tasks[2]["blocked"] is False


def test_structure_rejects_unknown_acs_self_and_foreign_dependencies(db):
    from gaia.store.writer import link_task_criteria, link_task_dependencies

    _seed(db, tasks=2)
    with pytest.raises(ValueError):
        link_task_criteria(_WS, _BRIEF, 1, ["AC-9"], db_path=db)
    with pytest.raises(ValueError):
        link_task_dependencies(_WS, _BRIEF, 1, [1], db_path=db)
    with pytest.raises(ValueError):
        link_task_dependencies(_WS, _BRIEF, 1, [7], db_path=db)


def test_structure_can_be_removed(db):
    from gaia.store.writer import link_task_criteria, list_plan_tasks

    _seed(db, acs=("AC-1", "AC-2"))
    link_task_criteria(_WS, _BRIEF, 1, ["AC-1", "AC-2"], db_path=db)
    link_task_criteria(_WS, _BRIEF, 1, ["AC-2"], remove=True, db_path=db)
    (task,) = list_plan_tasks(_WS, _BRIEF, db_path=db)
    assert task["covers"] == ["AC-1"]
    assert task["depends_on"] == []


def test_ac_is_done_only_when_covering_tasks_are_done_and_evidence_exists(db):
    from gaia.briefs.store import derive_brief_state
    from gaia.evidence.store import insert_evidence
    from gaia.store.writer import link_task_criteria

    (gate_id,) = _seed(db)
    link_task_criteria(_WS, _BRIEF, 1, ["AC-1"], db_path=db)
    brief_id = _brief_id(db)

    def ac_state():
        state = derive_brief_state(_WS, _BRIEF, db_path=db)
        return state["acceptance_criteria"][0], state["ready_to_close"]

    assert ac_state()[0]["done"] is False
    assert ac_state()[0]["covered_by"] == [1]

    _pass(db, 1, gate_id)
    assert ac_state()[0]["done"] is False, "no evidence yet"

    insert_evidence(_WS, brief_id, "AC-1", type="text", text="refutes",
                    polarity="negative", db_path=db)
    assert ac_state()[0]["done"] is False, "negative evidence does not accept"

    insert_evidence(_WS, brief_id, "AC-1", type="text", text="pytest 3 passed",
                    gate_id=gate_id, db_path=db)
    ac, ready = ac_state()
    assert ac["done"] is True
    assert ready is True


def test_brief_is_ready_when_every_ac_is_done_or_descoped(db):
    from gaia.briefs.store import derive_brief_state
    from gaia.store.writer import set_ac_status

    _seed(db, acs=("AC-1",))
    assert derive_brief_state(_WS, _BRIEF, db_path=db)["ready_to_close"] is False
    set_ac_status(_WS, _BRIEF, "AC-1", "descoped", db_path=db)
    assert derive_brief_state(_WS, _BRIEF, db_path=db)["ready_to_close"] is True


def test_verify_reports_an_uncovered_ac_and_a_stale_verdict(db):
    from gaia.briefs.store import verify_brief
    from gaia.store.writer import link_task_criteria, update_gate

    (gate_id,) = _seed(db, acs=("AC-1", "AC-2"))
    link_task_criteria(_WS, _BRIEF, 1, ["AC-1"], db_path=db)
    _pass(db, 1, gate_id)
    update_gate(_WS, _BRIEF, 1, gate_id, evidence_shape="changed", db_path=db)

    kinds = {i["kind"]: i["detail"] for i in verify_brief(_WS, _BRIEF, db_path=db)["inconsistencies"]}
    assert "AC-2" in kinds["uncovered_ac"]
    assert "AC-1" not in kinds["uncovered_ac"]
    assert str(gate_id) in kinds["stale_gate_verdict"]


# ---------------------------------------------------------------------------
# 4. Evidence and failure cause.
# ---------------------------------------------------------------------------

def _brief_id(db: Path) -> int:
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT id FROM briefs WHERE name = ?", (_BRIEF,)).fetchone()[0]
    finally:
        con.close()


def test_ac_cannot_be_set_done_without_positive_evidence(db):
    from gaia.evidence.store import insert_evidence
    from gaia.store.writer import set_ac_status

    _seed(db)
    with pytest.raises(ValueError, match="evidence"):
        set_ac_status(_WS, _BRIEF, "AC-1", "done", db_path=db)
    insert_evidence(_WS, _brief_id(db), "AC-1", type="text", text="no",
                    polarity="negative", db_path=db)
    with pytest.raises(ValueError, match="evidence"):
        set_ac_status(_WS, _BRIEF, "AC-1", "done", db_path=db)
    insert_evidence(_WS, _brief_id(db), "AC-1", type="text", text="yes", db_path=db)
    assert set_ac_status(_WS, _BRIEF, "AC-1", "done", db_path=db)["new_status"] == "done"


def test_evidence_binds_to_a_gate_of_the_same_brief_only(db):
    from gaia.briefs import upsert_brief
    from gaia.evidence.store import insert_evidence

    (gate_id,) = _seed(db)
    row = insert_evidence(_WS, _brief_id(db), "AC-1", type="text", text="x",
                          gate_id=gate_id, polarity="negative", db_path=db)
    assert row["gate_id"] == gate_id and row["polarity"] == "negative"

    upsert_brief(_WS, "other-brief", {"status": "open", "title": "o"}, db_path=db)
    con = sqlite3.connect(str(db))
    other_id = con.execute("SELECT id FROM briefs WHERE name='other-brief'").fetchone()[0]
    con.close()
    with pytest.raises(ValueError, match="gate"):
        insert_evidence(_WS, other_id, "AC-1", type="text", text="x",
                        gate_id=gate_id, db_path=db)
    with pytest.raises(ValueError, match="polarity"):
        insert_evidence(_WS, _brief_id(db), "AC-1", type="text", text="x",
                        polarity="maybe", db_path=db)


def test_a_failed_gate_records_its_cause(db):
    from gaia.store.writer import set_gate_status

    (gate_id,) = _seed(db)
    assert _cli("task", ["task", "gate", "set-status", _BRIEF, "1", str(gate_id),
                         "fail"]) != 0, "the agent surface requires a cause"
    assert _gate(db, gate_id)["status"] == "pending"
    with pytest.raises(ValueError, match="cause"):
        set_gate_status(_WS, _BRIEF, 1, gate_id, "fail", cause="bad luck", db_path=db)
    set_gate_status(_WS, _BRIEF, 1, gate_id, "fail", cause="environment", db_path=db)
    assert _gate(db, gate_id)["fail_cause"] == "environment"
    with pytest.raises(ValueError, match="cause"):
        set_gate_status(_WS, _BRIEF, 1, gate_id, "pass", cause="product", db_path=db)
    set_gate_status(_WS, _BRIEF, 1, gate_id, "pass", db_path=db)
    assert _gate(db, gate_id)["fail_cause"] is None


# ---------------------------------------------------------------------------
# 5. Brief decisions.
# ---------------------------------------------------------------------------

def test_a_new_decision_supersedes_an_old_one(db):
    from gaia.briefs import get_brief
    from gaia.briefs.store import add_decision

    _seed(db)
    first = add_decision(_WS, _BRIEF, "use sqlite", rationale="local", db_path=db)
    second = add_decision(_WS, _BRIEF, "use sqlite with WAL",
                          supersedes=first["id"], db_path=db)

    decisions = get_brief(_WS, _BRIEF, db_path=db)["decisions"]
    assert [d["id"] for d in decisions["current"]] == [second["id"]]
    assert [d["id"] for d in decisions["superseded"]] == [first["id"]]
    assert decisions["superseded"][0]["superseded_by"] == second["id"]

    with pytest.raises(ValueError):
        add_decision(_WS, _BRIEF, "again", supersedes=first["id"], db_path=db)
    with pytest.raises(ValueError):
        add_decision(_WS, _BRIEF, "ghost", supersedes=9999, db_path=db)


# ---------------------------------------------------------------------------
# CLI verbs.
# ---------------------------------------------------------------------------

def test_cli_verbs_for_structure_cause_evidence_and_decisions(db, capsys):
    (gate_id,) = _seed(db, tasks=1, acs=("AC-1", "AC-2"))
    from gaia.store.writer import add_task_to_plan

    add_task_to_plan(_WS, _BRIEF, 2, "task 2", db_path=db)

    assert _cli("task", ["task", "cover", _BRIEF, "1", "AC-1", "AC-2"]) == 0
    assert _cli("task", ["task", "depend", _BRIEF, "2", "1"]) == 0
    assert _cli("task", ["task", "gate", "set-status", _BRIEF, "1", str(gate_id),
                         "fail"]) != 0
    assert _cli("task", ["task", "gate", "set-status", _BRIEF, "1", str(gate_id),
                         "fail", "--cause", "broken_test"]) == 0
    assert _gate(db, gate_id)["fail_cause"] == "broken_test"
    assert _cli("evidence", ["evidence", "add", "--brief", _BRIEF, "--ac", "AC-1",
                             "--type", "text", "--text", "refuted",
                             "--gate", str(gate_id), "--negative"]) == 0
    assert _cli("brief", ["brief", "decision", "add", _BRIEF,
                          "--text", "keep it local"]) == 0
    capsys.readouterr()

    assert _cli("task", ["task", "list", _BRIEF, "--format", "json"]) == 0
    tasks = {t["order_num"]: t for t in json.loads(capsys.readouterr().out)}
    assert tasks[1]["covers"] == ["AC-1", "AC-2"]
    assert tasks[2]["depends_on"] == [1]

    assert _cli("brief", ["brief", "show", _BRIEF]) == 0
    out = capsys.readouterr().out
    assert "## Decisions" in out and "keep it local" in out


# ---------------------------------------------------------------------------
# 6. Migration: existing rows keep reading.
# ---------------------------------------------------------------------------

_V55_SUBSET = """
CREATE TABLE briefs (id INTEGER PRIMARY KEY AUTOINCREMENT, workspace TEXT, name TEXT);
CREATE TABLE acceptance_criteria (id INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id INTEGER NOT NULL, ac_id TEXT NOT NULL, description TEXT);
CREATE TABLE plans (id INTEGER PRIMARY KEY AUTOINCREMENT, brief_id INTEGER NOT NULL UNIQUE);
CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL,
    order_num INTEGER NOT NULL, goal TEXT, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE task_gates (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
    verification_type TEXT NOT NULL, evidence_type TEXT, evidence_shape TEXT,
    artifact_path TEXT, status TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, brief_id INTEGER NOT NULL,
    ac_id TEXT NOT NULL, task_id TEXT, type TEXT NOT NULL, text TEXT,
    artifact_path TEXT, size_bytes INTEGER, created_at TEXT, created_by_agent TEXT);
INSERT INTO briefs VALUES (1, 'me', 'old');
INSERT INTO plans VALUES (1, 1);
INSERT INTO tasks VALUES (1, 1, 1, 'old goal', 'done');
INSERT INTO task_gates VALUES (1, 1, 'command', NULL, 'pytest', NULL, 'pass');
INSERT INTO evidence VALUES (1, 1, 'AC-1', NULL, 'text', 'ok', NULL, NULL, NULL, NULL);
"""


def test_the_v56_migration_keeps_existing_rows_readable():
    migration = _REPO_ROOT / "scripts" / "migrations" / "v55_to_v56.sql"
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(_V55_SUBSET)
    con.executescript(migration.read_text())

    gate = dict(con.execute("SELECT * FROM task_gates").fetchone())
    assert gate["status"] == "pass"
    assert gate["stale_at"] is None and gate["fail_cause"] is None
    evidence = dict(con.execute("SELECT * FROM evidence").fetchone())
    assert evidence["polarity"] == "positive" and evidence["gate_id"] is None
    for table in ("task_acceptance_criteria", "task_dependencies", "brief_decisions"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
