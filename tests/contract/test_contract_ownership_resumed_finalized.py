"""A resumed turn whose contract is FINALIZED still has a working path to close.

The ownership guard denies `gaia contract init` for a turn that owns a stamped
row. For a closed row that denial must not strand the turn: the command it
names (`gaia contract set --draft-id <closed>`) has to open a linked
continuation that the same guard then lets the turn write and close.

Real DB, real contract CLI (subprocess), real guard; nothing is mocked.
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
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from modules.security import contract_ownership_guard as guard  # noqa: E402
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
AGENT_ID = valid_agent_id("resumed-finalized-owner")
HARNESS_ID = valid_agent_id("resumed-finalized-harness")
CLOSED_ID = f"{AGENT_ID}.first-turn"
EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    for var in ("GAIA_DB", "GAIA_DB_PATH", "CLAUDE_SESSION_ID", "GAIA_DISPATCH_AGENT"):
        monkeypatch.delenv(var, raising=False)
    return dict(os.environ)


def _cli(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=env, timeout=30,
    )


def _close_first_turn(db: Path) -> None:
    insert_dispatched_handoff(
        contract_id=CLOSED_ID, agent_id=AGENT_ID, workspace="me",
        session_id="sess", kind="task_execution", agent_name="gaia-system",
        dispatch_prompt_id="p1", db_path=db,
    )
    stamp_harness_agent_id(CLOSED_ID, HARNESS_ID, db_path=db)
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE", "agent_id": AGENT_ID,
            "pending_steps": [], "next_action": "done",
        },
        "evidence_report": {
            **{k: [] for k in EVIDENCE_KEYS},
            "verification": {"method": "t", "result": "pass", "details": "t"},
        },
        "consolidation_report": None, "approval_request": None,
    }
    finalize_agent_contract_handoff(
        contract_id=CLOSED_ID, agent_id=AGENT_ID, workspace="me",
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
        session_id="sess", db_path=db,
    )


def _link_of(db: Path) -> dict:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM agent_contract_handoffs WHERE contract_id != ?", (CLOSED_ID,)
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 1, "exactly one continuation link was expected"
    return dict(rows[0])


def test_resumed_turn_on_a_finalized_contract_can_still_close(env):
    db = Path(env["GAIA_DATA_DIR"]) / "gaia.db"
    _close_first_turn(db)

    allowed, reason = guard.check("gaia contract init", HARNESS_ID)
    assert not allowed and CLOSED_ID in reason

    # The command the denial names must itself work on the closed row.
    opened = _cli(env, "set", "work_phase", "executing", "--draft-id", CLOSED_ID)
    assert opened.returncode == 0, opened.stderr
    link = _link_of(db)
    assert link["harness_agent_id"] == HARNESS_ID
    assert link["agent_state"] == "DISPATCHED"
    link_id = link["contract_id"]

    # Init is now refused for the live link, and writes to it are not.
    assert not guard.check("gaia contract init", HARNESS_ID)[0]
    assert guard.check(f"gaia contract add --draft-id {link_id}", HARNESS_ID) == (True, None)

    status = {
        "agent_state": "NEEDS_VERIFICATION", "agent_id": AGENT_ID,
        "pending_steps": [], "next_action": "verify",
    }
    done = _cli(env, "set", "agent_status", json.dumps(status), "--draft-id", link_id)
    assert done.returncode == 0, done.stderr
    closed = _cli(env, "finalize", "--draft-id", link_id)
    assert closed.returncode == 0, closed.stderr
    assert _link_of(db)["agent_state"] == "NEEDS_VERIFICATION"
