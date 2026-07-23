"""
AC-8 -- exactly-once + never-lost on the LIVE SubagentStop path (M3, task T9).

The hook's ``persist_handoff`` (hooks/modules/agents/handoff_persister.py) is
now a CONDITIONAL BACKSTOP finalizer, NOT the primary writer:

  * A turn that FINALIZES (the agent ran ``gaia contract finalize`` ->
    ``finalize_agent_contract_handoff``) already owns its row -> the hook
    backstop adds NONE (it is passive; no duplicate).
  * A turn that does NOT finalize (crash / forget / truncation) -> the hook
    backstop finalizes the draft that exists (marked ``degraded=true``), or
    writes a MINIMAL degraded row when no draft exists -> EXACTLY ONE row
    (never zero). It fabricates no evidence fields it does not have.
  * Under a race between the agent finalize and the hook backstop, both key on
    the SAME ``contract_id`` and the writer's ``ON CONFLICT(contract_id) DO
    NOTHING`` leaves EXACTLY ONE row, in either arrival order.

A backstop row is DISTINGUISHABLE from an agent-verified COMPLETE row by the
``degraded`` flag in ``raw_handoff_json`` (an agent-finalized row carries no
such flag) -- the property AC-8 / AC-12 require.

The backstop is exercised in-process (it is a plain Python function on the
hook lifecycle). Drafts live under an isolated ``GAIA_DATA_DIR``; the DB is a
separate isolated file passed via ``task_info['db_path']`` and materialized by
the writer's own ``_connect`` from ``gaia/store/schema.sql`` (the real v28
table -- ``contract_id`` UNIQUE + ``task_status`` CHECK -- not a fixture).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# The backstop lives under hooks/ (the `modules` package). Put hooks/ on the
# path at import time -- collection happens before the conftest autouse fixture
# that does this for the rest of the suite.
_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from gaia.contract.drafts import mint_draft_id, save_draft
from gaia.store.writer import (
    agent_contract_handoff_exists,
    finalize_agent_contract_handoff,
)
from modules.agents.handoff_persister import persist_handoff

VALID_AGENT_ID = "a1234abcd"
WORKSPACE = "me"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    """Isolate BOTH substrates the backstop touches and clear the dispatch id.

    * ``GAIA_DATA_DIR`` -> the contract drafts dir (``<data>/contract_drafts``).
    * ``GAIA_DISPATCH_AGENT`` unset -> the write-guard early-returns (allowed),
      which is exactly how the hook runs (T8 carry-forward).
    """
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """Isolated DB path; the writer materializes the real schema on first use."""
    return tmp_path / "gaia.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _envelope(plan_status: str = "COMPLETE") -> dict:
    return {
        "agent_status": {
            "plan_status": plan_status,
            "agent_id": VALID_AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [], "files_checked": [], "commands_run": [],
            "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": {"method": "test", "checks": ["ac8"],
                             "result": "pass", "details": "ok"},
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _task_info(db_path: Path) -> dict:
    return {"agent_id": VALID_AGENT_ID, "agent": "developer",
            "workspace": WORKSPACE, "db_path": str(db_path)}


def _rows(db_path: Path):
    if not db_path.is_file():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT contract_id, agent_id, agent_state, raw_handoff_json "
            "FROM agent_contract_handoffs ORDER BY id"
        ).fetchall()
    finally:
        con.close()


def _is_degraded(raw_handoff_json: str) -> bool:
    return bool(json.loads(raw_handoff_json).get("degraded"))


# ---------------------------------------------------------------------------
# Clause 1 -- a turn that FINALIZES: hook backstop adds NO row (conditional)
# ---------------------------------------------------------------------------

def test_finalized_turn_backstop_is_passive_no_duplicate(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _envelope("COMPLETE")
    save_draft(draft_id, envelope)

    # The agent's PRIMARY finalize writes the row (non-degraded).
    outcome = finalize_agent_contract_handoff(
        contract_id=draft_id,
        agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE,
        task_status="COMPLETE",
        raw_handoff_json=json.dumps(envelope),
        db_path=db,
    )
    assert outcome["created"] is True

    # The hook backstop then runs on the SAME turn -> must be a no-op.
    persist_handoff(
        parsed_contract=envelope,
        agent_output="",
        task_info=_task_info(db),
        session_id="sess-1",
    )

    rows = _rows(db)
    assert len(rows) == 1, "backstop duplicated an already-finalized row"
    assert rows[0]["contract_id"] == draft_id
    assert rows[0]["agent_state"] == "COMPLETE"
    # An agent-finalized row is NOT degraded -- distinguishable from a backstop.
    assert _is_degraded(rows[0]["raw_handoff_json"]) is False


# ---------------------------------------------------------------------------
# Clause 2a -- a turn that does NOT finalize but has a draft:
#              backstop finalizes the draft as degraded -> exactly one row
# ---------------------------------------------------------------------------

def test_unfinalized_draft_backstopped_as_degraded(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _envelope("IN_PROGRESS")
    save_draft(draft_id, envelope)

    assert agent_contract_handoff_exists(draft_id, db_path=db) is False

    persist_handoff(
        parsed_contract=envelope,
        agent_output="partial work",
        task_info=_task_info(db),
        session_id="sess-2",
    )

    rows = _rows(db)
    assert len(rows) == 1, "backstop must capture exactly one row (not zero)"
    assert rows[0]["contract_id"] == draft_id
    assert _is_degraded(rows[0]["raw_handoff_json"]) is True


# ---------------------------------------------------------------------------
# Clause 2b -- a turn with NO draft at all (crash/truncation):
#              backstop writes a MINIMAL degraded row -> exactly one row
# ---------------------------------------------------------------------------

def test_no_draft_backstop_writes_minimal_degraded_row(db):
    # No draft, no parsed contract (a truncated / crashed turn).
    persist_handoff(
        parsed_contract=None,
        agent_output="truncated mid-sentence",
        task_info=_task_info(db),
        session_id="sess-3",
    )

    rows = _rows(db)
    assert len(rows) == 1, "a crashed turn must still leave one row (never zero)"
    row = rows[0]
    assert _is_degraded(row["raw_handoff_json"]) is True
    # NOT falsely COMPLETE -- a crash never satisfies the briefs COMPLETE invariant.
    assert row["agent_state"] == "IN_PROGRESS"
    payload = json.loads(row["raw_handoff_json"])
    # Minimal: no fabricated evidence_report / verification.
    assert "evidence_report" not in payload
    assert payload.get("backstop") == "hook_subagent_stop"


# ---------------------------------------------------------------------------
# Clause 3 -- race finalize + backstop -> idempotent UPSERT -> ONE row,
#             in EITHER arrival order
# ---------------------------------------------------------------------------

def test_race_backstop_then_finalize_one_row(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _envelope("COMPLETE")
    save_draft(draft_id, envelope)

    # Backstop wins the race first (writes a degraded row) ...
    persist_handoff(
        parsed_contract=envelope,
        agent_output="",
        task_info=_task_info(db),
        session_id="sess-4",
    )
    # ... then the agent finalize arrives on the SAME contract_id.
    outcome = finalize_agent_contract_handoff(
        contract_id=draft_id,
        agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE,
        task_status="COMPLETE",
        raw_handoff_json=json.dumps(envelope),
        db_path=db,
    )
    assert outcome["created"] is False, "second writer must be a no-op (UPSERT)"

    rows = _rows(db)
    assert len(rows) == 1, "race must converge to exactly one row"
    assert rows[0]["contract_id"] == draft_id


def test_race_finalize_then_backstop_one_row(db):
    draft_id = mint_draft_id(VALID_AGENT_ID)
    envelope = _envelope("COMPLETE")
    save_draft(draft_id, envelope)

    # Agent finalize wins first ...
    finalize_agent_contract_handoff(
        contract_id=draft_id,
        agent_id=VALID_AGENT_ID,
        workspace=WORKSPACE,
        task_status="COMPLETE",
        raw_handoff_json=json.dumps(envelope),
        db_path=db,
    )
    # ... then the hook backstop arrives -> passive no-op.
    persist_handoff(
        parsed_contract=envelope,
        agent_output="",
        task_info=_task_info(db),
        session_id="sess-5",
    )

    rows = _rows(db)
    assert len(rows) == 1, "race must converge to exactly one row"
    # The surviving row is the agent's verified (non-degraded) one.
    assert _is_degraded(rows[0]["raw_handoff_json"]) is False
