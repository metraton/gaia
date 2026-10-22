"""Plan 94 T12: a background specialist's conclusion reaches the orchestrator once, on the next prompt."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from tests.contract.test_conclusion_to_orchestrator import (  # noqa: F401
    _REPO_ROOT, AGENT_ID, CHILD_SESSION, CONCLUSION, HARNESS_AGENT_ID, SESSION_ID, WORKSPACE,
    _claude_code_stop, _envelope, _finalize, _isolated, db,
)
from adapters.claude_code import ClaudeCodeAdapter
from adapters.opencode import OpenCodeAdapter
from adapters.types import HookEvent, HookEventType
from gaia import conclusions
from gaia.store.writer import (
    bind_harness_child_session, claim_dispatch_row, insert_dispatched_handoff, stamp_harness_agent_id,
)


def _cc_agent_result(status: str) -> dict:
    payload = {
        "hook_event_name": "PostToolUse", "tool_name": "Agent", "session_id": SESSION_ID,
        "tool_input": {"description": "d", "prompt": "p", "subagent_type": "gaia-system",
                       "run_in_background": status == "async_launched"},
        "tool_response": {"status": status, "agentId": HARNESS_AGENT_ID,
                          "content": [{"type": "text", "text": "x"}]},
    }
    return ClaudeCodeAdapter().adapt_post_tool_use(
        HookEvent(event_type=HookEventType.POST_TOOL_USE, session_id=SESSION_ID, payload=payload)
    ).output or {}


def _cc_prompt() -> dict:
    done = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "hooks" / "user_prompt_submit.py")],
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": SESSION_ID,
                          "prompt": "<task-notification>done</task-notification>"}),
        capture_output=True, text=True, env=dict(os.environ), timeout=60, check=True,
    )
    return json.loads(done.stdout)


def _cc_dispatch(db, suffix: str) -> str:
    contract_id = f"{AGENT_ID}.{suffix}"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db)
    return contract_id


def test_claude_code_background_close_reaches_the_next_prompt_once(db):
    conclusions.baseline(SESSION_ID)
    contract_id = _cc_dispatch(db, "bg")
    launch = _cc_agent_result("async_launched")
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))
    outputs = [launch, _claude_code_stop(), _cc_prompt(), _cc_prompt()]

    assert all("systemMessage" not in out for out in outputs)
    assert [json.dumps(out).count(CONCLUSION) for out in outputs] == [0, 0, 1, 0]
    assert HARNESS_AGENT_ID in outputs[2]["hookSpecificOutput"]["additionalContext"]


def test_claude_code_sync_close_is_not_repeated_on_the_next_prompt(db):
    conclusions.baseline(SESSION_ID)
    contract_id = _cc_dispatch(db, "sync")
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))
    outputs = [_claude_code_stop(), _cc_agent_result("completed"), _cc_prompt()]

    assert all("systemMessage" not in out for out in outputs)
    assert [json.dumps(out).count(CONCLUSION) for out in outputs] == [0, 1, 0]


def test_opencode_background_close_reaches_the_next_prompt_once(db, monkeypatch):
    import bridge

    monkeypatch.setenv("GAIA_HOST", "opencode")
    conclusions.baseline(SESSION_ID)
    contract_id = f"{AGENT_ID}.ocbg"
    insert_dispatched_handoff(
        contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db,
        agent_name="gaia-system", kind="investigation", dispatch_tool_use_id="call-bg",
    )
    claim_dispatch_row(dispatch_tool_use_id="call-bg", db_path=db)
    bind_harness_child_session(dispatch_tool_use_id="call-bg", harness_agent_id=CHILD_SESSION, db_path=db)
    adapter = OpenCodeAdapter()
    after = adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.after", "sessionID": SESSION_ID, "callID": "call-bg", "tool": "task",
        "args": {"description": "d", "prompt": "p", "subagent_type": "gaia-system"},
        "result": {"output": "launched", "metadata": {"sessionId": CHILD_SESSION, "background": True},
                   "exit_code": 0, "is_error": False},
    }))).output
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))
    stop = bridge.handle({"event": "session.idle", "sessionID": CHILD_SESSION, "agent": "gaia-system"})
    prompts = [bridge.handle({"event": "chat.prompt", "sessionID": SESSION_ID}) for _ in range(2)]

    assert CONCLUSION not in (stop.get("user_message") or "")
    carried = [(out.get("additional_context") or "").count(CONCLUSION) for out in (after, *prompts)]
    assert carried == [0, 1, 0]
