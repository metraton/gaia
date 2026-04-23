#!/usr/bin/env python3
"""Tests for the SessionEnd hook (AC2).

The SessionEnd hook takes over what ``stop_hook`` used to do at session
teardown: it calls ``unregister_session(session_id)`` exactly once when
Claude Code fires SessionEnd. The benefit is that ``stop_hook`` (which
runs on every turn) no longer empties the registry between user messages,
so the liveness filter used by the cross-session pending scan operates on
a registry that reflects actual session lifecycle -- not per-turn churn.

What this file asserts:

1. The hook unregisters the current ``CLAUDE_SESSION_ID`` via
   ``modules.session.session_registry.unregister_session``.
2. It is robust to a missing ``CLAUDE_SESSION_ID`` (no-op, no raise).
3. A ``SessionRegistryError`` from the registry is swallowed -- shutdown
   must never fail loudly on a best-effort cleanup.
4. It works across the three matcher variants Claude Code emits
   (``prompt_input_exit``, ``logout``, ``other``).
"""

import importlib
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks/ to sys.path so ``import session_end_hook`` resolves the same
# way the production entry point does.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Point session_registry at a tmp file for this test only."""
    from modules.session import session_registry

    registry_file = tmp_path / "session_registry.json"
    monkeypatch.setattr(
        session_registry, "_get_registry_path", lambda: registry_file
    )
    return registry_file


def _load_hook():
    """Import (or reload) the session_end_hook entry module.

    Reloading protects against the module being imported earlier under a
    different CLAUDE_SESSION_ID during the same pytest session.
    """
    if "session_end_hook" in sys.modules:
        return importlib.reload(sys.modules["session_end_hook"])
    return importlib.import_module("session_end_hook")


def _feed_stdin(monkeypatch, payload: dict) -> None:
    """Wire a fake stdin so ``run_hook()`` sees our SessionEnd event."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    # has_stdin_data() uses select() on sys.stdin; rather than fight that,
    # override it directly since every hook in this codebase calls it.
    monkeypatch.setattr(
        "modules.core.stdin.has_stdin_data", lambda: True
    )


# ---------------------------------------------------------------------------
# AC2.1 -- hook calls unregister_session with the current session_id
# ---------------------------------------------------------------------------

class TestUnregistersCurrentSession:
    """The hook must call unregister_session(CLAUDE_SESSION_ID)."""

    def test_unregisters_current_session_on_prompt_input_exit(
        self, isolated_registry, monkeypatch
    ):
        from modules.session import session_registry

        # Pre-populate the registry so we can observe the removal.
        session_registry.register_session("sid-exit", pid=os.getpid())
        assert "sid-exit" in session_registry.get_live_sessions()

        monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-exit")
        _feed_stdin(
            monkeypatch,
            {
                "hook_event_name": "SessionEnd",
                "session_id": "sid-exit",
                "reason": "prompt_input_exit",
            },
        )

        hook = _load_hook()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with pytest.raises(SystemExit) as exc:
                hook.main() if hasattr(hook, "main") else hook._run()
        assert exc.value.code == 0
        assert "sid-exit" not in session_registry.get_live_sessions()

    @pytest.mark.parametrize("reason", ["logout", "other"])
    def test_unregisters_for_all_matcher_variants(
        self, isolated_registry, monkeypatch, reason
    ):
        """All three SessionEnd matchers must trigger the same cleanup."""
        from modules.session import session_registry

        session_registry.register_session(f"sid-{reason}", pid=os.getpid())
        monkeypatch.setenv("CLAUDE_SESSION_ID", f"sid-{reason}")
        _feed_stdin(
            monkeypatch,
            {
                "hook_event_name": "SessionEnd",
                "session_id": f"sid-{reason}",
                "reason": reason,
            },
        )

        hook = _load_hook()
        with redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit) as exc:
                hook.main() if hasattr(hook, "main") else hook._run()
        assert exc.value.code == 0
        assert f"sid-{reason}" not in session_registry.get_live_sessions()


# ---------------------------------------------------------------------------
# AC2.2 -- robustness: missing env var, registry errors
# ---------------------------------------------------------------------------

class TestRobustness:
    """Teardown must never fail loudly -- log and exit 0."""

    def test_missing_claude_session_id_is_noop(
        self, isolated_registry, monkeypatch
    ):
        """No CLAUDE_SESSION_ID means we have nothing to unregister."""
        monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
        _feed_stdin(
            monkeypatch,
            {
                "hook_event_name": "SessionEnd",
                "session_id": "",
                "reason": "other",
            },
        )

        hook = _load_hook()
        with redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit) as exc:
                hook.main() if hasattr(hook, "main") else hook._run()
        assert exc.value.code == 0

    def test_registry_error_is_swallowed(self, isolated_registry, monkeypatch):
        """A SessionRegistryError must not propagate out of the hook."""
        from modules.session import session_registry

        def _boom(session_id):
            raise session_registry.SessionRegistryError("simulated I/O")

        monkeypatch.setenv("CLAUDE_SESSION_ID", "sid-err")
        monkeypatch.setattr(session_registry, "unregister_session", _boom)
        _feed_stdin(
            monkeypatch,
            {
                "hook_event_name": "SessionEnd",
                "session_id": "sid-err",
                "reason": "logout",
            },
        )

        hook = _load_hook()
        with redirect_stdout(io.StringIO()):
            with pytest.raises(SystemExit) as exc:
                hook.main() if hasattr(hook, "main") else hook._run()
        assert exc.value.code == 0
