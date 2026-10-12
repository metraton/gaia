"""A turn that closes with empty evidence lists keeps the tool calls the host recorded for it.

Drives the real SubagentStop entry points against an isolated database: Claude
Code's ``ClaudeCodeAdapter.adapt_subagent_stop`` with a subagent transcript, and
OpenCode's ``bridge.handle`` on ``session.idle``. OpenCode hands the stop hook no
transcript and no other per-turn tool record, so on that host nothing can be
recovered; its cases pin that nothing is invented and that the close still holds.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "hooks"), str(_ROOT), str(_ROOT / "opencode")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    bind_harness_child_session,
    claim_dispatch_row,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

WORKSPACE = "me"
SESSION_ID = "sess-recover-evidence"
AGENT = "gaia-system"

_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    for name in ("GAIA_DB", "GAIA_DISPATCH_AGENT", "GAIA_CONTRACT_FULL_VERDICT_GATE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def db(tmp_path) -> Path:
    return tmp_path / "gaia_data" / "gaia.db"


def _envelope(agent_id: str, **lists) -> dict:
    evidence = {key: list(lists.get(key, [])) for key in _EVIDENCE_KEYS}
    evidence["verification"] = {
        "type": "command", "command": "pytest -q", "method": "suite",
        "result": "pass", "details": "green",
    }
    return {
        "agent_status": {
            "agent_state": "NEEDS_VERIFICATION", "agent_id": agent_id,
            "pending_steps": [], "next_action": "verify",
        },
        "evidence_report": evidence,
        "consolidation_report": None,
        "approval_request": None,
    }


def _finalize(db: Path, contract_id: str, envelope: dict, session_id: str) -> None:
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=envelope["agent_status"]["agent_id"],
        workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope),
        session_id=session_id,
        db_path=db,
    )


def _persisted_evidence(db: Path, contract_id: str) -> dict:
    con = sqlite3.connect(str(db))
    try:
        (raw,) = con.execute(
            "SELECT raw_handoff_json FROM agent_contract_handoffs WHERE contract_id = ?",
            (contract_id,),
        ).fetchone()
    finally:
        con.close()
    return json.loads(raw)["evidence_report"]


def _transcript(tmp_path: Path, tool_calls: list) -> str:
    """A Claude Code subagent transcript: one assistant entry per tool_use block."""
    lines = [{"type": "user", "message": {"role": "user", "content": "do the task"}}]
    for index, (name, tool_input) in enumerate(tool_calls):
        lines.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "tool_use", "id": f"tu{index}", "name": name, "input": tool_input}],
            },
        })
    lines.append({
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "Closed."}]},
    })
    path = tmp_path / "agent-transcript.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return str(path)


def _claude_code_close(db: Path, tmp_path: Path, token: str, envelope_lists: dict, tool_calls: list):
    agent_id = valid_agent_id(f"recover-{token}")
    harness_agent_id = valid_agent_id(f"recover-{token}-harness")
    contract_id = f"{agent_id}.{token}"
    insert_dispatched_handoff(
        contract_id=contract_id, agent_id=agent_id, workspace=WORKSPACE,
        session_id=SESSION_ID, db_path=db,
    )
    stamp_harness_agent_id(contract_id, harness_agent_id, db_path=db)
    _finalize(db, contract_id, _envelope(agent_id, **envelope_lists), SESSION_ID)

    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": AGENT,
        "agent_id": harness_agent_id,
        "agent_transcript_path": _transcript(tmp_path, tool_calls),
        "last_assistant_message": "Closed.",
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))
    return contract_id, adapter.adapt_subagent_stop(event)


_TOOL_CALLS = [
    ("Bash", {"command": "git -C /repo status --short"}),
    ("Read", {"file_path": "/repo/hooks/a.py"}),
    ("Bash", {"command": "python3 -m pytest tests/x.py -q"}),
    ("Read", {"file_path": "/repo/hooks/a.py"}),
    ("Bash", {"command": "gaia contract finalize --draft-id x.y"}),
]


def test_a_empty_lists_are_filled_from_the_transcript_and_marked(db, tmp_path):
    contract_id, response = _claude_code_close(db, tmp_path, "aaa1", {}, _TOOL_CALLS)

    assert response.exit_code == 0
    evidence = _persisted_evidence(db, contract_id)
    assert evidence["commands_run"], "the row kept empty lists although the transcript holds the turn's tool calls"
    from modules.agents.evidence_recovery import RECOVERED_MARK

    assert evidence["commands_run"] == [
        f"{RECOVERED_MARK}git -C /repo status --short",
        f"{RECOVERED_MARK}python3 -m pytest tests/x.py -q",
    ]
    assert evidence["files_checked"] == [f"{RECOVERED_MARK}/repo/hooks/a.py"]
    assert evidence["verbatim_outputs"] == []


def test_b_lists_the_agent_filled_stay_as_written(db, tmp_path):
    written = {
        "commands_run": ["git status -> clean"],
        "files_checked": ["hooks/b.py"],
    }
    contract_id, response = _claude_code_close(db, tmp_path, "bbb2", written, _TOOL_CALLS)

    assert response.exit_code == 0
    evidence = _persisted_evidence(db, contract_id)
    assert evidence["commands_run"] == written["commands_run"]
    assert evidence["files_checked"] == written["files_checked"]


def test_c_a_turn_that_ran_no_tool_keeps_empty_lists_and_is_not_refused(db, tmp_path):
    contract_id, response = _claude_code_close(db, tmp_path, "ccc3", {}, [])

    assert response.exit_code == 0
    assert response.output.get("contract_rejected") is not True
    evidence = _persisted_evidence(db, contract_id)
    assert evidence["commands_run"] == []
    assert evidence["files_checked"] == []


CHILD_SESSION = "ses-child-recover"


def _opencode_close(db: Path, token: str, envelope_lists: dict) -> tuple:
    import bridge

    agent_id = valid_agent_id(f"oc-{token}")
    contract_id = f"{agent_id}.{token}"
    child = f"{CHILD_SESSION}-{token}"
    insert_dispatched_handoff(
        contract_id, agent_id, WORKSPACE, session_id="ses-parent", db_path=db,
        agent_name=AGENT, kind="investigation", dispatch_tool_use_id=f"call-{token}",
    )
    claim_dispatch_row(dispatch_tool_use_id=f"call-{token}", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id=f"call-{token}", harness_agent_id=child, db_path=db,
    )
    _finalize(db, contract_id, _envelope(agent_id, **envelope_lists), "ses-parent")
    return contract_id, bridge.handle({"event": "session.idle", "sessionID": child, "agent": AGENT})


@pytest.fixture()
def opencode_host(monkeypatch):
    monkeypatch.setenv("GAIA_HOST", "opencode")


def test_d_opencode_records_no_tool_calls_so_nothing_is_invented(db, opencode_host):
    contract_id, response = _opencode_close(db, "ddd4", {})

    assert response["contract_valid"] is True
    evidence = _persisted_evidence(db, contract_id)
    assert evidence["commands_run"] == []
    assert evidence["files_checked"] == []


def test_d_opencode_keeps_lists_the_agent_filled(db, opencode_host):
    written = {"commands_run": ["pytest -q -> 3 passed"], "files_checked": ["hooks/c.py"]}
    contract_id, response = _opencode_close(db, "eee5", written)

    assert response["contract_valid"] is True
    evidence = _persisted_evidence(db, contract_id)
    assert evidence["commands_run"] == written["commands_run"]
    assert evidence["files_checked"] == written["files_checked"]
