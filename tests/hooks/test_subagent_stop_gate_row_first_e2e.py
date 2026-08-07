"""End-to-end wiring of the row-first SubagentStop gate.

``tests/hooks/modules/agents/test_contract_gate_row_first.py`` exercises
``resolve_subagent_stop_gate`` directly. This file proves the SAME inversion
holds through the real production entry point,
``ClaudeCodeAdapter.adapt_subagent_stop`` -- driven exactly as Claude Code
drives it (a JSON SubagentStop payload through ``parse_event``), against a
real, isolated SQLite database. No adapter method or gate function is
stubbed; only the substrate paths are isolated (mirrors
tests/contract/test_truncation_salvage.py's end-to-end fixtures).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).resolve().parents[2] / "hooks")
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
for _p in (_HOOKS_DIR, _REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
AGENT_ID = valid_agent_id("row-first-e2e")
# The harness's OWN per-run id (v40, hook_data['agent_id'] / harness_agent_id)
# -- a DIFFERENT identifier space from AGENT_ID (the CLI-minted identity a
# turn adopts), by design (see ClaudeCodeAdapter._resolve_dispatch_row). Every
# test below that means to reproduce a REAL dispatch keeps these two distinct
# on purpose: a test that (accidentally) sets hook_data['agent_id'] to the
# SAME value as the minted identity masks exactly the gap MEASURED live
# (handoff 11263) -- see test_finalized_row_passes_the_real_hook_with_no_fence.
HARNESS_AGENT_ID = valid_agent_id("row-first-e2e-harness")
SESSION_ID = "sess-row-first-e2e"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


def _evidence() -> dict:
    return {k: [] for k in _EVIDENCE_KEYS}


def _complete_envelope() -> dict:
    env = {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": AGENT_ID,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }
    env["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": "suite green",
    }
    return env


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_CONTRACT_FULL_VERDICT_GATE", raising=False)
    yield


@pytest.fixture()
def default_db(tmp_path) -> Path:
    """The DB the adapter resolves by default (GAIA_DATA_DIR/gaia.db) when
    the hook payload carries no explicit db_path -- exactly how the real
    hook runs."""
    return tmp_path / "gaia_data" / "gaia.db"


def _birth_row(db_path: Path, *, contract_suffix: str) -> str:
    contract_id = f"{AGENT_ID}.{contract_suffix}"
    insert_dispatched_handoff(
        contract_id=contract_id,
        agent_id=AGENT_ID,
        workspace=WORKSPACE,
        session_id=SESSION_ID,
        db_path=db_path,
    )
    return contract_id


def _finalize_row(db_path: Path, contract_id: str, envelope: dict) -> None:
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=envelope["agent_status"]["agent_id"],
        workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope),
        session_id=SESSION_ID,
        db_path=db_path,
    )


def _subagent_stop_event(
    adapter: ClaudeCodeAdapter, *,
    agent_output: str,
    stop_reason: str = "end_turn",
    harness_agent_id: str = HARNESS_AGENT_ID,
):
    payload = {
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": "gaia-system",
        # The harness's own per-run id, NOT the CLI-minted identity -- see
        # HARNESS_AGENT_ID's module comment. A real SubagentStop payload
        # never carries the minted id here.
        "agent_id": harness_agent_id,
        "agent_transcript_path": "",
        "last_assistant_message": agent_output,
        "stop_reason": stop_reason,
        "cwd": "/tmp",
    }
    return adapter.parse_event(json.dumps(payload))


def _row_state(db_path: Path, contract_id: str) -> "sqlite3.Row | None":
    if not db_path.is_file():
        return None
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(
            "SELECT agent_state, cut_reason FROM agent_contract_handoffs "
            "WHERE contract_id = ?", (contract_id,),
        ).fetchone()
    finally:
        con.close()


# ---------------------------------------------------------------------------
# A finalized, well-formed row passes through the REAL hook entry point even
# when the agent's final message carries no fence at all.
# ---------------------------------------------------------------------------

def test_finalized_row_passes_the_real_hook_with_no_fence(default_db):
    """The exact shape MEASURED live (handoff 11263): the turn ADOPTED the
    identity injected for it at dispatch (never ran `gaia contract init`
    itself), closed cleanly via `gaia contract finalize`, and its final
    message carries NO fence at all. Lanes 1-3 of `_resolve_dispatch_row`
    all miss here (no fence to read agent_id from, no mint report in an
    empty transcript, row already terminal not DISPATCHED) -- only the
    harness_agent_id join (lane 4, stamped at SubagentStart claim) finds it.
    """
    contract_id = _birth_row(default_db, contract_suffix="e2e-pass")
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=default_db)
    _finalize_row(default_db, contract_id, _complete_envelope())

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(
        adapter,
        agent_output="All done -- the fix landed and the suite is green.",
    )
    response = adapter.adapt_subagent_stop(event)

    assert response.output.get("contract_rejected") is not True
    assert response.output.get("contract_gate_source") == "row"


# ---------------------------------------------------------------------------
# A row that exists but was never finalized must not pass silently through
# the real hook, however well-formed the agent's fence is.
# ---------------------------------------------------------------------------

def test_unfinalized_row_rejects_through_the_real_hook_despite_a_perfect_fence(default_db):
    _birth_row(default_db, contract_suffix="e2e-unfinalized")  # never finalized

    envelope = _complete_envelope()
    agent_output = (
        "All done.\n\n```agent_contract_handoff\n"
        + json.dumps(envelope) + "\n```\n"
    )

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output=agent_output, stop_reason="end_turn")
    response = adapter.adapt_subagent_stop(event)

    assert response.output.get("contract_rejected") is True
    assert response.output.get("contract_gate_source") == "row_unfinalized"
    assert "gaia contract finalize" in response.output.get("contract_rejection_reason", "")
    assert response.exit_code == 2

    # The row is never a clean close: whatever the SAME hook call's backstop
    # (persist_handoff, unrelated to the gate) does with it afterwards, it
    # cannot land agent_state=COMPLETE with cut_reason=NULL -- the honest
    # verdict this migration exists to make authoritative.
    row = _row_state(default_db, f"{AGENT_ID}.e2e-unfinalized")
    assert row is not None
    assert not (row["agent_state"] == "COMPLETE" and row["cut_reason"] is None)
    assert row["cut_reason"] is not None


# ---------------------------------------------------------------------------
# No dispatch row was ever born for this session/agent. This case used to fall
# back to the fence and PASS on a well-formed one; the retirement makes it a
# rejection, and this test is the inversion that proves it end to end.
# ---------------------------------------------------------------------------

def test_no_dispatch_row_rejects_through_the_real_hook_however_good_the_fence(default_db):
    """The strongest form of the retirement: a flawless fenced envelope in the
    final message, and no persisted row. Before, this passed the close on the
    strength of the fence alone -- which is exactly the turn that never wrote
    a contract presenting itself as one that did."""
    envelope = _complete_envelope()
    agent_output = (
        "All done.\n\n```agent_contract_handoff\n"
        + json.dumps(envelope) + "\n```\n"
    )

    adapter = ClaudeCodeAdapter()
    event = _subagent_stop_event(adapter, agent_output=agent_output, stop_reason="end_turn")
    response = adapter.adapt_subagent_stop(event)

    assert response.output.get("contract_rejected") is True
    assert response.output.get("contract_gate_source") == "row_missing"
    assert response.exit_code == 2
    assert "gaia contract finalize" in response.output.get("contract_rejection_reason", "")
