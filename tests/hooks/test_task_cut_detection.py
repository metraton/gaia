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
    SKIP_TURN_NOT_ENDED,
    SKIP_UNREADABLE_RESULT,
    SKIP_VISIBLE_FAILURE,
    detect_task_cut,
    inspect_task_result,
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
    """A REAL PostToolUse ``tool_response`` for the Agent tool.

    Transcribed from a live ``toolUseResult`` (the field the harness passes to
    the hook verbatim), keys and nesting unchanged; only the free text and the
    prompt are shortened. Every key below was present on all 167 completed
    Agent results measured in the capture.
    """
    response = {
        "status": "completed",
        "prompt": "do the thing",
        "agentId": "a21d060aefc6855cc",
        "agentType": "gaia-system",
        "content": [{"type": "text", "text": text}],
        "resolvedModel": "claude-opus-5[1m]",
        "totalDurationMs": 251_402,
        "totalTokens": 184_311,
        "totalToolUseCount": 64,
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 192,
            "cache_read_input_tokens": 114_591,
            "output_tokens": 9_407,
            "service_tier": "standard",
        },
        "toolStats": {
            "readCount": 2,
            "searchCount": 0,
            "bashCount": 27,
            "editFileCount": 8,
            "linesAdded": 501,
            "linesRemoved": 0,
            "otherToolCount": 0,
        },
    }
    response.update(overrides)
    return response


def _async_launch_response() -> dict:
    """A REAL ``run_in_background`` dispatch result, transcribed verbatim.

    It carries no ``content`` and no fence because the turn has not ended --
    the single largest false-positive source (156 of 325 measured results).
    """
    return {
        "isAsync": True,
        "status": "async_launched",
        "agentId": "a328e0d9b8f2aa70b",
        "description": "some background work",
        "resolvedModel": "claude-sonnet-5",
        "outputFile": "/home/jorge/.tmp/claude-1001/-home-jorge-ws-me/793cce12",
        "canReadOutputFile": True,
    }


# The real tool_input keys the Agent tool ships with.
TASK_INPUT = {
    "description": "do the thing",
    "prompt": "do the thing",
    "subagent_type": "gaia-system",
    "run_in_background": False,
}


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
        response = _task_response("stale text")
        del response["status"]

        cut = detect_task_cut(TASK_INPUT, response)

        assert cut is not None
        assert cut.status == "completed"

    def test_cut_carries_the_harness_agent_run_id(self):
        """agentId is the handle that locates the orphaned contract draft."""
        cut = detect_task_cut(TASK_INPUT, _task_response("stale text"))

        assert cut.agent_run_id == "a21d060aefc6855cc"

    def test_agent_type_names_the_agent_when_the_input_does_not(self):
        cut = detect_task_cut({}, _task_response("stale text"))

        assert cut.agent == "gaia-system"

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

    def test_empty_content_on_a_completed_turn_is_a_cut(self):
        """Measured twice: a turn of 68-80 tool calls that emitted no text.

        The harness offered its result channel and it was empty -- the harshest
        cut, not an unreadable payload.
        """
        cut = detect_task_cut(TASK_INPUT, _task_response("", content=[]))

        assert cut is not None
        assert cut.reason == REASON_NO_FENCE
        assert cut.result_preview == ""

    def test_flat_result_shape_still_detects(self):
        """A harness version that returns the text flat is still readable."""
        assert detect_task_cut(TASK_INPUT, {"result": "flat shape, no fence"}) is not None

    def test_missing_subagent_type_degrades_to_unknown(self):
        cut = detect_task_cut({}, {"result": "flat shape, no fence"})

        assert cut.agent == "unknown"


class TestNotACut:
    """Shapes that carry no fence for a reason OTHER than a cut.

    Each was measured in the live capture; reading any of them as a cut is
    what made the detector unusable in production before it ever ran.
    """

    def test_background_dispatch_is_not_a_cut(self):
        """156 of 325 real results -- a launch, not an ended turn."""
        verdict = inspect_task_result(TASK_INPUT, _async_launch_response())

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_TURN_NOT_ENDED

    def test_bare_string_result_is_a_visible_failure(self):
        """The harness swaps in bare error text: 'User rejected tool use'."""
        verdict = inspect_task_result(TASK_INPUT, "User rejected tool use")

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_VISIBLE_FAILURE

    def test_unreadable_shape_is_reported_not_assumed_to_be_a_cut(self):
        """An unknown shape means 'could not read', never 'no fence'."""
        verdict = inspect_task_result(TASK_INPUT, {"status": "completed"})

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_UNREADABLE_RESULT
        assert verdict.unprocessable

    def test_a_real_completed_turn_with_a_fence_is_clean(self):
        verdict = inspect_task_result(TASK_INPUT, _task_response(_fenced(VALID_CONTRACT)))

        assert verdict.cut is None
        assert not verdict.unprocessable


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
                "tool_name": "Agent",
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
                "tool_name": "Agent",
                "session_id": "s-43",
                "tool_input": TASK_INPUT,
                "tool_response": _task_response(_fenced(VALID_CONTRACT)),
            }
        )

        assert cut is None
        assert _cut_rows(db_path) == []

    def test_a_background_dispatch_writes_nothing(self, db_path):
        cut = observe_task_result(
            {
                "tool_name": "Agent",
                "session_id": "s-45",
                "tool_input": {**TASK_INPUT, "run_in_background": True},
                "tool_response": _async_launch_response(),
            }
        )

        assert cut is None
        assert _cut_rows(db_path) == []


