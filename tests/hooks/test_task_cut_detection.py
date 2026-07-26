#!/usr/bin/env python3
"""Tests for orchestrator-side detection of a harness-cut subagent turn.

The cut leaves no trace on the subagent side (SubagentStop never fires), so the
only observable is the Task result the parent receives: a non-error status with
no ``agent_contract_handoff`` fence, plus metrics belonging to no episode.

Covers the signature itself (``detect_task_cut``), the harness_events row the
observer writes (``observe_task_result``), and the PostToolUse wiring that
reaches it.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.task_result_observer import (  # noqa: E402
    AGENT_CUT_EVENT,
    REASON_NO_FENCE,
    REASON_UNPARSEABLE_FENCE,
    detect_task_cut,
    observe_task_result,
)

VALID_CONTRACT = {
    "agent_status": {
        "agent_state": "COMPLETE",
        "agent_id": "a" + "0" * 16,
        "pending_steps": [],
        "next_action": "done",
    },
    "evidence_report": {
        "patterns_checked": [],
        "files_checked": [],
        "commands_run": [],
        "key_outputs": [],
        "verbatim_outputs": [],
        "cross_layer_impacts": [],
        "open_gaps": [],
        "verification": {"result": "pass"},
    },
}


def _fenced(contract: dict) -> str:
    return (
        "Done.\n\n```agent_contract_handoff\n"
        + json.dumps(contract)
        + "\n```\n"
    )


def _task_response(text: str, **overrides) -> dict:
    response = {
        "content": [{"type": "text", "text": text}],
        "totalDurationMs": 251_402,
        "totalTokens": 184_311,
        "totalToolUseCount": 64,
    }
    response.update(overrides)
    return response


TASK_INPUT = {"subagent_type": "gaia-system", "prompt": "do the thing"}


class TestCutSignature:
    def test_completed_task_without_fence_is_a_cut(self):
        """The measured signature: status=completed, stale text, no fence."""
        cut = detect_task_cut(
            TASK_INPUT,
            _task_response("Now I'll finalize the contract...", status="completed"),
            session_id="s-1",
        )

        assert cut is not None
        assert cut.agent == "gaia-system"
        assert cut.reason == REASON_NO_FENCE
        assert cut.status == "completed"
        assert cut.session_id == "s-1"

    def test_cut_carries_the_harness_metrics(self):
        """The metrics are the only quantitative record the cut leaves."""
        cut = detect_task_cut(TASK_INPUT, _task_response("stale text"))

        assert cut.metrics == {
            "totalDurationMs": 251_402,
            "totalTokens": 184_311,
            "totalToolUseCount": 64,
        }

    def test_absent_status_is_treated_as_completed(self):
        """The harness reports failure explicitly; silence means it closed."""
        cut = detect_task_cut(TASK_INPUT, _task_response("stale text"))

        assert cut is not None
        assert cut.status == "completed"

    def test_valid_fence_is_not_a_cut(self):
        assert (
            detect_task_cut(TASK_INPUT, _task_response(_fenced(VALID_CONTRACT)))
            is None
        )

    def test_unparseable_fence_is_a_cut_with_its_own_reason(self):
        broken = "```agent_contract_handoff\n{not json,}\n```"

        cut = detect_task_cut(TASK_INPUT, _task_response(broken))

        assert cut is not None
        assert cut.reason == REASON_UNPARSEABLE_FENCE

    @pytest.mark.parametrize(
        "overrides",
        [
            {"status": "error"},
            {"status": "cancelled"},
            {"wasInterrupted": True},
            {"is_error": True},
        ],
    )
    def test_visible_failures_are_not_the_silent_cut(self, overrides):
        """A failure the caller already sees is not what this detector records."""
        assert detect_task_cut(TASK_INPUT, _task_response("boom", **overrides)) is None

    @pytest.mark.parametrize(
        "response",
        [
            "a bare string result with no fence",
            {"result": "flat string shape, no fence"},
            {},
        ],
    )
    def test_alternate_response_shapes_still_detect(self, response):
        assert detect_task_cut(TASK_INPUT, response) is not None

    def test_missing_subagent_type_degrades_to_unknown(self):
        cut = detect_task_cut({}, _task_response("stale"))

        assert cut.agent == "unknown"


def _cut_rows(db_path):
    """Read the agent.cut rows written to harness_events."""
    from gaia.store import writer as store_writer

    con = store_writer._connect(db_path)
    try:
        rows = con.execute(
            "SELECT ts, type, source, agent, result, severity, payload "
            "FROM harness_events WHERE type = ? ORDER BY id",
            (AGENT_CUT_EVENT,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


class TestCutIsRecorded:
    @pytest.fixture
    def db_path(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "gaia_data"
        data_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
        return data_dir / "gaia.db"

    def test_observe_writes_a_harness_event(self, db_path):
        """The acceptance criterion: after the change, a cut leaves a queryable row."""
        cut = observe_task_result(
            {
                "tool_name": "Task",
                "session_id": "s-42",
                "tool_input": TASK_INPUT,
                "tool_response": _task_response("stale narration", status="completed"),
            }
        )

        assert cut is not None
        rows = _cut_rows(db_path)
        assert len(rows) == 1, f"expected exactly one {AGENT_CUT_EVENT} row"
        row = rows[0]
        assert row["agent"] == "gaia-system"
        assert row["severity"] == "warning"
        assert row["ts"]

        payload = json.loads(row["payload"])
        assert payload["reason"] == REASON_NO_FENCE
        assert payload["session_id"] == "s-42"
        assert payload["totalToolUseCount"] == 64
        assert payload["totalDurationMs"] == 251_402

    def test_a_clean_turn_writes_nothing(self, db_path):
        cut = observe_task_result(
            {
                "tool_name": "Task",
                "session_id": "s-43",
                "tool_input": TASK_INPUT,
                "tool_response": _task_response(_fenced(VALID_CONTRACT)),
            }
        )

        assert cut is None
        assert _cut_rows(db_path) == []


class TestPostToolUseWiring:
    def test_task_matcher_is_registered(self):
        """Without the matcher the observer is never reached (D4)."""
        hooks_json = json.loads((HOOKS_DIR / "hooks.json").read_text())
        matchers = [m.get("matcher") for m in hooks_json["hooks"]["PostToolUse"]]

        assert "Task" in matchers

    def test_adapter_routes_a_task_result_to_the_observer(self, monkeypatch):
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType

        seen = {}
        monkeypatch.setattr(
            "modules.agents.task_result_observer.observe_task_result",
            lambda hook_data: seen.setdefault("payload", hook_data),
        )

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Task",
            "session_id": "s-44",
            "tool_input": TASK_INPUT,
            "tool_response": _task_response("stale narration"),
        }
        response = ClaudeCodeAdapter().adapt_post_tool_use(
            HookEvent(
                event_type=HookEventType.POST_TOOL_USE,
                session_id="s-44",
                payload=payload,
            )
        )

        assert response.exit_code == 0
        assert seen["payload"]["tool_name"] == "Task"
