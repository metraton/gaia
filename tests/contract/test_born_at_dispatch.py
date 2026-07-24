"""
gate 38 (plan 34 task 6) -- born-at-dispatch: the row exists at dispatch.

Task 5 gave the writer the nascent-row PRIMITIVE (insert_dispatched_handoff) and
the convergent finalize. Task 6 is the DISPATCH-SIDE half: at dispatch time the
hook births the nascent row FROM the dispatch metadata, stamping the binding,
BEFORE the agent ever produces a contract -- so a DISPATCHED row is observable
the instant a subagent is dispatched and long before any finalize.

This gate proves the born-BEFORE-finalize invariant at the seam the hook uses
(modules.agents.dispatch_binding.birth_dispatched_row):

  1. birth_dispatched_row creates exactly one 'DISPATCHED' row carrying the
     binding, and that row EXISTS (queryable, state=DISPATCHED) before any
     finalize has run -- "al despachar existe fila DISPATCHED antes del finalize".
  2. the SAME turn's finalize then CONVERGES that born row to its terminal
     verdict -- one row per turn, the binding + birth created_at preserved.
  3. re-dispatch (a second birth of the same contract_id) is idempotent -- it
     never births a second row.

Runs against a FRESH DB; the writer's _connect materializes the real v37 schema.
The binding FK targets (briefs -> plans(34) -> tasks(43)) are seeded via raw
sqlite so a PRESENT binding satisfies referential integrity.
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
    agent_contract_handoff_finalized,
    agent_contract_handoff_state,
    finalize_agent_contract_handoff,
)
from modules.agents.dispatch_binding import (
    birth_dispatched_row,
    extract_dispatch_binding,
)

WORKSPACE = "me"
AGENT_ID = "a1234abcd"
# Mirror the real plan/task ids the task 6 dispatch names (plan 34 / task 43).
PLAN_ID = 34
TASK_ID = 43


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    # Clear the dispatch identity so the handoff write-guard fails open for the
    # hook/CLI context (T8 carry-forward), matching the writer's own suite.
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
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


def _seed_binding_targets(db_path: Path) -> None:
    """Materialize the schema (via a first finalize) + seed the FK chain
    briefs(1) -> plans(34) -> tasks(43, status='pending')."""
    finalize_agent_contract_handoff(
        contract_id="a1234abcd.schema-seed",
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        agent_state="COMPLETE",
        raw_handoff_json=_envelope("COMPLETE"),
        db_path=db_path,
    )
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
            (TASK_ID, PLAN_ID, 6, "dispatch hook with referential integrity", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, agent_state, plan_task_id, plan_id, parent_handoff_id, "
            "kind, created_at FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# (1) the row is BORN at dispatch and exists BEFORE any finalize
# ---------------------------------------------------------------------------

def test_row_born_dispatched_before_any_finalize(db):
    _seed_binding_targets(db)
    cid = "a1234abcd.born-at-dispatch"

    out = birth_dispatched_row(
        contract_id=cid,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        kind="task_execution",
        plan_task_id=TASK_ID,
        plan_id=PLAN_ID,
        db_path=db,
    )
    assert out["created"] is True
    assert out["handoff_id"] is not None

    # THE gate: the row exists, in the DISPATCHED row-state, and is NOT yet
    # finalized -- observable at dispatch, before the agent produced a contract.
    assert agent_contract_handoff_state(cid, db_path=db) == "DISPATCHED"
    assert agent_contract_handoff_finalized(cid, db_path=db) is False

    rows = _rows(db, cid)
    assert len(rows) == 1, "dispatch births exactly one row"
    r = rows[0]
    assert r["agent_state"] == "DISPATCHED"
    assert r["plan_task_id"] == TASK_ID
    assert r["plan_id"] == PLAN_ID
    assert r["kind"] == "task_execution"


# ---------------------------------------------------------------------------
# (2) the same turn's finalize CONVERGES the born row (one row per turn)
# ---------------------------------------------------------------------------

def test_finalize_converges_the_born_row(db):
    _seed_binding_targets(db)
    cid = "a1234abcd.dispatch-then-finalize"

    born = birth_dispatched_row(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        kind="task_execution", plan_task_id=TASK_ID, plan_id=PLAN_ID, db_path=db,
    )
    born_id = born["handoff_id"]
    born_created_at = _rows(db, cid)[0]["created_at"]

    fin = finalize_agent_contract_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=_envelope("COMPLETE"), db_path=db,
    )
    assert fin["created"] is True
    assert fin["handoff_id"] == born_id, "finalize converged the SAME born row"

    rows = _rows(db, cid)
    assert len(rows) == 1, "one row per turn -- finalize converged, did not dup"
    r = rows[0]
    assert r["agent_state"] == "COMPLETE"
    # binding + birth timestamp survive convergence untouched.
    assert r["plan_task_id"] == TASK_ID
    assert r["plan_id"] == PLAN_ID
    assert r["kind"] == "task_execution"
    assert r["created_at"] == born_created_at
    assert agent_contract_handoff_finalized(cid, db_path=db) is True


# ---------------------------------------------------------------------------
# (3) re-dispatch is idempotent -- never a second born row
# ---------------------------------------------------------------------------

def test_re_dispatch_is_idempotent(db):
    _seed_binding_targets(db)
    cid = "a1234abcd.re-dispatch"

    first = birth_dispatched_row(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        kind="task_execution", plan_task_id=TASK_ID, plan_id=PLAN_ID, db_path=db,
    )
    second = birth_dispatched_row(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        kind="task_execution", plan_task_id=TASK_ID, plan_id=PLAN_ID, db_path=db,
    )
    assert first["created"] is True
    assert second["created"] is False
    assert first["handoff_id"] == second["handoff_id"]
    assert len(_rows(db, cid)) == 1


# ---------------------------------------------------------------------------
# (4) a verifier turn is BORN via the extract -> birth path (plan 34 wiring)
# ---------------------------------------------------------------------------

def test_verifier_row_born_via_extracted_parent_handoff_id(db):
    """End-to-end for the verifier-birth wiring: extract_dispatch_binding must
    surface parent_handoff_id from the dispatch prompt so birth_dispatched_row
    can stamp it. Before this wiring the binding carried no parent_handoff_id,
    so the verifier branch of validate_dispatch_binding always failed with
    'verifier_requires_parent_handoff_id' and the verifier row never nació."""
    _seed_binding_targets(db)

    # A resolvable PRODUCER row for the verifier to bind to via parent_handoff_id.
    producer = birth_dispatched_row(
        contract_id="a1234abcd.producer", agent_id=AGENT_ID, workspace=WORKSPACE,
        kind="task_execution", plan_task_id=TASK_ID, plan_id=PLAN_ID, db_path=db,
    )
    parent_id = producer["handoff_id"]

    # The orchestrator dispatches gaia-verifier naming the producer handoff it
    # verifies via a parent_handoff_id=<N> token.
    binding = extract_dispatch_binding({
        "prompt": (
            f"Verifica la TASK (task_id={TASK_ID}) del plan_id={PLAN_ID}, "
            f"parent_handoff_id={parent_id}"
        ),
        "subagent_type": "gaia-verifier",
    })
    assert binding["turn_role"] == "verifier"
    assert binding["parent_handoff_id"] == parent_id
    assert binding["plan_task_id"] is None  # verifier binds by parent, not task

    cid = "dispatch.sess.gaia-verifier.pparent"
    out = birth_dispatched_row(
        contract_id=cid,
        agent_id="gaia-verifier",
        workspace=WORKSPACE,
        kind=binding["kind"],
        turn_role=binding["turn_role"],
        plan_task_id=binding["plan_task_id"],
        plan_id=binding["plan_id"],
        parent_handoff_id=binding["parent_handoff_id"],
        db_path=db,
    )
    assert out["created"] is True, "the verifier row must now be born"

    rows = _rows(db, cid)
    assert len(rows) == 1
    r = rows[0]
    assert r["agent_state"] == "DISPATCHED"
    assert r["parent_handoff_id"] == parent_id
    assert r["plan_task_id"] is None
    assert r["kind"] == "verifier"


# ---------------------------------------------------------------------------
# (5) WITHOUT the parent_handoff_id token, birth is a clean no-op (best-effort)
# ---------------------------------------------------------------------------

def test_verifier_row_not_born_without_parent_handoff_id_token(db):
    """The companion, negative-space case for (4): a verifier dispatch prompt
    that does NOT carry a `parent_handoff_id=<N>` token (the orchestrator
    template omitted it, or the producer never surfaced its handoff_id)
    extracts a binding with `parent_handoff_id=None`. Attempting to birth that
    binding is REJECTED by referential integrity (the verifier branch of
    validate_dispatch_binding requires a resolvable parent) -- but this must
    never corrupt state or raise anything OTHER than the clean
    DispatchBindingError the real hook path (_maybe_birth_dispatched_row)
    already catches best-effort and non-blocking (see
    hooks/adapters/claude_code.py). No row is born either way."""
    _seed_binding_targets(db)

    binding = extract_dispatch_binding({
        "prompt": f"Verifica la TASK (task_id={TASK_ID}) del plan_id={PLAN_ID}",
        "subagent_type": "gaia-verifier",
    })
    assert binding["turn_role"] == "verifier"
    assert binding["parent_handoff_id"] is None, (
        "no token in the prompt -- extraction leaves it None, not a guess"
    )
    assert binding["plan_task_id"] is None  # verifier never binds by task_id

    cid = "dispatch.sess.gaia-verifier.no-token"
    from modules.agents.dispatch_binding import DispatchBindingError

    with pytest.raises(DispatchBindingError) as ei:
        birth_dispatched_row(
            contract_id=cid,
            agent_id="gaia-verifier",
            workspace=WORKSPACE,
            kind=binding["kind"],
            turn_role=binding["turn_role"],
            plan_task_id=binding["plan_task_id"],
            plan_id=binding["plan_id"],
            parent_handoff_id=binding["parent_handoff_id"],
            db_path=db,
        )
    assert ei.value.reason == "verifier_requires_parent_handoff_id"

    # best-effort: the rejection births nothing -- no dangling / partial row.
    assert len(_rows(db, cid)) == 0