class TestFailureIsNotSilent:
    """A payload the observer cannot process must leave evidence, not vanish."""

    @pytest.fixture
    def trace_lines(self, tmp_path, monkeypatch):
        """Pin BOTH roots: the trace resolves through CLAUDE_PLUGIN_DATA.

        ``GAIA_DATA_DIR`` alone is not enough -- ``get_logs_dir`` goes through
        ``get_plugin_data_dir``, which reads ``CLAUDE_PLUGIN_DATA`` and caches
        the result. Without pinning it (and clearing the cache both ways) these
        tests append to the developer's real ``hook-trace.jsonl`` and then
        assert against it.
        """
        from modules.core.paths import clear_path_cache

        data_dir = tmp_path / "gaia_data"
        data_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
        monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin_data"))
        monkeypatch.delenv("GAIA_HOOK_TRACE", raising=False)
        clear_path_cache()

        from modules.core.hook_trace import trace_path

        def read():
            path = trace_path()
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

        yield read
        clear_path_cache()

    def test_unprocessable_payload_is_traced(self, trace_lines):
        observe_task_result(
            {
                "tool_name": "Agent",
                "session_id": "s-46",
                "tool_input": TASK_INPUT,
                "tool_response": {"status": "completed", "someNewKey": {}},
            }
        )

        observed = [ln for ln in trace_lines() if ln.get("observer") == "unprocessable"]
        assert observed, "an unreadable payload must leave a trace line"
        assert observed[-1]["skip"] == SKIP_UNREADABLE_RESULT
        assert observed[-1]["tool"] == "Agent"

    def test_a_detected_cut_is_traced(self, trace_lines):
        observe_task_result(
            {
                "tool_name": "Agent",
                "session_id": "s-47",
                "tool_input": TASK_INPUT,
                "tool_response": _task_response("Now I'll finalize the contract..."),
            }
        )

        observed = [ln for ln in trace_lines() if ln.get("observer") == AGENT_CUT_EVENT]
        assert observed, "a detected cut must leave a trace line"
        assert observed[-1]["reason"] == REASON_NO_FENCE

    def test_a_failed_event_write_is_traced(self, trace_lines, monkeypatch):
        """Non-blocking must not mean invisible: the swallow leaves a record."""
        import modules.events.event_writer as event_writer

        def boom(*_args, **_kwargs):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(event_writer.EventWriter, "write_event", boom)

        cut = observe_task_result(
            {
                "tool_name": "Agent",
                "session_id": "s-48",
                "tool_input": TASK_INPUT,
                "tool_response": _task_response("stale narration"),
            }
        )

        assert cut is not None, "a write failure must not hide the detection"
        observed = [ln for ln in trace_lines() if ln.get("observer") == "write_failed"]
        assert observed, "a swallowed write failure must leave a trace line"
        assert "database is locked" in observed[-1]["detail"]


class TestPostToolUseWiring:
    def test_task_matcher_is_registered(self):
        """Without the matcher the observer is never reached (D4).

        The registered matcher is still the tool's FORMER name, ``Task``. That
        is deliberate and load-bearing: the harness honors it for the renamed
        ``Agent`` tool (27 post_tool_use invocations with ``tool=Agent`` were
        traced against exactly this matcher set), and registering ``Agent`` as
        a second matcher would risk firing the hook twice per dispatch.
        """
        hooks_json = json.loads((HOOKS_DIR / "hooks.json").read_text())
        matchers = [m.get("matcher") for m in hooks_json["hooks"]["PostToolUse"]]

        assert "Task" in matchers

    @pytest.mark.parametrize("tool_name", ["Agent", "Task"])
    def test_adapter_routes_a_dispatch_result_to_the_observer(self, tool_name, monkeypatch):
        """The regression: the payload says ``Agent``, the old gate said ``Task``.

        The hook ran on every dispatch and the branch was simply never entered,
        so no event was ever written. Both names must reach the observer.
        """
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType

        seen = {}
        monkeypatch.setattr(
            "modules.agents.task_result_observer.observe_task_result",
            lambda hook_data: seen.setdefault("payload", hook_data),
        )

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
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
        assert seen["payload"]["tool_name"] == tool_name

    def test_a_real_dispatch_payload_produces_the_event(self, tmp_path, monkeypatch):
        """End to end on the REAL payload: PostToolUse in, agent.cut row out.

        This is the production criterion. The prior suite asserted the same
        thing against an invented ``tool_name="Task"`` payload, which is why it
        passed while production wrote nothing.
        """
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType

        data_dir = tmp_path / "gaia_data"
        data_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": "s-49",
            "tool_input": TASK_INPUT,
            "tool_response": _task_response(
                "Now registering the gate results in the substrate."
            ),
        }
        response = ClaudeCodeAdapter().adapt_post_tool_use(
            HookEvent(
                event_type=HookEventType.POST_TOOL_USE,
                session_id="s-49",
                payload=payload,
            )
        )

        assert response.exit_code == 0, "observation must never block the orchestrator"
        rows = _cut_rows(data_dir / "gaia.db")
        assert len(rows) == 1, f"expected one {AGENT_CUT_EVENT} row from the real payload"
        assert rows[0]["agent"] == "gaia-system"
        assert json.loads(rows[0]["payload"])["agent_run_id"] == "a21d060aefc6855cc"
