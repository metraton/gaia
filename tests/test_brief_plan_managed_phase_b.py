"""Managed briefs and plans, phase B: pause, re-verification, plan history,
the managed change flow, and the orchestrator's lane over them.

Matchable as one suite::

    pytest tests/test_brief_plan_managed_phase_b.py -q

Like phase A, every test runs against a disposable substrate and goes through
the real writers, CLI handlers, birth validator and orchestrator guard.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
# Appended, not prepended: tests/cli would otherwise shadow bin/cli.
if str(_REPO_ROOT / "tests") not in sys.path:
    sys.path.append(str(_REPO_ROOT / "tests"))

from test_brief_plan_managed import (  # noqa: E402  (shared phase A helpers)
    _BRIEF,
    _WS,
    _cli,
    _gate,
    _pass,
    _seed,
    _task_status,
    db,  # noqa: F401  (fixture)
)


def _plan_row(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT p.* FROM plans p JOIN briefs b ON b.id = p.brief_id "
            "WHERE b.name = ?", (_BRIEF,)).fetchone())
    finally:
        con.close()


def _task_id(db: Path, order: int) -> int:
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT t.id FROM tasks t JOIN plans p ON p.id = t.plan_id "
            "JOIN briefs b ON b.id = p.brief_id WHERE b.name = ? AND t.order_num = ?",
            (_BRIEF, order)).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 1. Pause (AC-5): an active plan stops with a reason and resumes where it was.
# ---------------------------------------------------------------------------

def test_pausing_needs_a_reason_and_an_active_plan(db):
    from gaia.store.writer import pause_plan, set_plan_status

    _seed(db)
    with pytest.raises(ValueError, match="reason"):
        pause_plan(_WS, _BRIEF, "  ", db_path=db)

    set_plan_status(_WS, _BRIEF, "draft", db_path=db)
    with pytest.raises(ValueError, match="active"):
        pause_plan(_WS, _BRIEF, "waiting on the vendor", db_path=db)


def test_a_paused_plan_keeps_its_tasks_and_resumes_where_it_was(db):
    from gaia.store.writer import pause_plan, resume_plan, set_plan_status

    gate_1, _gate_2 = _seed(db, tasks=2)
    _pass(db, 1, gate_1)

    pause_plan(_WS, _BRIEF, "waiting on the vendor", db_path=db)
    plan = _plan_row(db)
    assert plan["status"] == "active", "paused means approved-but-stopped"
    assert plan["pause_reason"] == "waiting on the vendor"
    assert plan["paused_at"] is not None
    with pytest.raises(ValueError, match="paused"):
        set_plan_status(_WS, _BRIEF, "closed", db_path=db)

    resume_plan(_WS, _BRIEF, db_path=db)
    plan = _plan_row(db)
    assert plan["paused_at"] is None and plan["pause_reason"] is None
    assert _task_status(db, 1) == "done"
    assert _task_status(db, 2) == "pending"


def test_a_paused_plan_refuses_the_birth_of_a_task_execution(db):
    from gaia.store.writer import pause_plan, resume_plan
    from modules.agents.dispatch_binding import (
        DEGRADABLE_BINDING_REASONS,
        DispatchBindingError,
        validate_dispatch_binding,
    )

    _seed(db)
    task_id = _task_id(db, 1)
    pause_plan(_WS, _BRIEF, "waiting on the vendor", db_path=db)

    with pytest.raises(DispatchBindingError) as exc:
        validate_dispatch_binding(kind="task_execution", plan_task_id=task_id,
                                  db_path=db)
    assert exc.value.reason == "plan_task_id_plan_paused"
    assert "waiting on the vendor" in str(exc.value)
    assert exc.value.reason in DEGRADABLE_BINDING_REASONS

    resume_plan(_WS, _BRIEF, db_path=db)
    validate_dispatch_binding(kind="task_execution", plan_task_id=task_id,
                              db_path=db)


# ---------------------------------------------------------------------------
# 2. Re-verification (AC-1, second trigger).
# ---------------------------------------------------------------------------

def test_reverification_keeps_the_verdict_marks_it_stale_and_reopens(db):
    from gaia.store.writer import request_gate_reverification

    (gate_id,) = _seed(db)
    _pass(db, 1, gate_id)
    assert _task_status(db, 1) == "done"

    res = request_gate_reverification(_WS, _BRIEF, 1, gate_id,
                                      "suspect flaky runner", db_path=db)

    gate = _gate(db, gate_id)
    assert gate["status"] == "pass", "the old verdict is kept"
    assert gate["stale_at"] is not None
    assert "suspect flaky runner" in gate["stale_reason"]
    assert _task_status(db, 1) == "pending"
    assert res["derived_closure"]["action"] == "reopen"

    _pass(db, 1, gate_id)
    assert _gate(db, gate_id)["stale_at"] is None
    assert _task_status(db, 1) == "done"


def test_reverification_needs_an_approved_gate_and_a_reason(db):
    from gaia.store.writer import request_gate_reverification

    (gate_id,) = _seed(db)
    with pytest.raises(ValueError, match="pass"):
        request_gate_reverification(_WS, _BRIEF, 1, gate_id, "why", db_path=db)
    _pass(db, 1, gate_id)
    with pytest.raises(ValueError, match="reason"):
        request_gate_reverification(_WS, _BRIEF, 1, gate_id, "", db_path=db)


# ---------------------------------------------------------------------------
# 3. Plan history (decision 5).
# ---------------------------------------------------------------------------

def test_resaving_an_existing_plan_keeps_the_previous_version_and_its_reason(db, capsys):
    from gaia.store.writer import get_plan, get_plan_history, upsert_plan

    _seed(db)
    upsert_plan(_WS, _BRIEF, content="plan v2", status="active",
                reason="split task 1", db_path=db)
    upsert_plan(_WS, _BRIEF, content="plan v2", status="active", db_path=db)

    history = get_plan_history(_WS, _BRIEF, db_path=db)
    assert [(v["version"], v["content"], v["reason"]) for v in history] == [
        (1, "plan", "split task 1"),
    ], "an unchanged re-save records no version"
    assert get_plan(_WS, _BRIEF, db_path=db)["version"] == 2

    assert _cli("plan", ["plan", "history", _BRIEF]) == 0
    out = capsys.readouterr().out
    assert "v1" in out and "split task 1" in out and "v2 (current)" in out


def test_the_cli_refuses_a_content_change_without_a_reason(db):
    _seed(db)
    assert _cli("plan", ["plan", "save", "--brief", _BRIEF, "--content", "other"]) == 1
    assert _cli("plan", ["plan", "save", "--brief", _BRIEF, "--content", "other",
                         "--reason", "rewrote the approach"]) == 0


# ---------------------------------------------------------------------------
# 4. Managed change (AC-4, decision 4).
# ---------------------------------------------------------------------------

def test_an_applied_change_versions_the_plan_and_stales_only_affected_tasks(db):
    from gaia.store.writer import (
        apply_plan_change,
        approve_plan_change,
        get_plan,
        get_plan_history,
        list_plan_changes,
        propose_plan_change,
        request_plan_change,
    )

    gate_1, gate_2 = _seed(db, tasks=2)
    _pass(db, 1, gate_1)
    _pass(db, 2, gate_2)

    with pytest.raises(ValueError, match="justification"):
        request_plan_change(_WS, _BRIEF, "", db_path=db)
    change_id = request_plan_change(
        _WS, _BRIEF, "the API moved to v2", db_path=db)["change_id"]
    with pytest.raises(ValueError, match="open change"):
        request_plan_change(_WS, _BRIEF, "another", db_path=db)
    with pytest.raises(ValueError, match="proposed"):
        approve_plan_change(_WS, _BRIEF, change_id, db_path=db)

    propose_plan_change(_WS, _BRIEF, change_id, "re-point task 2 at v2",
                        [(2, "calls the old endpoint")], db_path=db)
    with pytest.raises(ValueError, match="approved"):
        apply_plan_change(_WS, _BRIEF, change_id, db_path=db)
    approve_plan_change(_WS, _BRIEF, change_id, db_path=db)

    res = apply_plan_change(_WS, _BRIEF, change_id, content="plan for v2",
                            db_path=db)

    assert res["version"] == 2
    plan = get_plan(_WS, _BRIEF, db_path=db)
    assert plan["content"] == "plan for v2" and plan["version"] == 2
    (old,) = get_plan_history(_WS, _BRIEF, db_path=db)
    assert old["content"] == "plan" and "the API moved to v2" in old["reason"]
    assert old["change_id"] == change_id

    assert _task_status(db, 1) == "done", "an untouched verified task stays done"
    assert _gate(db, gate_1)["stale_at"] is None
    assert _task_status(db, 2) == "pending"
    assert "calls the old endpoint" in _gate(db, gate_2)["stale_reason"]

    (change,) = list_plan_changes(_WS, _BRIEF, db_path=db)
    assert change["status"] == "applied"
    assert change["tasks"] == [{"order_num": 2, "reason": "calls the old endpoint"}]


def test_the_v57_migration_keeps_existing_plans_readable():
    migration = _REPO_ROOT / "scripts" / "migrations" / "v56_to_v57.sql"
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(
        "CREATE TABLE briefs (id INTEGER PRIMARY KEY);"
        "CREATE TABLE plans (id INTEGER PRIMARY KEY, brief_id INTEGER, status TEXT,"
        " content TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE tasks (id INTEGER PRIMARY KEY, plan_id INTEGER);"
        "INSERT INTO briefs VALUES (1);"
        "INSERT INTO plans VALUES (1, 1, 'active', 'old plan', 'c', 'u');"
    )
    con.executescript(migration.read_text())

    plan = dict(con.execute("SELECT * FROM plans").fetchone())
    assert plan["status"] == "active" and plan["content"] == "old plan"
    assert plan["paused_at"] is None and plan["pause_reason"] is None
    for table in ("plan_versions", "plan_changes", "plan_change_tasks"):
        assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 5. The orchestrator's lane (AC-7).
# ---------------------------------------------------------------------------

_GAIA = "/abs/path/bin/gaia"


@pytest.fixture()
def guard(monkeypatch):
    from modules.security import gaia_cli_only_guard as g

    monkeypatch.setattr(g, "is_trusted_gaia_binary", lambda token: token == _GAIA)
    return g


@pytest.mark.parametrize("command", [
    "plan pause my-brief --reason=vendor",
    "plan resume my-brief",
    "plan history my-brief",
    "plan change list my-brief",
    "plan change request my-brief --reason=api-moved",
    "plan change approve my-brief 4",
    "task gate reverify my-brief 1 3 --reason=flaky",
    "brief decision add my-brief --text=keep-sqlite",
])
def test_the_orchestrator_may_coordinate_the_plan(guard, command):
    allowed, reason = guard.check(f"{_GAIA} {command}", {})
    assert allowed is True, reason


@pytest.mark.parametrize("command", [
    "task gate set-status my-brief 1 3 pass",
    "task gate set-status my-brief 1 3 fail --cause=product",
    "task set-status my-brief 1 done --override --reason=x",
    "evidence add my-brief AC-1 --type=text --text=x",
    "ac set-status my-brief AC-1 done",
    "plan change propose my-brief 4 --summary=x --affects=2:y",
    "plan change apply my-brief 4",
    "task cover my-brief 1 AC-1",
    "task depend my-brief 2 1",
    "plan pause my-brief",
    "task gate reverify my-brief 1 3",
    "plan change request my-brief",
])
def test_the_orchestrator_may_not_judge_close_or_restructure(guard, command):
    allowed, _reason = guard.check(f"{_GAIA} {command}", {})
    assert allowed is False


def _epilog_section(start: str, end: str) -> str:
    text = (_REPO_ROOT / "bin" / "gaia").read_text(encoding="utf-8")
    epilog = text.split('_EPILOG = """', 1)[1].split('"""', 1)[0]
    return epilog.split(start, 1)[1].split(end, 1)[0]


def test_the_help_epilog_lists_the_new_lane():
    mine = _epilog_section("WRITE the orchestrator owns", "WRITE a specialist owns")
    for verb in ("pause", "resume", "change request|approve", "gate reverify",
                 "decision add"):
        assert verb in mine, verb
    reads = _epilog_section("READ -- changes nothing", "WRITE the orchestrator owns")
    assert "history" in reads and "change list" in reads
    theirs = _epilog_section("WRITE a specialist owns", "The READ lane")
    for verb in ("change propose|apply", "cover", "depend"):
        assert verb in theirs, verb


def test_brief_verify_recommends_only_verbs_in_the_orchestrators_lane(db, guard):
    from gaia.briefs.store import set_status_brief, verify_brief

    _seed(db, acs=("AC-1", "AC-2"))
    set_status_brief(_WS, _BRIEF, "in-progress", db_path=db)
    set_status_brief(_WS, _BRIEF, "closed", db_path=db)

    details = [i["detail"] for i in
               verify_brief(_WS, _BRIEF, db_path=db)["inconsistencies"]]
    assert any("AC-1" in d and "closed" in d for d in details)
    assert not any("ac set-status" in d for d in details)
    commands = [c for d in details for c in re.findall(r"`gaia ([^`]+)`", d)]
    for command in commands:
        tokens = tuple(command.split())
        assert guard.match_allowed_phrase(tokens, guard.ALLOWED_PHRASES), command


# ---------------------------------------------------------------------------
# 6. Final adjustments: open-question marks and the stdin plan body.
# ---------------------------------------------------------------------------

def test_brief_verify_reports_every_field_still_marked_falta_aclarar(db):
    from gaia.briefs import upsert_brief
    from gaia.briefs.store import verify_brief

    _seed(db, acs=("AC-1", "AC-2"))
    upsert_brief(_WS, _BRIEF, {
        "title": _BRIEF,
        "objective": "FALTA ACLARAR: which tenants?",
        "context": "settled",
        "approach": "FALTA ACLARAR: sync or async?",
        "out_of_scope": "FALTA ACLARAR: the admin UI?",
        "acceptance_criteria": [
            {"ac_id": "AC-1", "description": "FALTA ACLARAR: what latency?"},
            {"ac_id": "AC-2", "description": "export holds"},
        ],
    }, db_path=db)

    result = verify_brief(_WS, _BRIEF, db_path=db)
    marked = [i["detail"] for i in result["inconsistencies"]
              if i["kind"] == "unresolved_clarification"]
    assert len(marked) == 4, marked
    for where in ("objective", "approach", "out_of_scope", "AC-1"):
        assert any(where in d for d in marked), where
    assert not any("context" in d or "AC-2" in d for d in marked)
    assert result["pass"] is False

    upsert_brief(_WS, _BRIEF, {
        "title": _BRIEF, "objective": "all tenants", "context": "settled",
        "approach": "async", "out_of_scope": "the admin UI",
        "acceptance_criteria": [
            {"ac_id": "AC-1", "description": "p95 under 200 ms"},
            {"ac_id": "AC-2", "description": "export holds"},
        ],
    }, db_path=db)
    kinds = {i["kind"] for i in
             verify_brief(_WS, _BRIEF, db_path=db)["inconsistencies"]}
    assert "unresolved_clarification" not in kinds


def test_save_and_apply_read_the_plan_body_from_stdin(db, monkeypatch):
    import io

    from gaia.store.writer import (
        approve_plan_change,
        get_plan,
        propose_plan_change,
        request_plan_change,
    )

    _seed(db)
    body = "## Plan\n\nquotes ' \" backticks ` and $HOME stay literal\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(body))
    assert _cli("plan", ["plan", "save", "--brief", _BRIEF, "--content-file", "-",
                         "--reason", "full plan body"]) == 0
    assert get_plan(_WS, _BRIEF, db_path=db)["content"] == body

    change_id = request_plan_change(_WS, _BRIEF, "scope moved", db_path=db)["change_id"]
    propose_plan_change(_WS, _BRIEF, change_id, "re-point task 1",
                        [(1, "scope moved")], db_path=db)
    approve_plan_change(_WS, _BRIEF, change_id, db_path=db)
    monkeypatch.setattr("sys.stdin", io.StringIO(body + "v2\n"))
    assert _cli("plan", ["plan", "change", "apply", _BRIEF, str(change_id),
                         "--content-file", "-"]) == 0
    assert get_plan(_WS, _BRIEF, db_path=db)["content"] == body + "v2\n"
