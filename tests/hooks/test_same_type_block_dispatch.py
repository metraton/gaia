"""Same-type specialists dispatched in one message are each born with their own contract."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

WORKSPACE = "me"
SESSION = "sess-block"
PROMPT_ID = "f171b3c2-739a-48c2-ac3b-b56c4851bfda"
AGENT = "gaia-operator"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.setenv("GAIA_WORKSPACE", WORKSPACE)
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)
    from adapters.claude_code import ClaudeCodeAdapter

    monkeypatch.setattr(ClaudeCodeAdapter, "CONTEXT_CACHE_DIR", tmp_path / "ctx")


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "gaia-data" / "gaia.db"


@pytest.fixture()
def transcript(tmp_path):
    return tmp_path / "projects" / f"{SESSION}.jsonl"


@pytest.fixture()
def adapter():
    from adapters.claude_code import ClaudeCodeAdapter

    return ClaudeCodeAdapter()


def _birth(adapter, tool_use_id, description, prompt):
    born = {}
    original = adapter._maybe_birth_dispatched_row

    def _capture(parameters, agent_name, sid, **kwargs):
        result = original(parameters, agent_name, sid, **kwargs)
        born.update(result or {})
        return result

    adapter._maybe_birth_dispatched_row = _capture
    try:
        adapter._adapt_task(
            "Agent",
            {"subagent_type": AGENT, "description": description, "prompt": prompt},
            SESSION,
            hook_data={
                "session_id": SESSION, "prompt_id": PROMPT_ID,
                "tool_use_id": tool_use_id, "hook_event_name": "PreToolUse",
                "tool_name": "Agent",
            },
        )
    finally:
        adapter._maybe_birth_dispatched_row = original
    assert born, "the Agent call must birth a row"
    return born


def _start(adapter, transcript, harness_id):
    return adapter.adapt_subagent_start({
        "session_id": SESSION, "prompt_id": PROMPT_ID,
        "transcript_path": str(transcript), "cwd": str(transcript.parent),
        "hook_event_name": "SubagentStart",
        "agent_id": harness_id, "agent_type": AGENT,
    }).additional_context or ""


def _host_meta(transcript, harness_id, tool_use_id, description):
    folder = transcript.parent / SESSION / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"agent-{harness_id}.meta.json").write_text(json.dumps({
        "agentType": AGENT, "description": description, "toolUseId": tool_use_id,
    }))


def _first_tool_call(adapter, transcript, harness_id):
    event = adapter.parse_event(json.dumps({
        "session_id": SESSION, "transcript_path": str(transcript),
        "cwd": str(transcript.parent), "hook_event_name": "PreToolUse",
        "agent_id": harness_id, "agent_type": AGENT,
        "tool_name": "Read", "tool_input": {"file_path": str(transcript)},
        "tool_use_id": f"toolu_read_{harness_id}",
    }))
    output = adapter.adapt_pre_tool_use(event).output
    return output.get("hookSpecificOutput", {}).get("additionalContext") or ""


def _row(db_path, contract_id):
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        return dict(con.execute(
            "SELECT claimed_at, harness_agent_id FROM agent_contract_handoffs "
            "WHERE contract_id = ?", (contract_id,),
        ).fetchone())
    finally:
        con.close()


def _two_births(adapter):
    first = _birth(adapter, "toolu_A", "Costos", "project=none\nObjetivo: costos")
    second = _birth(adapter, "toolu_B", "Segunda opinion", "project=none\nObjetivo: opinion")
    return first, second


def test_same_block_starts_get_the_unresolved_block_and_claim_nothing(
    adapter, db_path, transcript,
):
    from modules.context.kernel_builder import KERNEL_HEADING, UNRESOLVED_HEADING

    first, second = _two_births(adapter)
    for harness_id in ("aaaa000000000000a", "bbbb000000000000b"):
        context = _start(adapter, transcript, harness_id)
        assert UNRESOLVED_HEADING in context
        assert f"{KERNEL_HEADING}\n" not in context
        assert f"{first['contract_id']} (Costos)  goal: project=none" in context
        assert f"{second['contract_id']} (Segunda opinion)  goal: project=none" in context
        assert "first tool call" in context
        assert "Never run a bare `gaia contract init`" in context
    for born in (first, second):
        assert _row(db_path, born["contract_id"]) == {"claimed_at": None, "harness_agent_id": None}


def test_each_first_tool_call_claims_its_own_row_and_delivers_the_kernel(
    adapter, db_path, transcript,
):
    from modules.context.kernel_builder import KERNEL_HEADING

    first, second = _two_births(adapter)
    _start(adapter, transcript, "aaaa000000000000a")
    _start(adapter, transcript, "bbbb000000000000b")
    _host_meta(transcript, "aaaa000000000000a", "toolu_A", "Costos")
    _host_meta(transcript, "bbbb000000000000b", "toolu_B", "Segunda opinion")

    context_b = _first_tool_call(adapter, transcript, "bbbb000000000000b")
    context_a = _first_tool_call(adapter, transcript, "aaaa000000000000a")

    assert context_a.startswith(KERNEL_HEADING)
    assert first["contract_id"] in context_a and second["contract_id"] not in context_a
    assert context_b.startswith(KERNEL_HEADING)
    assert second["contract_id"] in context_b and first["contract_id"] not in context_b
    assert _row(db_path, first["contract_id"])["harness_agent_id"] == "aaaa000000000000a"
    assert _row(db_path, second["contract_id"])["harness_agent_id"] == "bbbb000000000000b"
    assert KERNEL_HEADING not in _first_tool_call(adapter, transcript, "aaaa000000000000a")


def test_a_start_for_an_already_bound_harness_id_claims_no_sibling(
    adapter, db_path, transcript,
):
    resumed = _birth(adapter, "toolu_R", "Redactar cartas", "project=none\nObjetivo: cartas")
    assert resumed["contract_id"] in _start(adapter, transcript, "cccc000000000000c")
    sibling = _birth(adapter, "toolu_B", "Segunda opinion", "project=none\nObjetivo: opinion")

    context = _start(adapter, transcript, "cccc000000000000c")

    assert sibling["contract_id"] not in context
    assert _row(db_path, sibling["contract_id"]) == {"claimed_at": None, "harness_agent_id": None}
