"""Reading no chain and failing to read one are different answers.

``continuation_chain`` used to catch every exception and return ``[]``, so "this
contract id names no row" and "the walk could not be performed" arrived at the
caller identically. The merge is not hypothetical: a database still on schema
< v46 has no ``continues_handoff_id`` column, so the walk raises on EVERY row and
``gaia contract chain`` reported "no agent_contract_handoffs row exists" for ids
the operator was holding in their hand.

The distinction has to survive the process that made it -- this module has no
logger, and a log line an operator cannot query later is not a trace. The
failure is therefore raised AND recorded as a ``contract.chain_unreadable``
harness event, readable with ``gaia query --surface harness_events``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.store.writer import (  # noqa: E402
    CONTINUATION_CHAIN_UNREADABLE_EVENT,
    ContinuationChainUnreadable,
    continuation_chain,
    continuation_tip,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"

WORKSPACE = "me"
AGENT_ID = valid_agent_id("chain-readability")
SESSION_ID = "sess-chain"
LIVE_CONTRACT_ID = f"{AGENT_ID}.exists"
ABSENT_CONTRACT_ID = f"{AGENT_ID}.never-existed"


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    """A current-schema database holding one real row."""
    path = tmp_path / "gaia.db"
    insert_dispatched_handoff(
        contract_id=LIVE_CONTRACT_ID, agent_id=AGENT_ID, workspace=WORKSPACE,
        session_id=SESSION_ID, kind="investigation", db_path=path,
    )
    return path


@pytest.fixture()
def pre_v46_db(db):
    """The same database as the live one: the edge column was never migrated in."""
    con = sqlite3.connect(str(db))
    try:
        con.execute("DROP INDEX IF EXISTS idx_agent_contract_handoffs_continues")
        con.execute(
            "ALTER TABLE agent_contract_handoffs DROP COLUMN continues_handoff_id"
        )
        con.commit()
    finally:
        con.close()
    return db


def _events(db_path: Path, event_type: str) -> list:
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT * FROM harness_events WHERE type = ? ORDER BY id",
            (event_type,),
        ).fetchall()
    finally:
        con.close()


def _run_chain(db_path: Path, contract_id: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GAIA_DATA_DIR"] = str(db_path.parent)
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), "chain", "--contract-id", contract_id,
         "--json"],
        capture_output=True, text=True, env=env, timeout=30,
    )


# ---------------------------------------------------------------------------
# The two answers, told apart
# ---------------------------------------------------------------------------

def test_an_unknown_contract_id_is_an_empty_chain_and_no_incident(db):
    assert continuation_chain(ABSENT_CONTRACT_ID, db_path=db) == []
    assert continuation_tip(ABSENT_CONTRACT_ID, db_path=db) is None
    assert _events(db, CONTINUATION_CHAIN_UNREADABLE_EVENT) == [], (
        "a known-empty answer is not a failure and must not raise an incident"
    )


def test_a_chain_that_cannot_be_read_raises_instead_of_reading_as_absent(pre_v46_db):
    """The measured case: a row that EXISTS on a database without the edge."""
    con = sqlite3.connect(str(pre_v46_db))
    try:
        assert con.execute(
            "SELECT COUNT(*) FROM agent_contract_handoffs WHERE contract_id = ?",
            (LIVE_CONTRACT_ID,),
        ).fetchone()[0] == 1, "precondition: the row is really there"
    finally:
        con.close()

    with pytest.raises(ContinuationChainUnreadable) as excinfo:
        continuation_chain(LIVE_CONTRACT_ID, db_path=pre_v46_db)

    assert excinfo.value.contract_id == LIVE_CONTRACT_ID
    assert "NOT 'no such contract'" in str(excinfo.value)


def test_the_failure_leaves_a_trace_an_operator_can_query(pre_v46_db):
    """No logger in this module: the trace has to be in the database."""
    with pytest.raises(ContinuationChainUnreadable):
        continuation_chain(LIVE_CONTRACT_ID, db_path=pre_v46_db)

    events = _events(pre_v46_db, CONTINUATION_CHAIN_UNREADABLE_EVENT)
    assert len(events) == 1, "the failure must be findable after the process exits"
    event = events[0]
    assert event["severity"] == "warning"
    assert LIVE_CONTRACT_ID in event["result"]
    payload = json.loads(event["payload"])
    assert payload["contract_id"] == LIVE_CONTRACT_ID
    assert "continues_handoff_id" in payload["error"], (
        "the incident must name the substrate failure, not just that one happened"
    )


def test_one_incident_per_failure_not_per_contract_id(pre_v46_db):
    """Every contract write resolves a chain: an event each would bury the signal."""
    insert_dispatched_handoff(
        contract_id=f"{AGENT_ID}.second", agent_id=AGENT_ID, workspace=WORKSPACE,
        session_id=SESSION_ID, kind="investigation", db_path=pre_v46_db,
    )
    for contract_id in (LIVE_CONTRACT_ID, f"{AGENT_ID}.second", LIVE_CONTRACT_ID):
        with pytest.raises(ContinuationChainUnreadable):
            continuation_chain(contract_id, db_path=pre_v46_db)

    assert len(_events(pre_v46_db, CONTINUATION_CHAIN_UNREADABLE_EVENT)) == 1, (
        "the failure describes the substrate, which is broken once"
    )


def test_the_tip_read_propagates_rather_than_reporting_no_row(pre_v46_db):
    """``None`` from the tip must keep meaning "no such row"."""
    with pytest.raises(ContinuationChainUnreadable):
        continuation_tip(LIVE_CONTRACT_ID, db_path=pre_v46_db)


# ---------------------------------------------------------------------------
# Where the operator actually meets the difference
# ---------------------------------------------------------------------------

def test_the_cli_says_no_such_row_only_when_that_is_what_it_found(db):
    result = _run_chain(db, ABSENT_CONTRACT_ID)
    assert result.returncode == 1
    assert "no agent_contract_handoffs row exists" in result.stdout + result.stderr


def test_the_cli_does_not_claim_absence_when_it_could_not_look(pre_v46_db):
    result = _run_chain(pre_v46_db, LIVE_CONTRACT_ID)
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "could not be read" in output
    assert "no agent_contract_handoffs row exists" not in output, (
        "the row does exist; reporting it as absent is the defect being fixed"
    )
