"""
gate 1016 (plan 65 task 13) -- host-death reaper for stale DISPATCHED rows.

A row is born 'DISPATCHED' with cut_reason='never_finalized' (v39 birth
stamp) and stays that way until either the agent finalizes it or a closure
path converges it. The SubagentStop backstop (handoff_persister) already
closes a row for a session that DID fire SubagentStop; this reaper is for
the case that backstop cannot reach: a host that died with total silence, so
no event of any kind names the turn again.

Four NAMED cases, per gate 1016's evidence_shape:
  (a) a stale DISPATCHED row with NO liveness evidence for its host -> reaped
      (promoted to a structurally cut, non-DISPATCHED row).
  (b) a stale DISPATCHED row whose host IS reported alive -> left intact,
      byte-for-byte (agent_state and cut_reason unchanged).
  (c) a second run over the same state -> zero changes (idempotent).
  (d) the emitted cut_reason belongs to the closed CUT_REASONS vocabulary.

Runs against a FRESH DB (writer._connect materializes the real schema from
gaia/store/schema.sql); no fixture file.
"""

from __future__ import annotations

import json

import pytest

from gaia.state import CUT_REASON_REAPED, CUT_REASONS
from gaia.store.writer import (
    agent_contract_handoff_state,
    insert_dispatched_handoff,
    reap_stale_dispatched_handoffs,
)
from tests.fixtures.agent_ids import valid_agent_id

WORKSPACE = "me"
AGENT_ID = valid_agent_id("a1234abcd")

# The reap window used by every case below: any row not touched inside 300s
# of "now" (as each test defines it) counts as vencida (stale/overdue).
OLDER_THAN_SECONDS = 300

# A "now" far past any real insert, so a row born an instant ago in test time
# is already outside the staleness window without touching raw timestamps.
FAR_FUTURE_NOW = "2999-01-01T00:00:00Z"


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """An isolated DB path; the writer materializes the real schema."""
    return tmp_path / "gaia.db"


def _birth(contract_id: str, db_path) -> None:
    outcome = insert_dispatched_handoff(
        contract_id,
        AGENT_ID,
        WORKSPACE,
        session_id="sess-" + contract_id,
        db_path=db_path,
    )
    assert outcome["status"] == "applied"


def test_case_a_stale_row_without_liveness_is_reaped_with_host_death_evidence(db):
    _birth("caseA.contract", db)

    result = reap_stale_dispatched_handoffs(
        older_than_seconds=OLDER_THAN_SECONDS,
        liveness_check=lambda row: False,  # no evidence the host is alive
        now=FAR_FUTURE_NOW,
        db_path=db,
    )

    assert result["reaped"] == ["caseA.contract"]
    assert result["spared"] == []

    state = agent_contract_handoff_state("caseA.contract", db_path=db)
    assert state != "DISPATCHED", "reaped row must leave the DISPATCHED row-state"


def test_case_b_row_with_live_host_is_left_intact(db):
    _birth("caseB.contract", db)

    result = reap_stale_dispatched_handoffs(
        older_than_seconds=OLDER_THAN_SECONDS,
        liveness_check=lambda row: True,  # attestation for this host is vigente
        now=FAR_FUTURE_NOW,
        db_path=db,
    )

    assert result["reaped"] == []
    assert result["spared"] == ["caseB.contract"]

    state = agent_contract_handoff_state("caseB.contract", db_path=db)
    assert state == "DISPATCHED", "a row with a live host must NEVER be touched"


def test_case_c_second_run_is_a_zero_change_no_op(db):
    _birth("caseC.contract", db)

    first = reap_stale_dispatched_handoffs(
        older_than_seconds=OLDER_THAN_SECONDS,
        liveness_check=lambda row: False,
        now=FAR_FUTURE_NOW,
        db_path=db,
    )
    assert first["reaped"] == ["caseC.contract"]

    second = reap_stale_dispatched_handoffs(
        older_than_seconds=OLDER_THAN_SECONDS,
        liveness_check=lambda row: False,
        now=FAR_FUTURE_NOW,
        db_path=db,
    )

    assert second["reaped"] == []
    assert second["spared"] == []
    assert second["checked"] == 0, "the already-reaped row must not be a candidate again"


def test_case_d_emitted_cut_reason_belongs_to_the_closed_vocabulary(db):
    _birth("caseD.contract", db)

    result = reap_stale_dispatched_handoffs(
        older_than_seconds=OLDER_THAN_SECONDS,
        liveness_check=lambda row: False,
        now=FAR_FUTURE_NOW,
        db_path=db,
    )

    assert result["cut_reason"] == CUT_REASON_REAPED
    assert result["cut_reason"] in CUT_REASONS

    envelope = json.loads(
        _raw_handoff_json("caseD.contract", db)
    )
    assert envelope["reaped"] is True


def _raw_handoff_json(contract_id: str, db_path) -> str:
    import sqlite3

    con = sqlite3.connect(str(db_path))
    try:
        row = con.execute(
            "SELECT raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    return row[0]
