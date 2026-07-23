"""
gate 37 (plan 34 task 5) -- born-at-dispatch writer lifecycle.

The writer half of the "born-at-dispatch" redesign (brief
contrato-binding-y-verificacion-por-task-id). A handoff row is BORN at dispatch
carrying agent_state='DISPATCHED' + the four binding coordinates, then
CONVERGES to a terminal verdict on finalize -- one row per turn, exactly once.

Clauses:
  (a) insert_dispatched_handoff births a nascent row: agent_state='DISPATCHED'
      AND the binding (plan_task_id, plan_id, parent_handoff_id, kind).
  (b) finalize CONVERGES the nascent row: exactly one row per turn, no duplicate
      INSERT, the binding + birth created_at preserved, agent_state now terminal.
  (c) the finalize-vs-degraded race (two convergent writers on the SAME born
      row, one COMPLETE, one degraded IN_PROGRESS) converges to EXACTLY ONE row
      per contract_id -- the exactly-once invariant, in either arrival order.

Runs against a FRESH DB; the writer's own ``_connect`` materializes the real
v37 schema (agent_state CHECK incl. DISPATCHED, contract_id UNIQUE, born-at-
dispatch binding FKs) from ``gaia/store/schema.sql`` -- not a fixture. The FK
targets (briefs/plans/tasks/parent handoff) are seeded via raw sqlite so the
PRESENT binding satisfies referential integrity (foreign_keys=ON at runtime).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from gaia.store.writer import (
    agent_contract_handoff_state,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
)

WORKSPACE = "me"
AGENT_ID = "a1234abcd"

# Mirror the real plan/task ids from the brief (plan 34 / task 42).
PLAN_ID = 34
TASK_ID = 42


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """An isolated DB path; the writer materializes the real v37 schema."""
    return tmp_path / "gaia.db"


def _envelope(state: str = "COMPLETE") -> str:
    return json.dumps({
        "agent_status": {
            "agent_state": state,
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["born-at-dispatch"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    })


def _seed_binding_targets(db_path: Path) -> int:
    """Materialize the schema + seed the born-at-dispatch binding FK targets.

    A first legitimate finalize materializes the schema and the workspaces row
    (and gives us a real parent handoff row to point parent_handoff_id at).
    Then raw INSERTs create the briefs -> plans(id=34) -> tasks(id=42) chain so
    a PRESENT binding satisfies the runtime FKs. Returns the parent handoff id.
    """
    parent = finalize_agent_contract_handoff(
        contract_id="a1234abcd.parent-seed",
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=_envelope("COMPLETE"),
        db_path=db_path,
    )
    parent_handoff_id = parent["handoff_id"]

    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contrato-binding-y-verificacion-por-task-id", "in-progress"),
        )
        con.execute(
            "INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
            (PLAN_ID, 1, "active"),
        )
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 5, "born-at-dispatch writer lifecycle", "pending"),
        )
        con.commit()
    finally:
        con.close()
    return parent_handoff_id


def _row(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, contract_id, agent_state, plan_task_id, plan_id, "
            "parent_handoff_id, kind, created_at, raw_handoff_json "
            "FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# (a) nascent-row primitive: INSERT DISPATCHED + binding
# ---------------------------------------------------------------------------

def test_insert_dispatched_births_nascent_row_with_binding(db):
    parent_handoff_id = _seed_binding_targets(db)
    cid = "a1234abcd.born"

    out = insert_dispatched_handoff(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        plan_task_id=TASK_ID,
        plan_id=PLAN_ID,
        parent_handoff_id=parent_handoff_id,
        kind="task_execution",
        db_path=db,
    )
    assert out["status"] == "applied"
    assert out["created"] is True
    assert out["handoff_id"] is not None

    rows = _row(db, cid)
    assert len(rows) == 1, "birth must create exactly one row"
    r = rows[0]
    # ROW state DISPATCHED (never an envelope value -- a born, not-yet-final row).
    assert r["agent_state"] == "DISPATCHED"
    # The four binding coordinates are stamped at birth.
    assert r["plan_task_id"] == TASK_ID
    assert r["plan_id"] == PLAN_ID
    assert r["parent_handoff_id"] == parent_handoff_id
    assert r["kind"] == "task_execution"
    # The state helper agrees.
    assert agent_contract_handoff_state(cid, db_path=db) == "DISPATCHED"


def test_insert_dispatched_is_idempotent(db):
    _seed_binding_targets(db)
    cid = "a1234abcd.born-twice"
    first = insert_dispatched_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution", db_path=db,
    )
    second = insert_dispatched_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution", db_path=db,
    )
    assert first["created"] is True
    assert second["created"] is False, "re-birth of the same contract_id is a no-op"
    assert first["handoff_id"] == second["handoff_id"]
    assert len(_row(db, cid)) == 1, "re-birth must not duplicate the nascent row"


def test_insert_dispatched_requires_contract_id(db):
    with pytest.raises(ValueError):
        insert_dispatched_handoff(
            contract_id="", agent_id=AGENT_ID, workspace=WORKSPACE, db_path=db,
        )


# ---------------------------------------------------------------------------
# (b) finalize converges onto the nascent row (one row per turn, no duplicate)
# ---------------------------------------------------------------------------

def test_finalize_converges_on_nascent_row(db):
    parent_handoff_id = _seed_binding_targets(db)
    cid = "a1234abcd.converge"

    born = insert_dispatched_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID,
        parent_handoff_id=parent_handoff_id, kind="task_execution", db_path=db,
    )
    born_id = born["handoff_id"]
    born_created_at = _row(db, cid)[0]["created_at"]

    out = finalize_agent_contract_handoff(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=_envelope("COMPLETE"),
        db_path=db,
    )
    # finalize CONVERGED the nascent row (created=True for the converging call).
    assert out["created"] is True
    # It is the SAME physical row -- no duplicate INSERT.
    assert out["handoff_id"] == born_id

    rows = _row(db, cid)
    assert len(rows) == 1, "finalize must converge, not duplicate -- one row per turn"
    r = rows[0]
    assert r["agent_state"] == "COMPLETE", "the row converged to the terminal verdict"
    # The born-at-dispatch binding survives convergence untouched.
    assert r["plan_task_id"] == TASK_ID
    assert r["plan_id"] == PLAN_ID
    assert r["parent_handoff_id"] == parent_handoff_id
    assert r["kind"] == "task_execution"
    # The birth created_at is preserved (not overwritten on convergence).
    assert r["created_at"] == born_created_at
    # And the raw envelope was updated to the finalized one.
    assert json.loads(r["raw_handoff_json"])["agent_status"]["agent_state"] == "COMPLETE"


def test_finalize_without_nascent_row_inserts_fresh(db):
    """Legacy / no-born-at-dispatch path: finalize with no nascent row INSERTs a
    fresh terminal row (created=True), one row -- back-compat preserved."""
    _seed_binding_targets(db)
    cid = "a1234abcd.no-born"
    out = finalize_agent_contract_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=_envelope("COMPLETE"), db_path=db,
    )
    assert out["created"] is True
    rows = _row(db, cid)
    assert len(rows) == 1
    assert rows[0]["agent_state"] == "COMPLETE"
    # No binding was stamped (there was no born row) -- NULLs, not a crash.
    assert rows[0]["plan_task_id"] is None
    assert rows[0]["plan_id"] is None


def test_second_finalize_after_convergence_is_noop(db):
    """Once a nascent row has converged to terminal, a second finalize (a retry)
    is a genuine no-op -- write-once for terminal, exactly-once preserved."""
    _seed_binding_targets(db)
    cid = "a1234abcd.reconverge"
    insert_dispatched_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution", db_path=db,
    )
    first = finalize_agent_contract_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=_envelope("COMPLETE"), db_path=db,
    )
    second = finalize_agent_contract_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="IN_PROGRESS", raw_handoff_json=_envelope("IN_PROGRESS"), db_path=db,
    )
    assert first["created"] is True
    assert second["created"] is False, "a terminal row is never edited in place"
    assert first["handoff_id"] == second["handoff_id"]
    rows = _row(db, cid)
    assert len(rows) == 1
    # The winner's terminal verdict survives; the loser did NOT overwrite it.
    assert rows[0]["agent_state"] == "COMPLETE"


# ---------------------------------------------------------------------------
# (c) finalize-vs-degraded race -> exactly one row per contract_id
# ---------------------------------------------------------------------------

def test_finalize_vs_degraded_race_converges_to_one_row(db):
    """Two convergent writers on the SAME born nascent row -- the agent's
    COMPLETE finalize and a degraded IN_PROGRESS finalize -- released together
    by a barrier. Exactly one converges the DISPATCHED row (created=True); the
    other finds it terminal and is a no-op (created=False). Exactly ONE row, no
    deadlock, in either arrival order (repeated across rounds)."""
    for round_i in range(12):
        cid = f"a1234abcd.race-{round_i}"
        # Fresh binding targets are shared across rounds (seed once).
        if round_i == 0:
            _seed_binding_targets(db)
        insert_dispatched_handoff(
            contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
            plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution", db_path=db,
        )

        barrier = threading.Barrier(2)
        results: dict = {}
        errors: list = []
        lock = threading.Lock()

        def _finalize(tag: str, state: str) -> None:
            barrier.wait(timeout=30)
            try:
                out = finalize_agent_contract_handoff(
                    contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
                    agent_state=state, raw_handoff_json=_envelope(state), db_path=db,
                )
                with lock:
                    results[tag] = out
            except Exception as exc:  # noqa: BLE001 -- surface any deadlock
                with lock:
                    errors.append((tag, exc))

        t_complete = threading.Thread(target=_finalize, args=("complete", "COMPLETE"))
        t_degraded = threading.Thread(target=_finalize, args=("degraded", "IN_PROGRESS"))
        t_complete.start()
        t_degraded.start()
        t_complete.join(timeout=45)
        t_degraded.join(timeout=45)

        assert not t_complete.is_alive() and not t_degraded.is_alive(), "a thread hung"
        assert not errors, f"convergent finalize raced into an error: {errors!r}"
        assert set(results) == {"complete", "degraded"}

        created_flags = sorted(v["created"] for v in results.values())
        assert created_flags == [False, True], (
            f"exactly one writer converges the nascent row; got {created_flags}"
        )
        ids = {v["handoff_id"] for v in results.values()}
        assert len(ids) == 1 and None not in ids, (
            f"both writers must resolve to the SAME row id, got {ids}"
        )

        rows = _row(db, cid)
        assert len(rows) == 1, "the race must converge to exactly one row"
        # Whoever won, the row is terminal -- never left DISPATCHED.
        assert rows[0]["agent_state"] in {"COMPLETE", "IN_PROGRESS"}
        assert rows[0]["agent_state"] != "DISPATCHED"
