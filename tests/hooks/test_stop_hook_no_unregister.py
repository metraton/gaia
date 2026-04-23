#!/usr/bin/env python3
"""Regression test: stop_hook must NOT touch the session registry (AC1).

Before Fix A+C, ``stop_hook`` called ``unregister_session(CLAUDE_SESSION_ID)``
on every Stop event -- i.e. once per turn. That drained the user-scoped
``session_registry.json`` between messages, which in turn poisoned the
``exclude_live_sessions`` filter in the cross-session pending scan: every
sibling session's pendings leaked into the current session's [ACTIONABLE]
block as "[session anterior]" even when the sibling was still alive and
would resolve them on its next turn.

Fix C moves the unregister call out of stop_hook and into a new SessionEnd
hook (exercised by ``test_session_end_hook.py``). Fix A adds PID-based
liveness to the registry so entries self-clean even if SessionEnd never
fires (hard crash). This file guards the Fix C half: it asserts that
running ``stop_hook._handle_stop`` leaves the registry untouched.
"""

import importlib
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Redirect session_registry.json into a tmp location per test."""
    from modules.session import session_registry

    registry_file = tmp_path / "session_registry.json"
    monkeypatch.setattr(
        session_registry, "_get_registry_path", lambda: registry_file
    )
    return registry_file


def _reload_stop_hook():
    if "stop_hook" in sys.modules:
        return importlib.reload(sys.modules["stop_hook"])
    return importlib.import_module("stop_hook")


def _feed_stdin(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(
        "modules.core.stdin.has_stdin_data", lambda: True
    )


class TestStopHookDoesNotMutateRegistry:
    """stop_hook must leave the registry exactly as it found it."""

    def test_registered_session_survives_stop_event(
        self, isolated_registry, monkeypatch
    ):
        """Running Stop once must not evict the current session."""
        from modules.session import session_registry

        session_registry.register_session("sid-live", pid=os.getpid())
        assert "sid-live" in session_registry.get_live_sessions()

        monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-live")
        _feed_stdin(
            monkeypatch,
            {
                "hook_event_name": "Stop",
                "session_id": "sid-live",
                "stop_reason": "end_turn",
            },
        )

        stop_hook = _reload_stop_hook()
        with redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit) as exc:
                stop_hook._handle_stop_entry() if hasattr(
                    stop_hook, "_handle_stop_entry"
                ) else __import__(
                    "modules.core.hook_entry", fromlist=["run_hook"]
                ).run_hook(stop_hook._handle_stop, hook_name="stop_hook")
        assert exc.value.code == 0
        assert "sid-live" in session_registry.get_live_sessions(), (
            "stop_hook evicted the session -- this is the bug Fix C closes. "
            "Between-turn unregister drains the registry, which then "
            "poisons the liveness filter in the cross-session scan."
        )

    def test_multiple_turns_do_not_drain_registry(
        self, isolated_registry, monkeypatch
    ):
        """Simulate 3 Stop events on the same session -- registry stable."""
        from modules.session import session_registry

        session_registry.register_session("sid-loop", pid=os.getpid())
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-loop")

        for _ in range(3):
            _feed_stdin(
                monkeypatch,
                {
                    "hook_event_name": "Stop",
                    "session_id": "sid-loop",
                    "stop_reason": "end_turn",
                },
            )
            stop_hook = _reload_stop_hook()
            with redirect_stdout(io.StringIO()):
                with pytest.raises(SystemExit):
                    from modules.core.hook_entry import run_hook
                    run_hook(stop_hook._handle_stop, hook_name="stop_hook")

        live = session_registry.get_live_sessions()
        assert "sid-loop" in live, (
            f"Registry drained after 3 turns: live={live}. "
            "stop_hook must not call unregister_session."
        )

    def test_stop_hook_does_not_import_unregister(self):
        """Belt-and-braces: the symbol should not even be referenced.

        If a future refactor reintroduces ``unregister_session`` into
        stop_hook, this grep-style check catches it immediately.
        """
        stop_hook_path = HOOKS_DIR / "stop_hook.py"
        source = stop_hook_path.read_text(encoding="utf-8")
        assert "unregister_session" not in source, (
            "stop_hook.py must not reference unregister_session -- "
            "that responsibility belongs to the SessionEnd hook."
        )
