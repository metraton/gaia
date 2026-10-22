"""Plan 94 T12: no conclusion of this session's dispatches is delivered zero times or twice, across session lifecycle orderings."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from tests.contract.test_conclusion_to_orchestrator import (  # noqa: F401
    _REPO_ROOT, AGENT_ID, CHILD_SESSION, CONCLUSION, HARNESS_AGENT_ID, SESSION_ID, WORKSPACE,
    _claude_code_stop, _envelope, _finalize, _isolated, db,
)
from tests.fixtures.agent_ids import valid_agent_id
from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import HookEvent, HookEventType
from gaia import conclusions
from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id


def _post(status: str, harness: str = HARNESS_AGENT_ID) -> str:
    payload = {
        "hook_event_name": "PostToolUse", "tool_name": "Agent", "session_id": SESSION_ID,
        "tool_input": {"description": "d", "prompt": "p", "subagent_type": "gaia-system",
                       "run_in_background": status == "async_launched"},
        "tool_response": {"status": status, "agentId": harness,
                          "content": [{"type": "text", "text": "x"}]},
    }
    out = ClaudeCodeAdapter().adapt_post_tool_use(
        HookEvent(event_type=HookEventType.POST_TOOL_USE, session_id=SESSION_ID, payload=payload)
    ).output or {}
    assert "systemMessage" not in out
    return json.dumps(out)


def _prompt() -> str:
    done = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "hooks" / "user_prompt_submit.py")],
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": SESSION_ID, "prompt": "next"}),
        capture_output=True, text=True, env=dict(os.environ), timeout=60, check=True,
    )
    out = json.loads(done.stdout)
    assert "systemMessage" not in out
    return json.dumps(out)


def _bg(db, n: int, text: str, launch: bool = True) -> str:
    harness = valid_agent_id(f"probe-harness-{n}")
    contract_id = f"{AGENT_ID}.p{n}"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, harness, db_path=db)
    launched = _post("async_launched", harness) if launch else ""
    assert text not in launched
    _finalize(db, contract_id, _envelope(conclusion=text))
    return harness


def test_probe_ledger_is_isolated(tmp_path):
    conclusions.baseline(SESSION_ID)
    assert str(conclusions._ledger_dir(SESSION_ID)).startswith(str(tmp_path))


def test_probe_two_background_closes_before_one_prompt(db):
    conclusions.baseline(SESSION_ID)
    texts = ["alpha conclusion one", "beta conclusion two"]
    for n, text in enumerate(texts):
        _bg(db, n, text)
    first, second = _prompt(), _prompt()
    assert [first.count(t) for t in texts] == [1, 1], first
    assert [second.count(t) for t in texts] == [0, 0], second


def test_probe_sync_close_with_racing_prompt(db):
    conclusions.baseline(SESSION_ID)
    contract_id = f"{AGENT_ID}.race"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db)
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))
    stop = json.dumps(_claude_code_stop())
    raced = _prompt()
    result = _post("completed")
    after = _prompt()
    counts = [s.count(CONCLUSION) for s in (stop, raced, result, after)]
    assert "systemMessage" not in stop
    assert sum(counts) == 1, counts


def test_probe_five_pending_conclusions(db):
    conclusions.baseline(SESSION_ID)
    texts = [f"conclusion number {n} xyz" for n in range(5)]
    for n, text in enumerate(texts):
        _bg(db, n, text)
    prompts = [_prompt() for _ in range(3)]
    per_text = [[p.count(t) for p in prompts] for t in texts]
    assert all(sum(row) == 1 for row in per_text), per_text
    assert sum(prompts[0].count(t) for t in texts) == 3, per_text
    assert "--field conclusion" in prompts[0]


def test_probe_resume_keeps_ledger_without_session_end(db):
    conclusions.baseline(SESSION_ID)
    _bg(db, 1, "resume conclusion kept")
    conclusions.baseline(SESSION_ID)
    assert _prompt().count("resume conclusion kept") == 1


def test_probe_background_close_pending_across_session_end_and_resume(db):
    from modules.session.session_lifecycle import end_session

    conclusions.baseline(SESSION_ID)
    _bg(db, 2, "pending across exit")
    end_session(SESSION_ID)
    conclusions.baseline(SESSION_ID)
    assert _prompt().count("pending across exit") == 1


def test_probe_old_closed_rows_resume_do_not_flood(db):
    _bg(db, 3, "closed long ago", launch=False)
    conclusions.baseline(SESSION_ID)
    assert _prompt().count("closed long ago") == 0


def test_probe_no_baseline_first_prompt_swallows_pending(db):
    _bg(db, 4, "closed before first prompt")
    first, second = _prompt(), _prompt()
    assert first.count("closed before first prompt") + second.count("closed before first prompt") == 1


def _launch_only(db, n: int) -> str:
    contract_id = f"{AGENT_ID}.l{n}"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, valid_agent_id(f"launch-harness-{n}"), db_path=db)
    _post("async_launched", valid_agent_id(f"launch-harness-{n}"))
    return contract_id


def test_close_after_session_end_is_delivered_once_on_resume(db):
    from modules.session.session_lifecycle import end_session

    conclusions.baseline(SESSION_ID)
    contract_id = _launch_only(db, 1)
    end_session(SESSION_ID)
    _finalize(db, contract_id, _envelope(conclusion="closed while away"))
    conclusions.baseline(SESSION_ID)
    assert [_prompt().count("closed while away") for _ in range(2)] == [1, 0]


def test_prompt_between_launch_and_close_does_not_consume_it(db):
    conclusions.baseline(SESSION_ID)
    contract_id = _launch_only(db, 2)
    before = _prompt()
    _finalize(db, contract_id, _envelope(conclusion="closed after a prompt"))
    assert [before.count("closed after a prompt")] + [_prompt().count("closed after a prompt") for _ in range(2)] == [0, 1, 0]


def test_sync_delivery_is_not_repeated_after_session_end_and_resume(db):
    from modules.session.session_lifecycle import end_session

    conclusions.baseline(SESSION_ID)
    contract_id = f"{AGENT_ID}.sync"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db)
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))
    result = _post("completed")
    end_session(SESSION_ID)
    conclusions.baseline(SESSION_ID)
    assert [result.count(CONCLUSION), _prompt().count(CONCLUSION)] == [1, 0]


def test_repeated_session_starts_keep_a_pending_close(db):
    conclusions.baseline(SESSION_ID)
    _bg(db, 5, "pending through compactions")
    for _ in range(3):
        conclusions.baseline(SESSION_ID)
    assert [_prompt().count("pending through compactions") for _ in range(2)] == [1, 0]


def test_first_prompt_without_ledger_opens_it_so_a_later_start_cannot_swallow(db):
    assert _prompt().count("conclusion") == 0
    _bg(db, 6, "closed after an empty first prompt")
    conclusions.baseline(SESSION_ID)
    assert _prompt().count("closed after an empty first prompt") == 1


def test_summary_line_without_a_session_id_still_claims_for_the_row_session(db):
    from modules.agents.task_result_observer import build_contract_summary_line

    conclusions.baseline(SESSION_ID)
    contract_id = f"{AGENT_ID}.nosession"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    stamp_harness_agent_id(contract_id, HARNESS_AGENT_ID, db_path=db)
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION))
    line = build_contract_summary_line({"status": "completed", "agentId": HARNESS_AGENT_ID}, session_id="")
    again = build_contract_summary_line({"status": "completed", "agentId": HARNESS_AGENT_ID}, session_id="")
    assert [line.count(CONCLUSION), again.count(CONCLUSION), _prompt().count(CONCLUSION)] == [1, 0, 0]


def test_ledger_pruned_while_the_session_lives_is_rebaselined_not_redelivered(db):
    conclusions.baseline(SESSION_ID)
    _bg(db, 7, "delivered before idle month")
    delivered = _prompt().count("delivered before idle month")
    os.utime(conclusions._ledger_dir(SESSION_ID), (0, 0))
    conclusions.baseline("another-session")
    _bg(db, 9, "closed after the prune")
    again = _prompt()
    assert [delivered, again.count("delivered before idle month")] == [1, 0]
    assert [again.count("closed after the prune"), _prompt().count("closed after the prune")] == [1, 0]


def test_refinalized_contract_delivers_its_new_conclusion(db):
    conclusions.baseline(SESSION_ID)
    contract_id = f"{AGENT_ID}.refin"
    insert_dispatched_handoff(contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db)
    _finalize(db, contract_id, _envelope(state="BLOCKED", conclusion="first conclusion blocked"))
    first = _prompt()
    _finalize(db, contract_id, _envelope(conclusion="second conclusion complete"))
    second, third = _prompt(), _prompt()
    assert [first.count("first conclusion blocked"), second.count("first conclusion blocked")] == [1, 0]
    assert [second.count("second conclusion complete"), third.count("second conclusion complete")] == [1, 0]


def test_prune_removes_only_aged_ledgers():
    conclusions.claim("old-session", "c.1", "one")
    conclusions.claim(SESSION_ID, "c.2", "two")
    aged = conclusions._ledger_dir("old-session")
    os.utime(aged, (0, 0))
    conclusions.prune()
    assert not aged.exists() and conclusions._ledger_dir(SESSION_ID).is_dir()
    assert conclusions._tombstone("old-session").is_file() and not conclusions._tombstone(SESSION_ID).exists()
