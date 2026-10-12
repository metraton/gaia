"""The tool-call ceiling notice, driven through both hosts' post-tool adapters.

A dispatch carrying ``ceiling=<N>`` is born with that prompt; the subagent's
tool calls then reach each host's real ``adapt_post_tool_use``. The notice
must fire exactly once, at the recorded threshold, and never for a dispatch
without the token or for the orchestrator's own session. Neither host hands a
hook the turn's context use, so a token-less dispatch with a large transcript
gets no notice either: that row is the declared gap, asserted as behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]
_HOOKS_DIR = str(_REPO_ROOT / "hooks")
for _p in (_HOOKS_DIR, str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import ClaudeCodeAdapter  # noqa: E402
from adapters.opencode import OpenCodeAdapter  # noqa: E402
from gaia.store.writer import stamp_harness_agent_id  # noqa: E402
from modules.agents.dispatch_binding import (  # noqa: E402
    birth_dispatched_row,
    extract_dispatch_binding,
)
from tests.fixtures.agent_ids import valid_agent_id  # noqa: E402

CEILING_PROMPT = "project=gaia plan_id=87 task_id=946 ceiling=8\n\nBuild the notice."
FREE_PROMPT = "project=gaia\n\nInvestigate the hook layer."
CEILING = 8
THRESHOLD_CALL = 6
NOTICE_MARK = "Tool-call ceiling"


@pytest.fixture(autouse=True)
def _isolated_substrate(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GAIA_DB", str(tmp_path / "gaia.db"))
    monkeypatch.delenv("GAIA_DISPATCH_AGENT", raising=False)


def _born_turn(prompt: str, harness_agent_id: str, token: str) -> None:
    agent_id = valid_agent_id(token)
    contract_id = f"{agent_id}.{token}"
    birth_dispatched_row(
        contract_id=contract_id,
        agent_id=agent_id,
        workspace="me",
        kind="investigation",
        agent_name="gaia-system",
        dispatch_prompt=prompt,
    )
    assert stamp_harness_agent_id(contract_id, harness_agent_id)["status"] == "applied"


def _write_large_transcript(tmp_path: Path) -> str:
    usage = {"input_tokens": 12, "cache_read_input_tokens": 185_000,
             "cache_creation_input_tokens": 3_000, "output_tokens": 400}
    entry = {"type": "assistant", "message": {"role": "assistant", "usage": usage,
                                              "content": [{"type": "text", "text": "x"}]}}
    path = tmp_path / "transcript.jsonl"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return str(path)


def _claude_code_call(n: int, *, agent_id: str | None, transcript_path: str = "") -> str:
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "cc-parent-session",
        "transcript_path": transcript_path,
        "cwd": str(_REPO_ROOT),
        "tool_name": "Read",
        "tool_use_id": f"toolu_{n}",
        "tool_input": {"file_path": str(_REPO_ROOT / "README.md")},
        "tool_response": {"type": "text", "file": {"content": "x"}},
    }
    if agent_id:
        payload.update({"agent_id": agent_id, "agent_type": "gaia-system"})
    adapter = ClaudeCodeAdapter()
    response = adapter.adapt_post_tool_use(adapter.parse_event(json.dumps(payload)))
    specific = response.output.get("hookSpecificOutput") or {}
    return specific.get("additionalContext") or ""


def _opencode_call(n: int, *, session_id: str, dispatch_handle: str | None) -> str:
    payload = {
        "event": "tool.execute.after",
        "sessionID": session_id,
        "callID": f"call-{n}",
        "agent": "gaia-system" if dispatch_handle else "gaia-orchestrator",
        "tool": "read",
        "args": {"filePath": str(_REPO_ROOT / "README.md")},
        "result": {"output": "x", "exit_code": 0, "is_error": False},
    }
    if dispatch_handle:
        payload["agentID"] = dispatch_handle
    adapter = OpenCodeAdapter()
    response = adapter.adapt_post_tool_use(adapter.parse_event(json.dumps(payload)))
    return response.output.get("additional_context") or ""


def _claude_code_turn(calls: int, **kwargs) -> list[str]:
    return [_claude_code_call(n, **kwargs) for n in range(1, calls + 1)]


def _opencode_turn(calls: int, **kwargs) -> list[str]:
    return [_opencode_call(n, **kwargs) for n in range(1, calls + 1)]


def _assert_single_notice_at_threshold(contexts: list[str]) -> None:
    noticed = [i + 1 for i, text in enumerate(contexts) if NOTICE_MARK in text]
    assert noticed == [THRESHOLD_CALL]
    notice = contexts[THRESHOLD_CALL - 1]
    assert f"call {THRESHOLD_CALL} of {CEILING}" in notice
    assert f"{CEILING - THRESHOLD_CALL} remain" in notice
    assert "evidence" in notice and "finalize" in notice


def test_ceiling_token_is_parsed_beside_project_and_task_id():
    binding = extract_dispatch_binding({"prompt": CEILING_PROMPT, "subagent_type": "gaia-system"})
    assert binding["ceiling"] == CEILING
    assert binding["project"] == "gaia"
    assert binding["plan_task_id"] == 946
    assert binding["kind"] == "task_execution"


def test_ceiling_token_absent_is_none_and_keeps_free_kind():
    binding = extract_dispatch_binding({"prompt": FREE_PROMPT, "subagent_type": "gaia-system"})
    assert binding["ceiling"] is None
    assert binding["kind"] == "investigation"


def test_claude_code_notice_fires_once_at_threshold():
    _born_turn(CEILING_PROMPT, "cc-harness-1", "a1cc00001")
    contexts = _claude_code_turn(CEILING + 2, agent_id="cc-harness-1")
    assert not any(NOTICE_MARK in text for text in contexts[: THRESHOLD_CALL - 1])
    _assert_single_notice_at_threshold(contexts)


def test_opencode_notice_fires_once_at_threshold():
    _born_turn(CEILING_PROMPT, "ses-child-1", "a1oc00001")
    contexts = _opencode_turn(CEILING + 2, session_id="ses-child-1", dispatch_handle="call-task-1")
    assert not any(NOTICE_MARK in text for text in contexts[: THRESHOLD_CALL - 1])
    _assert_single_notice_at_threshold(contexts)


def test_no_token_large_transcript_gets_no_notice_on_claude_code(tmp_path):
    """Declared gap: no Claude Code hook field carries context use, so a large
    transcript does not stand in for the missing ceiling."""
    _born_turn(FREE_PROMPT, "cc-harness-2", "a1cc00002")
    transcript = _write_large_transcript(tmp_path)
    contexts = _claude_code_turn(12, agent_id="cc-harness-2", transcript_path=transcript)
    assert not any(NOTICE_MARK in text for text in contexts)


def test_no_token_gets_no_notice_on_opencode():
    """Declared gap: OpenCode's tool.execute.after carries no context use."""
    _born_turn(FREE_PROMPT, "ses-child-2", "a1oc00002")
    contexts = _opencode_turn(12, session_id="ses-child-2", dispatch_handle="call-task-2")
    assert not any(NOTICE_MARK in text for text in contexts)


def test_claude_code_orchestrator_session_is_never_counted():
    _born_turn(CEILING_PROMPT, "cc-parent-session", "a1cc00003")
    contexts = _claude_code_turn(CEILING + 2, agent_id=None)
    assert not any(NOTICE_MARK in text for text in contexts)


def test_opencode_orchestrator_session_is_never_counted():
    _born_turn(CEILING_PROMPT, "ses-root", "a1oc00003")
    contexts = _opencode_turn(CEILING + 2, session_id="ses-root", dispatch_handle=None)
    assert not any(NOTICE_MARK in text for text in contexts)
