"""
gate 39 (plan 34 task 6) -- referential integrity of the nascent-row binding.

At dispatch the hook births the nascent row FROM the dispatch metadata, but a
binding that does not resolve must be REJECTED before the row is born. This is
referential integrity of the row's coordinates -- NOT semantic gating by
``kind``. The rules (modules.agents.dispatch_binding):

  * kind='task_execution' with a plan_task_id that does NOT resolve to a
    tasks.id (or resolves to a non-dispatchable task) is REJECTED, and no row is
    born.
  * a VALID, dispatchable plan_task_id ATA (binds) the row to that tasks.id --
    the row is born DISPATCHED with the coordinate stamped.
  * a verifier turn (turn_role='verifier') REQUIRES a parent_handoff_id that
    resolves to an agent_contract_handoffs.id; an unresolved / missing parent is
    REJECTED.
  * kind is a PURE LABEL -- no value of kind is ever rejected for its value; an
    arbitrary label with a sound binding is born without complaint.

Runs against a FRESH DB; the writer's _connect materializes the real v37 schema.
FK targets (briefs -> plans(34) -> tasks) seeded via raw sqlite.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.store.writer import (
    agent_contract_handoff_state,
    finalize_agent_contract_handoff,
)
from modules.agents.dispatch_binding import (
    DispatchBindingError,
    birth_dispatched_row,
    validate_dispatch_binding,
)

WORKSPACE = "me"
AGENT_ID = "a1234abcd"
PLAN_ID = 34
TASK_PENDING = 43     # dispatchable
TASK_DONE = 44        # terminal -> not dispatchable
MISSING_TASK = 999    # never seeded -> unresolved


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _envelope(state: str = "COMPLETE") -> str:
    return json.dumps({
        "agent_status": {
            "agent_state": state, "agent_id": AGENT_ID,
            "pending_steps": [], "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["ri"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    })


def _seed(db_path: Path) -> int:
    """Seed the FK chain + a real parent handoff row. Returns the parent id.

    briefs(1) -> plans(34) -> tasks(43 pending, 44 done). The parent handoff is a
    real finalized row so parent_handoff_id can point at a resolvable id.
    """
    parent = finalize_agent_contract_handoff(
        contract_id="a1234abcd.parent", agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=_envelope("COMPLETE"), db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contrato-binding", "in-progress"),
        )
        con.execute(
            "INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
            (PLAN_ID, 1, "active"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) VALUES (?,?,?,?,?)",
            (TASK_PENDING, PLAN_ID, 6, "dispatch hook", "pending"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) VALUES (?,?,?,?,?)",
            (TASK_DONE, PLAN_ID, 5, "prior task", "done"),
        )
        con.commit()
    finally:
        con.close()
    return parent["handoff_id"]


def _count(db_path: Path, contract_id: str) -> int:
    con = sqlite3.connect(str(db_path))
    try:
        return con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# task_execution: plan_task_id MUST resolve to a DISPATCHABLE task
# ---------------------------------------------------------------------------

def test_task_execution_missing_plan_task_id_rejected(db):
    _seed(db)
    with pytest.raises(DispatchBindingError) as ei:
        birth_dispatched_row(
            contract_id="a1234abcd.no-ptid", agent_id=AGENT_ID, workspace=WORKSPACE,
            kind="task_execution", plan_id=PLAN_ID, db_path=db,
        )
    assert ei.value.reason == "task_execution_requires_plan_task_id"
    assert _count(db, "a1234abcd.no-ptid") == 0, "a rejected binding births no row"


def test_task_execution_unresolved_plan_task_id_rejected(db):
    _seed(db)
    with pytest.raises(DispatchBindingError) as ei:
        birth_dispatched_row(
            contract_id="a1234abcd.bad-ptid", agent_id=AGENT_ID, workspace=WORKSPACE,
            kind="task_execution", plan_task_id=MISSING_TASK, plan_id=PLAN_ID, db_path=db,
        )
    assert ei.value.reason == "plan_task_id_unresolved"
    assert _count(db, "a1234abcd.bad-ptid") == 0


def test_task_execution_non_dispatchable_task_rejected(db):
    _seed(db)
    with pytest.raises(DispatchBindingError) as ei:
        birth_dispatched_row(
            contract_id="a1234abcd.done-ptid", agent_id=AGENT_ID, workspace=WORKSPACE,
            kind="task_execution", plan_task_id=TASK_DONE, plan_id=PLAN_ID, db_path=db,
        )
    assert ei.value.reason == "plan_task_id_not_dispatchable"
    assert _count(db, "a1234abcd.done-ptid") == 0


def test_valid_plan_task_id_binds_the_row(db):
    _seed(db)
    cid = "a1234abcd.good-ptid"
    out = birth_dispatched_row(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        kind="task_execution", plan_task_id=TASK_PENDING, plan_id=PLAN_ID, db_path=db,
    )
    assert out["created"] is True
    assert agent_contract_handoff_state(cid, db_path=db) == "DISPATCHED"
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT plan_task_id, kind FROM agent_contract_handoffs WHERE contract_id=?",
            (cid,),
        ).fetchone()
    finally:
        con.close()
    assert r["plan_task_id"] == TASK_PENDING, "a valid plan_task_id ATA la fila"
    assert r["kind"] == "task_execution"


# ---------------------------------------------------------------------------
# verifier turn: parent_handoff_id MUST resolve
# ---------------------------------------------------------------------------

def test_verifier_requires_parent_handoff_id(db):
    _seed(db)
    with pytest.raises(DispatchBindingError) as ei:
        birth_dispatched_row(
            contract_id="a1234abcd.verif-noparent", agent_id=AGENT_ID,
            workspace=WORKSPACE, kind="verifier", turn_role="verifier", db_path=db,
        )
    assert ei.value.reason == "verifier_requires_parent_handoff_id"
    assert _count(db, "a1234abcd.verif-noparent") == 0


def test_verifier_unresolved_parent_handoff_id_rejected(db):
    _seed(db)
    with pytest.raises(DispatchBindingError) as ei:
        birth_dispatched_row(
            contract_id="a1234abcd.verif-badparent", agent_id=AGENT_ID,
            workspace=WORKSPACE, kind="verifier", turn_role="verifier",
            parent_handoff_id=987654, db_path=db,
        )
    assert ei.value.reason == "parent_handoff_id_unresolved"
    assert _count(db, "a1234abcd.verif-badparent") == 0


def test_verifier_resolvable_parent_binds_the_row(db):
    parent_id = _seed(db)
    cid = "a1234abcd.verif-ok"
    out = birth_dispatched_row(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        kind="verifier", turn_role="verifier", parent_handoff_id=parent_id, db_path=db,
    )
    assert out["created"] is True
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        r = con.execute(
            "SELECT parent_handoff_id, agent_state FROM agent_contract_handoffs "
            "WHERE contract_id=?", (cid,),
        ).fetchone()
    finally:
        con.close()
    assert r["parent_handoff_id"] == parent_id
    assert r["agent_state"] == "DISPATCHED"


# ---------------------------------------------------------------------------
# kind is a PURE LABEL -- never rejected for its value
# ---------------------------------------------------------------------------

def test_kind_is_a_pure_label_no_rejection_by_value(db):
    _seed(db)
    # An arbitrary, never-enumerated kind with NO binding coordinates: no
    # task_execution requirement, no verifier requirement -> born without
    # complaint. kind names the turn; it is not validated for its value.
    for i, label in enumerate(["investigation", "memory", "totally-made-up-kind"]):
        cid = f"a1234abcd.label-{i}"
        out = birth_dispatched_row(
            contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
            kind=label, db_path=db,
        )
        assert out["created"] is True, f"kind={label!r} must not be rejected"
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        try:
            r = con.execute(
                "SELECT kind FROM agent_contract_handoffs WHERE contract_id=?",
                (cid,),
            ).fetchone()
        finally:
            con.close()
        assert r["kind"] == label


def test_validate_only_no_side_effects(db):
    """validate_dispatch_binding is read-only -- a sound binding validates and
    births nothing on its own (birth is a separate, explicit step)."""
    parent_id = _seed(db)
    # sound task_execution
    validate_dispatch_binding(
        kind="task_execution", plan_task_id=TASK_PENDING, db_path=db,
    )
    # sound verifier
    validate_dispatch_binding(
        kind="verifier", turn_role="verifier", parent_handoff_id=parent_id, db_path=db,
    )
    # pure label
    validate_dispatch_binding(kind="anything-goes", db_path=db)
