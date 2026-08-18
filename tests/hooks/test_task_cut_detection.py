#!/usr/bin/env python3
"""Tests for orchestrator-side detection of a harness-cut subagent turn.

The cut leaves no trace on the subagent side (SubagentStop never fires), so the
only observable is the Task result the parent receives: a non-error status,
metrics belonging to no episode, and -- the signature -- a contract row that
either does not exist or never left DISPATCHED, addressed by the harness run id
the result carries.

BOTH DIRECTIONS ARE THE PROPERTY UNDER TEST, and the fence is no longer part of
either: a turn that closes clean WITHOUT emitting one must not be recorded as a
cut, and a genuinely cut turn must still be.

Covers the signature itself (``detect_task_cut``), the tolerance for a finalize
still in flight, the harness_events row the observer writes
(``observe_task_result``), and the PostToolUse wiring that reaches it.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.agents.task_result_observer import (  # noqa: E402
    AGENT_CUT_EVENT,
    REASON_NO_CONTRACT_ROW,
    REASON_ROW_NEVER_FINALIZED,
    ROW_FINALIZED,
    ROW_MISSING,
    ROW_UNFINALIZED,
    ROW_UNRESOLVABLE,
    SKIP_CONTRACT_FINALIZED,
    SKIP_CONTRACT_ROW_UNRESOLVABLE,
    SKIP_NO_AGENT_RUN_ID,
    SKIP_TURN_NOT_ENDED,
    SKIP_VISIBLE_FAILURE,
    build_contract_summary_line,
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


def _answers(*states):
    """A row lookup returning ``states`` in order, then repeating the last.

    Several states in a row is how the grace window is exercised: a finalize
    that lands between two reads is the race the tolerance exists for.
    """
    seen = list(states)

    def lookup(agent_run_id, session_id):
        return seen.pop(0) if len(seen) > 1 else seen[0]

    return lookup


def _forbidden(agent_run_id, session_id):
    """A row lookup that must never be reached."""
    raise AssertionError("the row was queried for a payload decided before it")


@pytest.fixture(autouse=True)
def _no_grace_sleep(monkeypatch):
    """What the grace window DOES is under test; how long it waits is not."""
    monkeypatch.setattr(
        "modules.agents.task_result_observer._FINALIZE_GRACE_SECONDS", 0,
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


def _async_launch_response(**overrides) -> dict:
    """A REAL ``run_in_background`` dispatch result, transcribed verbatim.

    It carries no ``content`` and no fence because the turn has not ended --
    the single largest false-positive source (156 of 325 measured results).
    """
    response = {
        "isAsync": True,
        "status": "async_launched",
        "agentId": "a328e0d9b8f2aa70b",
        "description": "some background work",
        "resolvedModel": "claude-sonnet-5",
        "outputFile": "/home/jorge/.tmp/claude-1001/-home-jorge-ws-me/793cce12",
        "canReadOutputFile": True,
    }
    response.update(overrides)
    return response


# The real tool_input keys the Agent tool ships with.
TASK_INPUT = {
    "description": "do the thing",
    "prompt": "do the thing",
    "subagent_type": "gaia-system",
    "run_in_background": False,
}


class TestCutSignature:
    def test_a_turn_that_left_no_contract_row_is_a_cut(self):
        """The signature: status=completed, and no row carries its run id."""
        cut = detect_task_cut(
            TASK_INPUT,
            _task_response("Now I'll finalize the contract...", status="completed"),
            session_id="s-1",
            row_state=_answers(ROW_MISSING),
        )

        assert cut is not None
        assert cut.agent == "gaia-system"
        assert cut.reason == REASON_NO_CONTRACT_ROW
        assert cut.status == "completed"
        assert cut.session_id == "s-1"

    def test_a_row_that_never_left_dispatched_is_a_cut(self):
        """The other half: the row was born, and the agent never closed it."""
        cut = detect_task_cut(
            TASK_INPUT,
            _task_response("Now I'll finalize the contract..."),
            row_state=_answers(ROW_UNFINALIZED),
        )

        assert cut is not None
        assert cut.reason == REASON_ROW_NEVER_FINALIZED

    def test_a_finalized_row_is_clean_even_with_no_fence_in_the_text(self):
        """THE acceptance property, direction one.

        The result text carries no ``agent_contract_handoff`` block -- the
        shape every turn will have once the fence is retired. Under the old
        signature that absence WAS the cut; under the row signature it is
        nothing at all, because the turn's row is finalized.
        """
        verdict = inspect_task_result(
            TASK_INPUT,
            _task_response("Committed a7f3c21 and closed the contract."),
            row_state=_answers(ROW_FINALIZED),
        )

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_CONTRACT_FINALIZED
        assert not verdict.unprocessable

    def test_cut_carries_the_harness_metrics(self):
        """The metrics are the only quantitative record the cut leaves."""
        cut = detect_task_cut(
            TASK_INPUT, _task_response("stale text"), row_state=_answers(ROW_MISSING),
        )

        assert cut.metrics == {
            "totalDurationMs": 251_402,
            "totalTokens": 184_311,
            "totalToolUseCount": 64,
        }

    def test_absent_status_is_treated_as_completed(self):
        """The harness reports failure explicitly; silence means it closed."""
        response = _task_response("stale text")
        del response["status"]

        cut = detect_task_cut(
            TASK_INPUT, response, row_state=_answers(ROW_MISSING),
        )

        assert cut is not None
        assert cut.status == "completed"

    def test_cut_carries_the_harness_agent_run_id(self):
        """agentId is the coordinate the row is addressed by, and is recorded."""
        cut = detect_task_cut(
            TASK_INPUT, _task_response("stale text"), row_state=_answers(ROW_MISSING),
        )

        assert cut.agent_run_id == "a21d060aefc6855cc"

    def test_agent_type_names_the_agent_when_the_input_does_not(self):
        cut = detect_task_cut(
            {}, _task_response("stale text"), row_state=_answers(ROW_MISSING),
        )

        assert cut.agent == "gaia-system"

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
        """A failure the caller already sees is not what this detector records.

        ``_forbidden`` also pins the ordering: a status the harness already
        reported as failed is decided without touching the database.
        """
        assert detect_task_cut(
            TASK_INPUT, _task_response("boom", **overrides), row_state=_forbidden,
        ) is None

    def test_empty_content_on_a_completed_turn_is_a_cut(self):
        """Measured twice: a turn of 68-80 tool calls that emitted no text."""
        cut = detect_task_cut(
            TASK_INPUT,
            _task_response("", content=[]),
            row_state=_answers(ROW_MISSING),
        )

        assert cut is not None
        assert cut.reason == REASON_NO_CONTRACT_ROW
        assert cut.result_preview == ""

    def test_flat_result_shape_still_detects(self):
        """A harness version that returns the text flat is still readable."""
        assert detect_task_cut(
            TASK_INPUT,
            {"result": "flat shape", "agentId": "a21d060aefc6855cc"},
            row_state=_answers(ROW_MISSING),
        ) is not None

    def test_missing_subagent_type_degrades_to_unknown(self):
        cut = detect_task_cut(
            {},
            {"result": "flat shape", "agentId": "a21d060aefc6855cc"},
            row_state=_answers(ROW_MISSING),
        )

        assert cut.agent == "unknown"


class TestFinalizeRaceTolerance:
    """The measured risk: the finalize may not have landed when we look.

    47 of the 52 measured cases finalize from inside the agent's own turn and
    cannot race, but the 5 that reach terminal through the SubagentStop
    machinery land in the SAME SECOND as this observation (p50 = 0s over 35
    unambiguous pairs), so the order is not something to assume.
    """

    def test_a_finalize_landing_after_the_first_read_is_not_a_cut(self):
        verdict = inspect_task_result(
            TASK_INPUT,
            _task_response("closed clean, no fence"),
            row_state=_answers(ROW_UNFINALIZED, ROW_FINALIZED),
        )

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_CONTRACT_FINALIZED

    def test_a_row_born_after_the_first_read_is_not_a_cut(self):
        verdict = inspect_task_result(
            TASK_INPUT,
            _task_response("closed clean, no fence"),
            row_state=_answers(ROW_MISSING, ROW_MISSING, ROW_FINALIZED),
        )

        assert verdict.cut is None

    def test_a_row_still_unfinalized_when_the_window_closes_is_a_cut(self):
        """Tolerance is bounded: waiting must not swallow the real signal."""
        cut = detect_task_cut(
            TASK_INPUT,
            _task_response("stale narration"),
            row_state=_answers(ROW_UNFINALIZED),
        )

        assert cut is not None
        assert cut.reason == REASON_ROW_NEVER_FINALIZED


class TestNotACut:
    """Shapes that carry no fence for a reason OTHER than a cut.

    Each was measured in the live capture; reading any of them as a cut is
    what made the detector unusable in production before it ever ran.
    """

    def test_background_dispatch_is_not_a_cut(self):
        """156 of 325 real results -- a launch, not an ended turn."""
        verdict = inspect_task_result(
            TASK_INPUT, _async_launch_response(), row_state=_forbidden,
        )

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_TURN_NOT_ENDED

    def test_bare_string_result_is_a_visible_failure(self):
        """The harness swaps in bare error text: 'User rejected tool use'."""
        verdict = inspect_task_result(
            TASK_INPUT, "User rejected tool use", row_state=_forbidden,
        )

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_VISIBLE_FAILURE

    def test_a_result_without_a_run_id_is_reported_not_assumed_to_be_a_cut(self):
        """No run id means the row is unfindable, never that it is absent."""
        verdict = inspect_task_result(
            TASK_INPUT, {"status": "completed"}, row_state=_forbidden,
        )

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_NO_AGENT_RUN_ID
        assert verdict.unprocessable

    def test_an_unresolvable_row_is_reported_not_assumed_to_be_a_cut(self):
        """Store unavailable, or several rival rows: 'cannot tell', not a cut."""
        verdict = inspect_task_result(
            TASK_INPUT,
            _task_response("closed clean, no fence"),
            row_state=_answers(ROW_UNRESOLVABLE),
        )

        assert verdict.cut is None
        assert verdict.skip_reason == SKIP_CONTRACT_ROW_UNRESOLVABLE
        assert verdict.unprocessable


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


def _seed_finalized_row(db_path, harness_agent_id: str) -> None:
    """Persist the row a turn that reached its own end leaves behind.

    Born DISPATCHED, stamped with the harness run id at the SubagentStart
    seam, then converged by the agent's own finalize -- the real three-step
    lifecycle, because a row inserted straight into a terminal state could not
    be stamped (``stamp_harness_agent_id`` refuses a terminal row) and would
    prove nothing about the coordinate the observer joins on.
    """
    from gaia.store.writer import (
        finalize_agent_contract_handoff,
        insert_dispatched_handoff,
        stamp_harness_agent_id,
    )

    contract_id = "a0000000000000001.clean-close"
    agent_id = "a" + "0" * 16
    insert_dispatched_handoff(
        contract_id, agent_id, "me", session_id="s-43", db_path=db_path,
    )
    stamp_harness_agent_id(contract_id, harness_agent_id, db_path=db_path)
    finalize_agent_contract_handoff(
        contract_id=contract_id,
        agent_id=agent_id,
        workspace="me",
        agent_state="COMPLETE",
        raw_handoff_json=json.dumps(VALID_CONTRACT),
        db_path=db_path,
    )


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
        assert payload["reason"] == REASON_NO_CONTRACT_ROW
        assert payload["session_id"] == "s-42"
        assert payload["totalToolUseCount"] == 64
        assert payload["totalDurationMs"] == 251_402

    def test_a_clean_turn_with_no_fence_writes_nothing(self, db_path):
        """THE acceptance property end to end, against a real persisted row."""
        _seed_finalized_row(db_path, "a21d060aefc6855cc")

        cut = observe_task_result(
            {
                "tool_name": "Agent",
                "session_id": "s-43",
                "tool_input": TASK_INPUT,
                "tool_response": _task_response(
                    "Committed a7f3c21 and closed the contract."
                ),
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
        assert observed[-1]["skip"] == SKIP_NO_AGENT_RUN_ID
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
        assert observed[-1]["reason"] == REASON_NO_CONTRACT_ROW

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


# ---------------------------------------------------------------------------
# P1: the one-line contract-row summary handed back on the Agent/Task result.
#
# BOTH DIRECTIONS ARE THE PROPERTY UNDER TEST, same as the cut signature
# above: a resolvable, finalized row must produce a real line naming its
# populated evidence and an armed read command; a row that is missing, still
# DISPATCHED, or unreadable must produce silence -- never a line that claims
# "nothing to read" when the truth is "could not be read".
# ---------------------------------------------------------------------------


def _row(agent_state: str, envelope: dict) -> dict:
    """A minimal ``agent_contract_handoffs`` row shape, as the bridge returns it."""
    return {"agent_state": agent_state, "raw_handoff_json": json.dumps(envelope)}


def _lookup_returning(row):
    def lookup(task_info, session_id):
        return row

    return lookup


def _lookup_raising(task_info, session_id):
    raise RuntimeError("store unavailable")


TWO_FIELD_ENVELOPE = {
    "agent_status": {"agent_state": "COMPLETE"},
    "evidence_report": {
        "patterns_checked": [],
        "files_checked": [],
        "commands_run": [],
        "key_outputs": [],
        "verbatim_outputs": [],
        "cross_layer_impacts": ["skill X drifted"],
        "open_gaps": ["g1", "g2"],
        "verification": {"result": "pass"},
    },
}

FOUR_FIELD_ENVELOPE = {
    "agent_status": {"agent_state": "BLOCKED"},
    "evidence_report": {
        "patterns_checked": ["p1"],
        "files_checked": ["f1"],
        "commands_run": [],
        "key_outputs": [],
        "verbatim_outputs": [],
        "cross_layer_impacts": ["c1"],
        "open_gaps": ["g1"],
    },
}

# Same four populated fields, but with a longer state name and a verification
# block -- long enough that naming all of it alongside the armed command
# would cross the hard ceiling. This is the case the ceiling exists for.
FOUR_FIELD_ENVELOPE_OVER_CEILING = {
    "agent_status": {"agent_state": "NEEDS_VERIFICATION"},
    "evidence_report": {
        "patterns_checked": ["p1"],
        "files_checked": ["f1", "f2"],
        "commands_run": [],
        "key_outputs": [],
        "verbatim_outputs": [],
        "cross_layer_impacts": ["c1"],
        "open_gaps": ["g1", "g2"],
        "verification": {"result": "pass"},
    },
    "report_prose": "why this turn did what it did",
}

EMPTY_EVIDENCE_ENVELOPE = {
    "agent_status": {"agent_state": "BLOCKED"},
    "evidence_report": {
        "patterns_checked": [], "files_checked": [], "commands_run": [],
        "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
        "open_gaps": [],
    },
}


class TestContractSummaryLineResolvable:
    """A resolvable, finalized row produces a real line with real coordinates."""

    def test_typical_row_produces_the_armed_command_with_the_real_id(self):
        line = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(_row("COMPLETE", TWO_FIELD_ENVELOPE)),
        )

        assert line is not None
        assert line.startswith("a21d060aefc6855cc: state=COMPLETE, verification=pass")
        assert "cross_layer_impacts(1)" in line
        assert "open_gaps(2)" in line
        assert line.endswith(
            "gaia contract view --harness-id a21d060aefc6855cc "
            "--field evidence_report.open_gaps"
        )
        # MEASURED length of a typical two-populated-field case (the shape
        # the design reference itself illustrates) -- the budget is a
        # ceiling paid on every subagent return, not a suggestion.
        assert len(line) <= 180, f"typical line is {len(line)} chars: {line!r}"

    def test_open_gaps_is_prioritized_over_other_populated_fields(self):
        line = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(_row("BLOCKED", FOUR_FIELD_ENVELOPE)),
        )

        assert line.endswith("--field evidence_report.open_gaps")

    def test_more_than_max_named_fields_folds_into_a_more_tag(self):
        line = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(_row("BLOCKED", FOUR_FIELD_ENVELOPE)),
        )

        # 4 populated fields, cap is 3 named + a "+1more" tag for the rest.
        assert "+1more" in line
        assert line.count("(") == 3, "only the first 3 populated fields are counted individually"

    def test_ceiling_drops_the_field_list_but_never_the_command(self):
        """When naming every populated field would blow the hard ceiling, the
        descriptive segment is dropped and the armed command survives intact
        -- a truncated command is not copy-pasteable, so length pressure must
        never be absorbed by it.
        """
        line = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(
                _row("NEEDS_VERIFICATION", FOUR_FIELD_ENVELOPE_OVER_CEILING)
            ),
        )

        assert "patterns_checked" not in line, "field list must be dropped, not truncated mid-word"
        assert line == (
            "a21d060aefc6855cc: state=NEEDS_VERIFICATION, verification=pass -- "
            "gaia contract view --harness-id a21d060aefc6855cc "
            "--field evidence_report.open_gaps"
        )

    def test_no_evidence_and_no_report_prose_omits_the_field_flag(self):
        line = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(_row("BLOCKED", EMPTY_EVIDENCE_ENVELOPE)),
        )

        assert line is not None
        assert line == (
            "a21d060aefc6855cc: state=BLOCKED -- "
            "gaia contract view --harness-id a21d060aefc6855cc"
        )

    def test_no_verification_block_omits_the_verification_term(self):
        envelope = {
            "agent_status": {"agent_state": "NEEDS_INPUT"},
            "evidence_report": {
                "patterns_checked": [], "files_checked": [], "commands_run": [],
                "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
                "open_gaps": ["what to decide"],
            },
        }
        line = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(_row("NEEDS_INPUT", envelope)),
        )

        assert "verification=" not in line
        assert line.startswith("a21d060aefc6855cc: state=NEEDS_INPUT, open_gaps(1)")


class TestContractSummaryLineLaunchPointer:
    """MODE B: no finalized row yet, but the result still names an agentId.

    This is the async-launch case the P1 rescue targets: ``run_in_background``
    returns a stub (status ``async_launched``) at LAUNCH, before any row can
    be finalized -- measured as 156 of 325 real Agent results, the majority.
    The pointer must read differently from MODE A (no ``state=``, no field
    counts) so whoever reads it can tell a launch pointer from a close.
    """

    def test_async_launch_stub_with_no_row_yet_produces_the_pointer(self):
        """The row for a just-launched dispatch may not exist at all yet."""
        line = build_contract_summary_line(
            _async_launch_response(), row_lookup=_lookup_returning(None),
        )

        assert line == (
            "a328e0d9b8f2aa70b: launched, not finalized yet -- "
            "gaia contract view --harness-id a328e0d9b8f2aa70b"
        )

    def test_row_still_dispatched_produces_the_pointer(self):
        """The row was born (PreToolUse) but the subagent has not closed it."""
        line = build_contract_summary_line(
            _async_launch_response(),
            row_lookup=_lookup_returning(_row("DISPATCHED", TWO_FIELD_ENVELOPE)),
        )

        assert line == (
            "a328e0d9b8f2aa70b: launched, not finalized yet -- "
            "gaia contract view --harness-id a328e0d9b8f2aa70b"
        )

    def test_pointer_carries_the_real_agent_id_not_a_placeholder(self):
        line = build_contract_summary_line(
            _async_launch_response(agentId="a" + "9" * 17),
            row_lookup=_lookup_returning(None),
        )

        assert line.startswith("a" + "9" * 17)
        assert "--harness-id " + "a" + "9" * 17 in line

    def test_pointer_never_claims_a_state_it_does_not_have(self):
        """The distinguishing mark: MODE A always carries ``state=``, MODE B
        never does -- that absence is what tells the two modes apart.
        """
        line = build_contract_summary_line(
            _async_launch_response(), row_lookup=_lookup_returning(None),
        )

        assert "state=" not in line

    def test_pointer_is_shorter_than_a_typical_mode_a_line(self):
        """The budget this turn was asked to report: MODE B pays no count
        segment, so it must come in under MODE A's own measured typical
        length (asserted at 180 in TestContractSummaryLineResolvable).
        """
        pointer = build_contract_summary_line(
            _async_launch_response(), row_lookup=_lookup_returning(None),
        )
        closed = build_contract_summary_line(
            _task_response("closed clean"),
            row_lookup=_lookup_returning(_row("COMPLETE", TWO_FIELD_ENVELOPE)),
        )

        assert len(pointer) < len(closed), (
            f"MODE B ({len(pointer)} chars) must be shorter than "
            f"MODE A ({len(closed)} chars)"
        )


class TestContractSummaryLineDegradesToSilence:
    """The other direction: an irresolvable row is SILENCE, never a lie.

    A conditional that fires on every input is not a conditional -- each case
    here is a distinct reason the line must NOT be produced, and none of them
    may raise past this function.
    """

    def test_no_agent_run_id_is_silence(self):
        assert build_contract_summary_line(
            {"status": "completed"}, row_lookup=_forbidden,
        ) is None

    def test_unparseable_envelope_is_silence(self):
        row = {"agent_state": "COMPLETE", "raw_handoff_json": "{not json"}
        assert build_contract_summary_line(
            _task_response("x"), row_lookup=_lookup_returning(row),
        ) is None

    def test_envelope_that_is_not_a_json_object_is_silence(self):
        row = {"agent_state": "COMPLETE", "raw_handoff_json": json.dumps([1, 2])}
        assert build_contract_summary_line(
            _task_response("x"), row_lookup=_lookup_returning(row),
        ) is None

    def test_lookup_failure_is_silence_not_a_raise(self):
        assert build_contract_summary_line(
            _task_response("x"), row_lookup=_lookup_raising,
        ) is None


class TestContractSummaryLineWiring:
    """End to end: the PostToolUse response the orchestrator actually reads."""

    @pytest.fixture
    def db_path(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "gaia_data"
        data_dir.mkdir()
        monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
        return data_dir / "gaia.db"

    def test_a_finalized_row_yields_additional_context(self, db_path):
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType

        _seed_finalized_row(db_path, "a21d060aefc6855cc")

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": "s-43",
            "tool_input": TASK_INPUT,
            "tool_response": _task_response("closed clean"),
        }
        response = ClaudeCodeAdapter().adapt_post_tool_use(
            HookEvent(
                event_type=HookEventType.POST_TOOL_USE,
                session_id="s-43",
                payload=payload,
            )
        )

        assert response.exit_code == 0
        hook_specific = response.output["hookSpecificOutput"]
        assert hook_specific["hookEventName"] == "PostToolUse"
        assert "a21d060aefc6855cc" in hook_specific["additionalContext"]
        assert "--harness-id a21d060aefc6855cc" in hook_specific["additionalContext"]

    def test_a_cut_row_never_finalized_yields_the_mode_b_pointer(self, db_path):
        """The row exists (born DISPATCHED) but never closed -- MODE B, not
        silence: the agentId is real, so the launch pointer is armed with it.
        """
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType
        from gaia.store.writer import insert_dispatched_handoff, stamp_harness_agent_id

        contract_id = "a0000000000000002.never-closed"
        agent_id = "a" + "0" * 15 + "2"
        insert_dispatched_handoff(
            contract_id, agent_id, "me", session_id="s-50", db_path=db_path,
        )
        stamp_harness_agent_id(contract_id, "a328e0d9b8f2aa70c", db_path=db_path)

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": "s-50",
            "tool_input": TASK_INPUT,
            "tool_response": _task_response(
                "stale narration", agentId="a328e0d9b8f2aa70c",
            ),
        }
        response = ClaudeCodeAdapter().adapt_post_tool_use(
            HookEvent(
                event_type=HookEventType.POST_TOOL_USE,
                session_id="s-50",
                payload=payload,
            )
        )

        assert response.exit_code == 0
        context = response.output["hookSpecificOutput"]["additionalContext"]
        assert context == (
            "a328e0d9b8f2aa70c: launched, not finalized yet -- "
            "gaia contract view --harness-id a328e0d9b8f2aa70c"
        )

    def test_no_matching_row_at_all_yields_the_mode_b_pointer(self, db_path):
        """No row was ever born under this id -- still MODE B, since the
        stub carries a real agentId and nothing has finalized.
        """
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": "s-51",
            "tool_input": TASK_INPUT,
            "tool_response": _task_response("closed clean", agentId="a" + "f" * 17),
        }
        response = ClaudeCodeAdapter().adapt_post_tool_use(
            HookEvent(
                event_type=HookEventType.POST_TOOL_USE,
                session_id="s-51",
                payload=payload,
            )
        )

        assert response.exit_code == 0
        context = response.output["hookSpecificOutput"]["additionalContext"]
        assert context == (
            f"a{'f' * 17}: launched, not finalized yet -- "
            f"gaia contract view --harness-id a{'f' * 17}"
        )

    def test_async_launched_stub_at_the_wiring_level_yields_the_mode_b_pointer(
        self, db_path,
    ):
        """The real shape this rescue targets: a PostToolUse Agent payload
        whose tool_response IS the launch stub (status ``async_launched``),
        not a completed result -- end to end through the adapter.
        """
        from adapters.claude_code import ClaudeCodeAdapter
        from adapters.types import HookEvent, HookEventType

        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "session_id": "s-52",
            "tool_input": TASK_INPUT,
            "tool_response": _async_launch_response(),
        }
        response = ClaudeCodeAdapter().adapt_post_tool_use(
            HookEvent(
                event_type=HookEventType.POST_TOOL_USE,
                session_id="s-52",
                payload=payload,
            )
        )

        assert response.exit_code == 0
        context = response.output["hookSpecificOutput"]["additionalContext"]
        assert context == (
            "a328e0d9b8f2aa70b: launched, not finalized yet -- "
            "gaia contract view --harness-id a328e0d9b8f2aa70b"
        )
