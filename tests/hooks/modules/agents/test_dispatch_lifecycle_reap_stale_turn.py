"""gate 1016 (plan 65 task 13) -- the dispatch_lifecycle.reap_stale_turn facade.

The four named cases already run against the host-neutral core
(``gaia.store.writer.reap_stale_dispatched_handoffs``,
see tests/contract/test_reap_stale_dispatched_handoffs.py). This file covers
the facade wrapper itself: the liveness predicate it wires in scans the
``GAIA_OPENCODE_ATTESTATION_DIR`` ledger for a record naming the row's OWN
``session_id`` column, treating a match whose ledger file is itself STALE
(mtime older than the same staleness window) the same as no match at all.

Runs against a fresh DB (``created_at`` is backdated by direct UPDATE, since
the facade has no ``now`` override -- it always measures against the real
wall clock) and an isolated ledger directory.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[4] / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from modules.agents.dispatch_lifecycle import reap_stale_turn  # noqa: E402
from modules.security.host_attestation import ATTESTATION_SCHEME  # noqa: E402

from gaia.store.writer import (  # noqa: E402
    agent_contract_handoff_state,
    insert_dispatched_handoff,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("a1234abcd")
OLDER_THAN_SECONDS = 60


@pytest.fixture(autouse=True)
def _clean_dispatch(monkeypatch):
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    yield


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "gaia.db"


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    directory = tmp_path / "ledgers"
    directory.mkdir()
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(directory))
    return directory


def _birth_stale_row(contract_id: str, session_id: str, db_path) -> None:
    insert_dispatched_handoff(
        contract_id, AGENT_ID, WORKSPACE, session_id=session_id, db_path=db_path,
    )
    con = sqlite3.connect(str(db_path))
    try:
        backdated = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600)
        )
        con.execute(
            "UPDATE agent_contract_handoffs SET created_at = ? WHERE contract_id = ?",
            (backdated, contract_id),
        )
        con.commit()
    finally:
        con.close()


def _write_ledger(ledger_dir: Path, host_run: str, session_id: str, *, fresh: bool) -> None:
    path = ledger_dir / f"{host_run}.json"
    payload = {
        "records": {
            ATTESTATION_SCHEME + "deadbeef": {
                "session_id": session_id,
                "role": "gaia-orchestrator",
                "issuer": "test",
                "depth": 0,
                "granted_by": None,
                "issued_at": "2020-01-01T00:00:00+00:00",
            }
        }
    }
    path.write_text(json.dumps(payload))
    if not fresh:
        stale_time = time.time() - 7200
        os.utime(path, (stale_time, stale_time))


def test_facade_reaps_a_stale_row_with_no_matching_ledger(db, ledger_dir):
    _birth_stale_row("facadeA.contract", "sess-facadeA", db)

    result = reap_stale_turn(older_than_seconds=OLDER_THAN_SECONDS, db_path=db)

    assert "facadeA.contract" in result["reaped"]
    assert agent_contract_handoff_state("facadeA.contract", db_path=db) != "DISPATCHED"


def test_facade_spares_a_row_with_a_fresh_matching_ledger(db, ledger_dir):
    _birth_stale_row("facadeB.contract", "sess-facadeB", db)
    _write_ledger(ledger_dir, "host-live", "sess-facadeB", fresh=True)

    result = reap_stale_turn(older_than_seconds=OLDER_THAN_SECONDS, db_path=db)

    assert "facadeB.contract" in result["spared"]
    assert agent_contract_handoff_state("facadeB.contract", db_path=db) == "DISPATCHED"


def test_facade_reaps_a_row_whose_matching_ledger_is_itself_stale(db, ledger_dir):
    _birth_stale_row("facadeC.contract", "sess-facadeC", db)
    _write_ledger(ledger_dir, "host-dead", "sess-facadeC", fresh=False)

    result = reap_stale_turn(older_than_seconds=OLDER_THAN_SECONDS, db_path=db)

    assert "facadeC.contract" in result["reaped"]
