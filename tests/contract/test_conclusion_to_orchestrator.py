"""Plan 94 T12: a specialist's conclusion is addressed to the orchestrator and never relayed to the user's screen."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "hooks"), str(_REPO_ROOT / "opencode")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402
from adapters.types import HookEvent, HookEventType  # noqa: E402
from gaia.store.writer import (  # noqa: E402
    bind_harness_child_session,
    claim_dispatch_row,
    finalize_agent_contract_handoff,
    insert_dispatched_handoff,
    stamp_harness_agent_id,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CONTRACT_CLI = _REPO_ROOT / "bin" / "cli" / "contract.py"
WORKSPACE = "me"
SESSION_ID = "sess-conclusion"
AGENT_ID = valid_agent_id("conclusion")
HARNESS_AGENT_ID = valid_agent_id("conclusion-harness")
CHILD_SESSION = "ses-child-conclusion"
CONCLUSION = "Gate 1808 green at 3f2a1c0; the OpenCode notice path is untested under bun."
_EVIDENCE_KEYS = (
    "patterns_checked", "files_checked", "commands_run", "key_outputs",
    "verbatim_outputs", "cross_layer_impacts", "open_gaps",
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    for var in (
        "GAIA_DB", "GAIA_DB_PATH", "CLAUDE_SESSION_ID", "GAIA_DISPATCH_AGENT",
        "GAIA_CONTRACT_FULL_VERDICT_GATE", "GAIA_CONTRACT_MAX_REJECTIONS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def db(tmp_path) -> Path:
    return tmp_path / "gaia_data" / "gaia.db"


def _envelope(state: str = "COMPLETE", **text_fields) -> dict:
    evidence = {k: [] for k in _EVIDENCE_KEYS}
    evidence["verification"] = {
        "type": "command", "command": "pytest -q", "method": "suite",
        "result": "pass", "details": "green",
    }
    return {
        "agent_status": {
            "agent_state": state, "agent_id": AGENT_ID,
            "pending_steps": [], "next_action": "done",
        },
        "evidence_report": evidence,
        "consolidation_report": None,
        "approval_request": None,
        **text_fields,
    }


def _finalize(db: Path, contract_id: str, envelope: dict) -> None:
    finalize_agent_contract_handoff(
        contract_id=contract_id, agent_id=AGENT_ID, workspace=WORKSPACE,
        agent_state=envelope["agent_status"]["agent_state"],
        raw_handoff_json=json.dumps(envelope), session_id=SESSION_ID, db_path=db,
    )


def _claude_code_row(db: Path, field: str) -> None:
    contract_id = f"{AGENT_ID}.cc"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db)
    _finalize(db, contract_id, _envelope(**{field: CONCLUSION}))


def _claude_code_stop() -> dict:
    adapter = ClaudeCodeAdapter()
    return adapter.adapt_subagent_stop(adapter.parse_event(json.dumps({
        "hook_event_name": "SubagentStop",
        "session_id": SESSION_ID,
        "agent_type": "gaia-system",
        "agent_id": HARNESS_AGENT_ID,
        "agent_transcript_path": "",
        "last_assistant_message": "",
        "stop_reason": "end_turn",
        "cwd": "/tmp",
    }))).output


def _claude_code_agent_result() -> str:
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Agent",
        "session_id": SESSION_ID,
        "tool_input": {"description": "d", "prompt": "p", "subagent_type": "gaia-system"},
        "tool_response": {
            "status": "completed",
            "agentId": HARNESS_AGENT_ID,
            "content": [{"type": "text", "text": "COMPLETE"}],
        },
    }
    response = ClaudeCodeAdapter().adapt_post_tool_use(
        HookEvent(event_type=HookEventType.POST_TOOL_USE, session_id=SESSION_ID, payload=payload)
    )
    return response.output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("field", ["conclusion", "user_facing_summary"])
def test_claude_code_clean_close_puts_no_conclusion_on_the_user_screen(db, field):
    _claude_code_row(db, field)

    output = _claude_code_stop()

    assert output.get("contract_rejected") is not True, output
    assert "systemMessage" not in output
    assert CONCLUSION not in json.dumps(output)


@pytest.mark.parametrize("field", ["conclusion", "user_facing_summary"])
def test_claude_code_summary_line_carries_the_conclusion_once(db, field):
    _claude_code_row(db, field)
    _claude_code_stop()

    context = _claude_code_agent_result()

    assert context.count(CONCLUSION) == 1, context
    assert f"conclusion: {CONCLUSION}" in context


def test_opencode_task_result_carries_the_conclusion_once(db, monkeypatch):
    import bridge

    monkeypatch.setenv("GAIA_HOST", "opencode")
    contract_id = f"{AGENT_ID}.oc"
    insert_dispatched_handoff(
        contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db,
        agent_name="gaia-system", kind="investigation", dispatch_tool_use_id="call-oc",
    )
    claim_dispatch_row(dispatch_tool_use_id="call-oc", db_path=db)
    bind_harness_child_session(
        dispatch_tool_use_id="call-oc", harness_agent_id=CHILD_SESSION, db_path=db,
    )
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))

    stop = bridge.handle({"event": "session.idle", "sessionID": CHILD_SESSION, "agent": "gaia-system"})
    adapter = OpenCodeAdapter()
    after = adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.after",
        "sessionID": SESSION_ID,
        "callID": "call-oc",
        "tool": "task",
        "args": {"description": "d", "prompt": "p", "subagent_type": "gaia-system"},
        "result": {
            "output": "child finished", "metadata": {"sessionId": CHILD_SESSION},
            "exit_code": 0, "is_error": False,
        },
    }))).output

    assert stop["contract_valid"] is True, stop
    parent_notices = [stop.get("orchestrator_notice"), stop.get("user_message")]
    appended = "\n".join([after.get("additional_context") or "", *filter(None, parent_notices)])
    assert appended.count(CONCLUSION) == 1, appended
    assert f"conclusion: {CONCLUSION}" in after["additional_context"]


def _cli(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CONTRACT_CLI), *args],
        capture_output=True, text=True, env=dict(os.environ), timeout=60,
    )


def _draft(envelope: dict) -> str:
    init = _cli(["init", "--agent-id", AGENT_ID, "--json"])
    assert init.returncode == 0, init.stderr
    draft_id = json.loads(init.stdout)["draft_id"]
    filled = _cli(["fill", "--draft-id", draft_id, "--json", json.dumps(envelope)])
    assert filled.returncode == 0, filled.stderr
    return draft_id


@pytest.mark.parametrize("text_fields", [
    {}, {"conclusion": ""}, {"conclusion": "   "}, {"user_facing_summary": CONCLUSION},
])
def test_finalize_refuses_a_clean_close_without_conclusion_and_names_it(text_fields):
    draft_id = _draft(_envelope("NEEDS_VERIFICATION", **text_fields))

    fin = _cli(["finalize", "--draft-id", draft_id, "--json"])

    assert fin.returncode == 1, fin.stdout
    payload = json.loads(fin.stdout)
    assert payload["reason"] == "conclusion_required"
    assert f"gaia contract set --draft-id {draft_id} conclusion" in payload["error"]


def test_finalize_accepts_the_close_once_the_conclusion_is_set():
    draft_id = _draft(_envelope("NEEDS_VERIFICATION"))

    assert _cli(["set", "--draft-id", draft_id, "conclusion", CONCLUSION]).returncode == 0
    fin = _cli(["finalize", "--draft-id", draft_id, "--json"])

    assert fin.returncode == 0, fin.stderr
    assert json.loads(fin.stdout)["status"] == "finalized"


def test_a_row_with_only_user_facing_summary_reads_as_its_conclusion(db):
    contract_id = f"{AGENT_ID}.legacy"
    _finalize(db, contract_id, _envelope("NEEDS_VERIFICATION", user_facing_summary=CONCLUSION))
    _finalize(db, f"{AGENT_ID}.blank", _envelope("NEEDS_VERIFICATION"))

    view = _cli(["view", "--draft-id", contract_id, "--field", "conclusion"])
    rate = _cli(["fill-rate", "--since", "7d", "--json"])

    assert view.returncode == 0, view.stderr
    assert json.loads(view.stdout) == CONCLUSION
    assert rate.returncode == 0, rate.stderr
    assert json.loads(rate.stdout)["conclusion"] == {"filled": 1, "rate": 0.5}
