"""AC-8 (task 815, T9): OpenCode closes a child's turn through the same SubagentStop gate as Claude Code.

The Python half runs ``bridge.handle`` in-process on the ``session.idle`` the
plugin forwards for a dispatched child; the plugin half drives the real
``GaiaOpenCodePlugin`` under bun with a stubbed client and a scripted bridge.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _path in (str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "opencode")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tests.conftest import IsolatedRuntimeEnv

PARENT_SESSION = "ses-parent"
CHILD_SESSION = "ses-child-gate"
MINTED_AGENT_ID = "a" + "c" * 16
CONTRACT_ID = f"{MINTED_AGENT_ID}.feedc0de"
AGENT = "gaia-system"
SUMMARY = "The parity gate now returns unfinished turns to the specialist."
DRIVER = _ROOT / "tests" / "opencode" / "subagent_stop_gate_driver.ts"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_WORKSPACE", "me")
    monkeypatch.setenv("GAIA_HOST", "opencode")
    for name in (
        "GAIA_DB", "GAIA_DISPATCH_AGENT", "GAIA_CONTRACT_MAX_REJECTIONS",
        "GAIA_CONTRACT_FULL_VERDICT_GATE",
    ):
        monkeypatch.delenv(name, raising=False)


def _db(tmp_path) -> Path:
    return tmp_path / "gaia.db"


def _born_child(db: Path) -> None:
    """The row a Task dispatch births, claimed and bound to the child session as plugin.ts binds it."""
    from gaia.store.writer import (
        bind_harness_child_session,
        claim_dispatch_row,
        insert_dispatched_handoff,
    )

    insert_dispatched_handoff(
        CONTRACT_ID, MINTED_AGENT_ID, "me",
        session_id=PARENT_SESSION, db_path=db,
        agent_name=AGENT, kind="investigation",
        dispatch_tool_use_id="call-gate",
    )
    claim_dispatch_row(dispatch_tool_use_id="call-gate", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id="call-gate", harness_agent_id=CHILD_SESSION, db_path=db,
    )


def _finalize(db: Path) -> None:
    from gaia.store.writer import finalize_agent_contract_handoff

    evidence = {key: [] for key in _EVIDENCE_KEYS}
    evidence["verification"] = {
        "type": "command", "command": "pytest -q", "method": "suite",
        "result": "pass", "details": "green",
    }
    envelope = {
        "agent_status": {
            "agent_state": "COMPLETE", "agent_id": MINTED_AGENT_ID,
            "pending_steps": [], "next_action": "done",
        },
        "evidence_report": evidence,
        "consolidation_report": None,
        "approval_request": None,
        "user_facing_summary": SUMMARY,
    }
    finalize_agent_contract_handoff(
        contract_id=CONTRACT_ID, agent_id=MINTED_AGENT_ID, workspace="me",
        agent_state="COMPLETE", raw_handoff_json=json.dumps(envelope),
        session_id=PARENT_SESSION, db_path=db,
    )


def _idle(session_id: str = CHILD_SESSION) -> dict:
    import bridge

    return bridge.handle({"event": "session.idle", "sessionID": session_id, "agent": AGENT})


def _query(db: Path, sql: str) -> list[sqlite3.Row]:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_subagent_stop_gate_returns_an_unfinalized_child_turn_for_repair(tmp_path):
    db = _db(tmp_path)
    _born_child(db)

    response = _idle()

    assert response["contract_valid"] is False
    assert response["repair_prompt"].startswith("[CONTRACT REJECTED]")
    assert CONTRACT_ID in response["repair_prompt"]
    assert CHILD_SESSION in response["orchestrator_notice"]
    assert response["closed"]["status"] == "repair_requested"

    _finalize(db)
    repaired = _idle()

    assert repaired["contract_valid"] is True, "the child's own finalize must still close the turn it was sent back"
    assert SUMMARY in repaired["user_message"]


def test_subagent_stop_gate_cuts_after_the_claude_code_rejection_limit(tmp_path):
    from modules.agents.rejection_circuit import max_rejections

    db = _db(tmp_path)
    _born_child(db)
    limit = max_rejections()

    responses = [_idle() for _ in range(limit)]

    assert [r["contract_valid"] for r in responses[:-1]] == [False] * (limit - 1)
    cut = responses[-1]
    assert "repair_prompt" not in cut
    assert cut["contract_circuit_open"] is True
    assert cut["contract_rejection_count"] == limit
    assert "CORTADO" in cut["user_message"]
    row = _query(db, f"SELECT agent_state, cut_reason FROM agent_contract_handoffs WHERE contract_id = '{CONTRACT_ID}'")
    assert row[0]["agent_state"] != "DISPATCHED"
    assert row[0]["cut_reason"] is not None, "a cut turn is never recorded as a clean close"


def test_subagent_stop_gate_relays_the_user_facing_summary(tmp_path):
    db = _db(tmp_path)
    _born_child(db)
    _finalize(db)

    response = _idle()

    assert response["contract_valid"] is True
    assert "repair_prompt" not in response
    assert SUMMARY in response["user_message"]
    assert response["closed"] == {"status": "already_closed", "contract_id": CONTRACT_ID}


def test_subagent_stop_gate_writes_the_episode_and_workflow_audit(tmp_path):
    db = _db(tmp_path)
    _born_child(db)

    _idle()

    episodes = _query(db, "SELECT episode_id, agent, session_id FROM episodes")
    assert [(e["agent"], e["session_id"]) for e in episodes] == [(AGENT, PARENT_SESSION)]
    anomalies = _query(
        db, f"SELECT payload FROM episode_anomalies WHERE episode_id = '{episodes[0]['episode_id']}'",
    )
    codes = {json.loads(a["payload"]).get("code") for a in anomalies}
    assert "ROW_NOT_FINALIZED" in codes
    rejections = _query(db, "SELECT agent FROM harness_events WHERE type = 'agent.contract_rejected'")
    assert [r["agent"] for r in rejections] == [AGENT]


def test_subagent_stop_gate_leaves_the_main_session_idle_to_its_heartbeat(tmp_path):
    db = _db(tmp_path)
    _born_child(db)

    assert _idle(PARENT_SESSION) == {"contract_valid": True, "closed": {"status": "no_row"}}


def test_subagent_stop_gate_parity_alarm_no_longer_lists_it_pending():
    from adapters.opencode_parity import CAPABILITIES, PENDING_GAPS

    gate = next(c for c in CAPABILITIES if c.name == "SubagentStop gate and relay")
    assert gate.opencode_event == "session.idle"
    assert "SubagentStop gate and relay" not in PENDING_GAPS


def _drive(tmp_path) -> dict:
    env = IsolatedRuntimeEnv(tmp_path)
    env.prepare_hook_workspace()
    result = subprocess.run(
        ["bun", str(DRIVER)], cwd=env["WORKSPACE"], env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_subagent_stop_gate_plugin_reprompts_the_child_and_relays_to_the_parent(tmp_path):
    driven = _drive(tmp_path)

    assert driven["prompts"] == [{
        "path": {"id": "ses-child-1"},
        "body": {"parts": [{"type": "text", "text": "[CONTRACT REJECTED] repair me", "synthetic": True}]},
    }]
    assert "continue task_id ses-child-1" in driven["firstTaskOutput"]
    assert "Summary for the user" in driven["secondTaskOutput"]
    assert "continue task_id" not in driven["secondTaskOutput"]


def test_subagent_stop_gate_plugin_does_not_loop_on_its_own_repair_part(tmp_path):
    driven = _drive(tmp_path)

    assert driven["sendsAfterSyntheticRepair"] == 0
    assert len(driven["prompts"]) == 1
