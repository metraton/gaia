"""One helper words every proposal a finished turn carries, for the dispatch result the orchestrator reads.

``memory_delta`` and ``update_contracts`` are both proposals the orchestrator
curates; each gets its own line, and a turn that proposed neither adds none.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "hooks"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gaia import conclusions  # noqa: E402
from modules.agents.proposal_notices import proposal_notice  # noqa: E402
from tests.contract.test_conclusion_to_orchestrator import (  # noqa: E402,F401
    AGENT_ID, CHILD_SESSION, CONCLUSION, SESSION_ID, WORKSPACE, _envelope, _finalize, _isolated, db,
)
from tests.contract.test_conclusion_to_orchestrator_background import (  # noqa: E402
    _cc_agent_result, _cc_dispatch, _cc_prompt,
)

VERB = "gaia contract proposals list"
CONTRACT_ID = "a21d060aefc6855cc.0123456789ab"
FACT = {
    "operation": "create",
    "create": {"slug": "atom_alpha_admin_url", "type": "atom", "description": "d", "body": "b"},
}
UPDATE = {"contract": "project_identity", "payload": {"alpha": {"workflow": "w"}}}
EVIDENCE = {"contract": "evidence", "payload": {"brief_id": 1, "ac_id": "AC-1", "type": "text", "text": "t"}}


def _notice(contract) -> str:
    return proposal_notice(contract, contract_id=CONTRACT_ID, dispatch_project="alpha (/work/alpha)")


def test_a_project_context_proposal_names_its_count_contract_and_accept_verb():
    assert _notice({"update_contracts": [UPDATE, UPDATE]}) == (
        f"2 project-context updates proposed for alpha — contract {CONTRACT_ID}; "
        f"apply with `gaia context accept {CONTRACT_ID}`"
    )


def test_a_turn_proposing_both_kinds_gets_one_line_each_facts_first():
    lines = _notice({
        "memory_delta": {"version": 1, "proposals": [FACT]},
        "update_contracts": [UPDATE],
    }).splitlines()

    assert lines[0] == f"1 project fact proposed for alpha — contract {CONTRACT_ID}"
    assert lines[1].startswith(f"1 project-context update proposed for alpha — contract {CONTRACT_ID}")


def test_a_section_keyed_object_is_counted_and_says_it_was_reshaped():
    notice = _notice({"update_contracts": {"cluster_details": {"gke": {"version": "1.30"}}}})

    assert notice.startswith("1 project-context update proposed")
    assert "{section: payload}" in notice


@pytest.mark.parametrize("contract", [
    None,
    "not a dict",
    {},
    {"update_contracts": []},
    {"update_contracts": [EVIDENCE]},
    {"update_contracts": [{"contract": "project_identity"}]},
    {"update_contracts": "project_identity"},
])
def test_a_turn_with_nothing_for_the_orchestrator_to_accept_adds_no_notice(contract):
    assert _notice(contract) == ""


def test_evidence_entries_do_not_count_beside_a_real_proposal():
    assert _notice({"update_contracts": [EVIDENCE, UPDATE]}).startswith("1 project-context update proposed")


def _delta(count: int) -> dict:
    facts = [
        {"operation": "create", "create": {**FACT["create"], "slug": f"atom_alpha_fact_{i}"}}
        for i in range(count)
    ]
    return {"version": 1, "proposals": facts}


def _context(out: dict) -> str:
    return (out.get("hookSpecificOutput") or {}).get("additionalContext") or out.get("additional_context") or ""


def _background_prompts(db, *envelopes: dict) -> list[str]:
    conclusions.baseline(SESSION_ID)
    contract_id = _cc_dispatch(db, "bg")
    _cc_agent_result("async_launched")
    prompts = []
    for envelope in envelopes:
        _finalize(db, contract_id, envelope)
        prompts.append(_context(_cc_prompt()))
    prompts.append(_context(_cc_prompt()))
    return prompts


@pytest.mark.parametrize("count, phrase", [(1, "1 project fact proposed"), (3, "3 project facts proposed")])
def test_a_background_close_with_proposals_says_so_once_after_its_conclusion(db, count, phrase):
    first, second = _background_prompts(db, _envelope(conclusion=CONCLUSION, memory_delta=_delta(count)))

    assert first.count(CONCLUSION) == first.count(phrase) == first.count(VERB) == 1
    assert first.index(CONCLUSION) < first.index(phrase) < first.index(VERB)
    assert phrase not in second and VERB not in second


def test_a_background_close_with_an_empty_delta_adds_no_notice(db):
    first, second = _background_prompts(db, _envelope(conclusion=CONCLUSION, memory_delta=_delta(0)))

    assert first.count(CONCLUSION) == 1
    assert "proposed" not in first and VERB not in first and not second


@pytest.mark.parametrize("delta, verb_count", [(None, 0), (_delta(1), 1)])
def test_a_close_whose_updates_were_refused_at_write_reports_the_refusal_once(db, delta, verb_count):
    envelope = _envelope(conclusion=CONCLUSION, update_contracts_refused=["section 'ghost' is unknown"])
    if delta:
        envelope["memory_delta"] = delta
    first, second = _background_prompts(db, envelope)

    assert first.count("update_contracts NOT recorded") == 1
    assert first.count(VERB) == verb_count
    assert "NOT recorded" not in second


def test_a_re_finalized_contract_does_not_repeat_the_notice(db):
    envelope = _envelope(conclusion=CONCLUSION, memory_delta=_delta(1))
    first, again, last = _background_prompts(db, envelope, envelope)

    assert first.count("1 project fact proposed") == 1
    assert "proposed" not in again and not last


def test_a_foreground_close_keeps_its_notice_and_the_next_prompt_adds_none(db):
    conclusions.baseline(SESSION_ID)
    contract_id = _cc_dispatch(db, "fg")
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION, memory_delta=_delta(1)))
    result, prompt = _context(_cc_agent_result("completed")), _context(_cc_prompt())

    assert result.count("1 project fact proposed") == 1
    assert "proposed" not in prompt and VERB not in prompt


def test_a_result_line_read_after_the_background_delivery_does_not_repeat_the_notice(db):
    conclusions.baseline(SESSION_ID)
    contract_id = _cc_dispatch(db, "late")
    _cc_agent_result("async_launched")
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION, memory_delta=_delta(1)))
    prompt, result = _context(_cc_prompt()), _context(_cc_agent_result("completed"))

    assert prompt.count("1 project fact proposed") == 1
    assert "proposed" not in result and VERB not in result


def test_an_opencode_background_close_with_proposals_says_so_once(db, monkeypatch):
    import bridge
    from adapters.opencode import OpenCodeAdapter
    from gaia.store.writer import bind_harness_child_session, claim_dispatch_row, insert_dispatched_handoff

    monkeypatch.setenv("GAIA_HOST", "opencode")
    conclusions.baseline(SESSION_ID)
    contract_id = f"{AGENT_ID}.ocnotice"
    insert_dispatched_handoff(
        contract_id, AGENT_ID, WORKSPACE, session_id=SESSION_ID, db_path=db,
        agent_name="gaia-system", kind="investigation", dispatch_tool_use_id="call-notice",
    )
    claim_dispatch_row(dispatch_tool_use_id="call-notice", db_path=db)
    bind_harness_child_session(dispatch_tool_use_id="call-notice", harness_agent_id=CHILD_SESSION, db_path=db)
    adapter = OpenCodeAdapter()
    adapter.adapt_post_tool_use(adapter.parse_event(json.dumps({
        "event": "tool.execute.after", "sessionID": SESSION_ID, "callID": "call-notice", "tool": "task",
        "args": {"description": "d", "prompt": "p", "subagent_type": "gaia-system"},
        "result": {"output": "launched", "metadata": {"sessionId": CHILD_SESSION, "background": True},
                   "exit_code": 0, "is_error": False},
    })))
    _finalize(db, contract_id, _envelope(conclusion=CONCLUSION, memory_delta=_delta(3)))
    first, second = (_context(bridge.handle({"event": "chat.prompt", "sessionID": SESSION_ID})) for _ in range(2))

    assert first.count(CONCLUSION) == first.count("3 project facts proposed") == first.count(VERB) == 1
    assert "proposed" not in second
