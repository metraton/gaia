#!/usr/bin/env python3
"""Tests for the always-on JSONL hook invocation trace.

``configure_hook_logging`` attaches a NullHandler unless GAIA_DEBUG is set, so
a default installation records nothing about which hooks ran. This trace is the
cheap, always-on answer to "did this hook fire?" -- one line per invocation,
size-bounded, and silent on failure.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core import hook_trace  # noqa: E402


@pytest.fixture
def logs_dir(tmp_path, monkeypatch):
    target = tmp_path / "logs"
    target.mkdir()
    monkeypatch.setattr(hook_trace, "trace_path", lambda: target / hook_trace.TRACE_FILENAME)
    return target


def _lines(logs_dir):
    path = logs_dir / hook_trace.TRACE_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestTraceIsOnByDefault:
    def test_records_one_line_per_invocation(self, logs_dir):
        hook_trace.record_hook_invocation(
            "subagent_stop",
            payload={
                "session_id": "s-9",
                "tool_name": "Task",
                "tool_input": {"subagent_type": "gaia-system"},
            },
            exit_code=0,
        )

        records = _lines(logs_dir)
        assert len(records) == 1
        assert records[0]["hook"] == "subagent_stop"
        assert records[0]["agent"] == "gaia-system"
        assert records[0]["session"] == "s-9"
        assert records[0]["exit_code"] == 0
        assert records[0]["blocked"] is False
        assert records[0]["ts"]

    def test_exit_code_two_is_recorded_as_a_rejection(self, logs_dir):
        hook_trace.record_hook_invocation("pre_tool_use", exit_code=2)

        assert _lines(logs_dir)[0]["blocked"] is True

    def test_explicit_blocked_flag_wins_over_the_exit_code(self, logs_dir):
        """PreToolUse carries its rejection in permissionDecision, not the code."""
        hook_trace.record_hook_invocation("pre_tool_use", exit_code=0, blocked=True)

        assert _lines(logs_dir)[0]["blocked"] is True

    def test_appends_rather_than_overwrites(self, logs_dir):
        hook_trace.record_hook_invocation("session_start")
        hook_trace.record_hook_invocation("session_end_hook")

        assert [r["hook"] for r in _lines(logs_dir)] == [
            "session_start",
            "session_end_hook",
        ]


class TestTraceIsBounded:
    def test_rotates_to_a_single_backup_at_the_size_cap(self, logs_dir, monkeypatch):
        monkeypatch.setenv("GAIA_HOOK_TRACE_MAX_BYTES", "1")

        hook_trace.record_hook_invocation("first")
        hook_trace.record_hook_invocation("second")

        assert (logs_dir / (hook_trace.TRACE_FILENAME + ".1")).exists()
        assert [r["hook"] for r in _lines(logs_dir)] == ["second"]


class TestTraceIsSafe:
    def test_can_be_disabled(self, logs_dir, monkeypatch):
        monkeypatch.setenv("GAIA_HOOK_TRACE", "0")

        hook_trace.record_hook_invocation("post_tool_use")

        assert _lines(logs_dir) == []

    def test_a_write_failure_never_raises(self, monkeypatch):
        monkeypatch.setattr(
            hook_trace, "trace_path", lambda: Path("/nonexistent-dir/trace.jsonl")
        )

        hook_trace.record_hook_invocation("post_tool_use")


class TestRunHookWiring:
    def test_run_hook_records_the_handler_exit_code(self, logs_dir, monkeypatch):
        """Every run_hook entry point traces without touching its own code."""
        from modules.core import hook_entry

        class _Event:
            payload = {"session_id": "s-3", "hook_event_name": "SubagentStop"}

        class _Adapter:
            def parse_event(self, _raw):
                return _Event()

        monkeypatch.setattr(hook_entry, "has_stdin_data", lambda: True)
        monkeypatch.setattr(sys, "stdin", type("S", (), {"read": staticmethod(lambda: "{}")})())
        monkeypatch.setitem(
            sys.modules,
            "adapters.registry",
            type("M", (), {"get_adapter": staticmethod(lambda: _Adapter())}),
        )

        def _handler(_event):
            sys.exit(2)

        with pytest.raises(SystemExit):
            hook_entry.run_hook(_handler, hook_name="subagent_stop")

        records = _lines(logs_dir)
        assert records[-1]["hook"] == "subagent_stop"
        assert records[-1]["exit_code"] == 2
        assert records[-1]["blocked"] is True
