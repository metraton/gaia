"""
AC-8 GAP-CLOSE (T18) -- TRUE-concurrency backstop-vs-finalize race.

T9's ``test_exactly_once_and_backstop.py`` proves the never-lost / exactly-once
contract, but its "race" clauses are SEQUENTIAL simulations: they run the hook
backstop and the agent finalize one after the other and rely on the
``ON CONFLICT(contract_id) DO NOTHING`` UPSERT to converge. That establishes the
LOGICAL contract but never exercises the two writers hitting the SAME
``contract_id`` at the SAME instant against a live SQLite file -- exactly the
production condition (a ``gaia contract finalize`` subprocess and the
SubagentStop hook backstop firing on the same turn) that the deferred-BEGIN
two-writers deadlock in ``gaia.store.writer`` used to break.

This module adds the missing genuine-concurrency coverage, in the same
``threading.Barrier`` style as ``test_concurrent_drafts.py``: two writers are
blocked on a shared barrier and released together, so their SQLite writes
overlap in wall-clock time every run. The correctness property is deterministic
regardless of who wins the race:

  * EXACTLY ONE row for the shared ``contract_id`` (never two, never zero).
  * NO deadlock / "database is locked" surfaced to either writer.
  * Holds in EITHER arrival order (the barrier makes order nondeterministic;
    repeating across rounds exercises both).

Two complementary shapes:
  1. The FAITHFUL production shape -- the real hook backstop
     (``modules.agents.handoff_persister.persist_handoff``) racing the real
     agent finalize (``finalize_agent_contract_handoff``) on the same draft.
     Because ``persist_handoff`` deliberately SWALLOWS its own exceptions
     (a hook must never break the lifecycle), a deadlock on the backstop side
     would be hidden here -- so this shape asserts convergence + that the
     finalize side (which does NOT swallow) never raised.
  2. The DEADLOCK-SURFACING shape -- two direct ``finalize_agent_contract_handoff``
     calls (the underlying convergence point both writers reach) on the same
     ``contract_id``. Neither swallows, so a two-writers deadlock or a
     "database is locked" would surface as a raised exception and FAIL the
     test. This is the strongest single check that the write-lock-first
     (``BEGIN IMMEDIATE``) + busy_timeout + retry fix actually holds.

Both shapes run against a FRESH DB (no pre-materialized schema), so the race
also exercises the concurrent first-write path through the TOCTOU-safe
``_ensure_schema_materialized`` -- a concurrent first writer must never observe
a missing ``agent_contract_handoffs`` table or its ``contract_id`` UNIQUE index.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

# The backstop lives under hooks/ (the `modules` package). Put hooks/ on the
# path before importing it (mirrors test_exactly_once_and_backstop.py).
_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.contract.drafts import mint_draft_id, save_draft
from gaia.store.writer import finalize_agent_contract_handoff
from modules.agents.handoff_persister import persist_handoff

VALID_AGENT_ID = "a1234abcd"
WORKSPACE = "me"


# ---------------------------------------------------------------------------
# Fixtures (mirror test_exactly_once_and_backstop.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    """Isolate the drafts substrate and clear the dispatch id (allowed path)."""
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """Isolated DB path -- FRESH (no schema): the concurrent writers race to
    materialize it, exercising the TOCTOU-safe first-write path."""
    return tmp_path / "gaia.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": VALID_AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["ac8-race"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path) -> dict:
    return {"agent_id": VALID_AGENT_ID, "agent": "developer",
            "workspace": WORKSPACE, "db_path": str(db_path)}


def _count_rows(db_path: Path, contract_id: str) -> int:
    if not db_path.is_file():
        return 0
    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
        return int(row[0])
    finally:
        con.close()


def _all_rows(db_path: Path, contract_id: str):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT id, contract_id, agent_id, agent_state "
            "FROM agent_contract_handoffs WHERE contract_id = ? ORDER BY id",
            (contract_id,),
        ).fetchall()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Shape 1 -- FAITHFUL: real hook backstop vs real agent finalize, same instant
# ---------------------------------------------------------------------------

def test_true_concurrent_backstop_and_finalize_yield_one_row(db):
    """The real backstop and the real agent finalize, released together by a
    barrier, hit the SAME contract_id at the same instant -> exactly one row,
    the finalize side never deadlocks. Repeated across rounds so both arrival
    orders are exercised."""
    for _round in range(8):
        draft_id = mint_draft_id(VALID_AGENT_ID)
        envelope = _envelope()
        save_draft(draft_id, envelope)

        barrier = threading.Barrier(2)
        finalize_error: list = []
        finalize_outcome: list = []

        def _backstop() -> None:
            # The real hook path. It resolves the SAME draft (same contract_id)
            # and finalizes via the same idempotent writer. It swallows its own
            # errors by contract, so we do not assert on it directly.
            barrier.wait(timeout=30)
            persist_handoff(
                parsed_contract=envelope,
                agent_output="",
                task_info=_task_info(db),
                session_id="sess-race",
            )

        def _agent_finalize() -> None:
            barrier.wait(timeout=30)
            try:
                out = finalize_agent_contract_handoff(
                    contract_id=draft_id,
                    agent_id=VALID_AGENT_ID,
                    workspace=WORKSPACE,
                    agent_state="COMPLETE",
                    raw_handoff_json=json.dumps(envelope),
                    db_path=db,
                )
                finalize_outcome.append(out)
            except Exception as exc:  # noqa: BLE001 -- surface any deadlock
                finalize_error.append(exc)

        t_backstop = threading.Thread(target=_backstop)
        t_agent = threading.Thread(target=_agent_finalize)
        t_backstop.start()
        t_agent.start()
        t_backstop.join(timeout=45)
        t_agent.join(timeout=45)

        assert not t_backstop.is_alive(), "backstop thread hung (possible deadlock)"
        assert not t_agent.is_alive(), "finalize thread hung (possible deadlock)"
        # The finalize side does NOT swallow -- a two-writers deadlock or a
        # 'database is locked' would land here.
        assert not finalize_error, (
            f"agent finalize raised under the race: {finalize_error!r}"
        )
        assert finalize_outcome and finalize_outcome[0]["status"] == "applied"

        # The headline property, independent of who won the race.
        assert _count_rows(db, draft_id) == 1, (
            "backstop+finalize race must converge to exactly one row"
        )


# ---------------------------------------------------------------------------
# Shape 2 -- DEADLOCK-SURFACING: two direct finalize calls, both raise on error
# ---------------------------------------------------------------------------

def test_true_concurrent_double_finalize_exactly_one_created(db):
    """Two ``finalize_agent_contract_handoff`` calls on the SAME contract_id,
    released together -- the convergence point both production writers reach.
    Neither swallows, so a deadlock / lock error surfaces and fails. Exactly one
    call reports created=True, the other created=False, both return the SAME
    handoff_id, and exactly one row exists -- in either arrival order."""
    for _round in range(12):
        draft_id = mint_draft_id(VALID_AGENT_ID)
        envelope = _envelope()
        raw = json.dumps(envelope)

        barrier = threading.Barrier(2)
        results: dict = {}
        errors: list = []
        lock = threading.Lock()

        def _finalize(tag: str) -> None:
            barrier.wait(timeout=30)
            try:
                out = finalize_agent_contract_handoff(
                    contract_id=draft_id,
                    agent_id=VALID_AGENT_ID,
                    workspace=WORKSPACE,
                    agent_state="COMPLETE",
                    raw_handoff_json=raw,
                    db_path=db,
                )
                with lock:
                    results[tag] = out
            except Exception as exc:  # noqa: BLE001 -- surface any deadlock
                with lock:
                    errors.append((tag, exc))

        t_a = threading.Thread(target=_finalize, args=("A",))
        t_b = threading.Thread(target=_finalize, args=("B",))
        t_a.start()
        t_b.start()
        t_a.join(timeout=45)
        t_b.join(timeout=45)

        assert not t_a.is_alive() and not t_b.is_alive(), "a finalize thread hung"
        assert not errors, f"concurrent finalize raised (deadlock/lock?): {errors!r}"
        assert set(results) == {"A", "B"}

        created_flags = sorted(v["created"] for v in results.values())
        # Exactly one writer created the row; the other was the idempotent no-op.
        assert created_flags == [False, True], (
            f"expected exactly one created=True under the race, got {created_flags}"
        )
        # Both writers agree on the single surviving row's id.
        ids = {v["handoff_id"] for v in results.values()}
        assert len(ids) == 1 and None not in ids, (
            f"both writers must resolve to the same handoff_id, got {ids}"
        )

        rows = _all_rows(db, draft_id)
        assert len(rows) == 1, "double finalize race must leave exactly one row"
        assert rows[0]["contract_id"] == draft_id
