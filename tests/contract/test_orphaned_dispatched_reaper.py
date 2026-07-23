"""
gate 36 (plan 34 task 5) -- orphaned-DISPATCHED reaper.

The SubagentStop backstop (hooks/modules/agents/handoff_persister.py) is, under
v37 born-at-dispatch, ALSO the REAPER: a handoff row born at dispatch with
agent_state='DISPATCHED' and left orphaned by a crash (the agent never ran its
own verified ``gaia contract finalize``) must be RECONCILED to a terminal
verdict -- and that verdict must NEVER be a false COMPLETE, because an
unfinalized turn never truly completed (a false COMPLETE would falsely satisfy
the briefs "plan closed => a COMPLETE handoff row exists" invariant).

Clauses:
  1. orphan WITH a draft claiming COMPLETE -> reaped, but converged to a
     degraded NON-COMPLETE verdict (COMPLETE downgraded to IN_PROGRESS).
  2. orphan WITHOUT any draft (draftless crash) -> located by (session, agent)
     and reaped in place -> exactly one row (the born row converged, no dup).
  3. a turn that DID finalize (terminal row exists) -> the backstop stays
     passive: the row is the agent's genuine COMPLETE, not reaped, not degraded.
  4. the reaper-vs-finalize race converges to exactly one row per contract_id;
     a COMPLETE survivor is ONLY ever the agent's genuine (non-degraded) one --
     the reaper never manufactures a COMPLETE.

Fresh DB; the writer materializes the real v37 schema. Drafts live under an
isolated GAIA_DATA_DIR. The dispatch id is cleared so the write-guard allows
the hook path (T8 carry-forward).
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.contract.drafts import mint_draft_id, save_draft
from gaia.store.writer import (
    agent_contract_handoff_state,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
)
from modules.agents.handoff_persister import persist_handoff

WORKSPACE = "me"
AGENT_ID = "a1234abcd"
PLAN_ID = 34
TASK_ID = 42


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


def _envelope(state: str = "COMPLETE") -> dict:
    return {
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
            "verification": {"method": "test", "checks": ["reaper"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path) -> dict:
    return {"agent_id": AGENT_ID, "agent": "developer",
            "workspace": WORKSPACE, "db_path": str(db_path)}


def _seed_binding_targets(db_path: Path) -> None:
    """Materialize schema + seed the born-at-dispatch binding FK targets."""
    finalize_agent_contract_handoff(
        contract_id="a1234abcd.parent-seed", agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(_envelope("COMPLETE")),
        db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        con.execute(
            "INSERT INTO briefs (id, workspace, name, status) VALUES (?, ?, ?, ?)",
            (1, WORKSPACE, "contrato-binding-y-verificacion-por-task-id", "in-progress"),
        )
        con.execute("INSERT INTO plans (id, brief_id, status) VALUES (?, ?, ?)",
                    (PLAN_ID, 1, "active"))
        con.execute(
            "INSERT INTO tasks (id, plan_id, order_num, goal, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (TASK_ID, PLAN_ID, 5, "reaper", "pending"),
        )
        con.commit()
    finally:
        con.close()


def _rows(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, contract_id, agent_state, plan_task_id, plan_id, kind, "
            "raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ? "
            "ORDER BY id",
            (contract_id,),
        ).fetchall()
    finally:
        con.close()


def _flags(raw_handoff_json: str) -> dict:
    return json.loads(raw_handoff_json)


# ---------------------------------------------------------------------------
# Clause 1 -- orphan WITH a draft claiming COMPLETE: reaped, NEVER false COMPLETE
# ---------------------------------------------------------------------------

def test_orphan_with_complete_draft_reaped_never_false_complete(db):
    _seed_binding_targets(db)
    draft_id = mint_draft_id(AGENT_ID)
    envelope = _envelope("COMPLETE")  # the agent BUILT a COMPLETE contract ...
    save_draft(draft_id, envelope)

    # ... a row was BORN at dispatch under that same contract_id, and the agent
    # CRASHED before its own `gaia contract finalize` (row still DISPATCHED).
    born = insert_dispatched_handoff(
        contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution",
        session_id="sess-orphan-1", db_path=db,
    )
    assert agent_contract_handoff_state(draft_id, db_path=db) == "DISPATCHED"

    # The SubagentStop backstop fires -> it must REAP the orphan.
    persist_handoff(
        parsed_contract=envelope,
        agent_output="crashed after building COMPLETE",
        task_info=_task_info(db),
        session_id="sess-orphan-1",
    )

    rows = _rows(db, draft_id)
    assert len(rows) == 1, "reaper must converge the orphan, not duplicate it"
    assert rows[0]["id"] == born["handoff_id"], "same physical row converged"
    # NEVER a false COMPLETE: an unfinalized turn is downgraded to IN_PROGRESS.
    assert rows[0]["agent_state"] == "IN_PROGRESS"
    assert rows[0]["agent_state"] != "COMPLETE"
    flags = _flags(rows[0]["raw_handoff_json"])
    assert flags.get("degraded") is True
    assert flags.get("reaped") is True
    # The born-at-dispatch binding survives the reap.
    assert rows[0]["plan_task_id"] == TASK_ID
    assert rows[0]["plan_id"] == PLAN_ID
    assert rows[0]["kind"] == "task_execution"


# ---------------------------------------------------------------------------
# Clause 2 -- orphan WITHOUT a draft: located by (session, agent), reaped in place
# ---------------------------------------------------------------------------

def test_orphan_without_draft_located_by_session_and_reaped(db):
    _seed_binding_targets(db)
    # Born at dispatch, no draft ever created, then crashed. It carries the
    # (session, agent) the backstop will fire under.
    cid = "a1234abcd.draftless-orphan"
    born = insert_dispatched_handoff(
        contract_id=cid, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution",
        session_id="sess-orphan-2", db_path=db,
    )
    assert agent_contract_handoff_state(cid, db_path=db) == "DISPATCHED"

    # No parsed contract, no draft (a truncated / crashed turn).
    persist_handoff(
        parsed_contract=None,
        agent_output="truncated mid-sentence",
        task_info=_task_info(db),
        session_id="sess-orphan-2",
    )

    rows = _rows(db, cid)
    assert len(rows) == 1, "the orphan is reaped in place -- no second row"
    assert rows[0]["id"] == born["handoff_id"]
    # Honest non-COMPLETE verdict for a crash.
    assert rows[0]["agent_state"] == "IN_PROGRESS"
    flags = _flags(rows[0]["raw_handoff_json"])
    assert flags.get("degraded") is True
    assert flags.get("reaped") is True


# ---------------------------------------------------------------------------
# Clause 3 -- a FINALIZED turn: backstop is passive (no reap, no downgrade)
# ---------------------------------------------------------------------------

def test_finalized_turn_backstop_passive_no_reap(db):
    _seed_binding_targets(db)
    draft_id = mint_draft_id(AGENT_ID)
    envelope = _envelope("COMPLETE")
    save_draft(draft_id, envelope)

    insert_dispatched_handoff(
        contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
        plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution",
        session_id="sess-done", db_path=db,
    )
    # The agent genuinely finalizes -> the nascent row converges to COMPLETE.
    out = finalize_agent_contract_handoff(
        contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope), db_path=db,
    )
    assert out["created"] is True

    # The backstop then fires -> it must stay passive (terminal row exists).
    persist_handoff(
        parsed_contract=envelope, agent_output="",
        task_info=_task_info(db), session_id="sess-done",
    )

    rows = _rows(db, draft_id)
    assert len(rows) == 1
    assert rows[0]["agent_state"] == "COMPLETE", "genuine COMPLETE preserved"
    flags = _flags(rows[0]["raw_handoff_json"])
    assert not flags.get("degraded"), "an agent-finalized row is not degraded"
    assert not flags.get("reaped"), "a finalized row is never reaped"


# ---------------------------------------------------------------------------
# Clause 4 -- reaper-vs-finalize race: one row; a COMPLETE survivor is genuine
# ---------------------------------------------------------------------------

def test_reaper_vs_finalize_race_one_row_no_false_complete(db):
    _seed_binding_targets(db)
    for round_i in range(10):
        draft_id = mint_draft_id(AGENT_ID)
        envelope = _envelope("COMPLETE")
        save_draft(draft_id, envelope)
        sid = f"sess-race-{round_i}"
        insert_dispatched_handoff(
            contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
            plan_task_id=TASK_ID, plan_id=PLAN_ID, kind="task_execution",
            session_id=sid, db_path=db,
        )

        barrier = threading.Barrier(2)
        finalize_error: list = []

        def _agent_finalize() -> None:
            barrier.wait(timeout=30)
            try:
                finalize_agent_contract_handoff(
                    contract_id=draft_id, agent_id=AGENT_ID, workspace=WORKSPACE,
                    agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
                    db_path=db,
                )
            except Exception as exc:  # noqa: BLE001
                finalize_error.append(exc)

        def _backstop_reaper() -> None:
            barrier.wait(timeout=30)
            persist_handoff(
                parsed_contract=envelope, agent_output="",
                task_info=_task_info(db), session_id=sid,
            )

        t_fin = threading.Thread(target=_agent_finalize)
        t_reap = threading.Thread(target=_backstop_reaper)
        t_fin.start()
        t_reap.start()
        t_fin.join(timeout=45)
        t_reap.join(timeout=45)

        assert not t_fin.is_alive() and not t_reap.is_alive(), "a thread hung"
        assert not finalize_error, f"finalize raised under race: {finalize_error!r}"

        rows = _rows(db, draft_id)
        assert len(rows) == 1, "reaper-vs-finalize must converge to exactly one row"
        r = rows[0]
        flags = _flags(r["raw_handoff_json"])
        if r["agent_state"] == "COMPLETE":
            # A COMPLETE survivor can ONLY be the agent's genuine finalize --
            # the reaper never manufactures a COMPLETE.
            assert not flags.get("degraded"), "false COMPLETE from the reaper!"
            assert not flags.get("reaped"), "false COMPLETE from the reaper!"
        else:
            # The reaper won -> honest degraded non-COMPLETE verdict.
            assert r["agent_state"] == "IN_PROGRESS"
            assert flags.get("degraded") is True
            assert flags.get("reaped") is True
