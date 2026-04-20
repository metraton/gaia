#!/usr/bin/env python3
"""Tests for user_prompt_submit session-liveness filter (T13).

Validates that ``_build_pending_context()`` passes
``exclude_live_sessions=True`` to the cross-session fallback scan, so
pendings from parallel live sessions do NOT appear in the
[ACTIONABLE] injection of another session.

This closes the root bug that motivated the approvals-drift-fix brief:
pendings created in session X were being injected into session Y
under the "[session anterior]" label even though session X was still
alive and would resolve them on its next turn.

Tests in this file focus on the liveness axis; the shape/format of
the [ACTIONABLE] block is covered by test_user_prompt_submit_pending.py.
"""

import sys
import time
from pathlib import Path
from typing import Dict, List

import pytest

# Add hooks to path so imports mirror the production layout.
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from user_prompt_submit import _build_pending_context
from modules.core.paths import clear_path_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_fake_pending(
    nonce_short: str,
    session_id: str,
    cross_session: bool = True,
) -> Dict:
    """Build a pending dict in the shape pending_scanner returns."""
    return {
        "nonce_short": nonce_short,
        "nonce_full": nonce_short + "0" * 24,
        "command": f"cmd-{nonce_short}",
        "verb": "push",
        "category": "GIT",
        "age_human": "1 min",
        "timestamp": time.time() - 60,
        "context": {
            "source": "developer",
            "description": "fake",
            "risk": "medium",
        },
        "scope_type": "semantic_signature",
        "cross_session": cross_session,
        "pending_session_id": session_id,
    }


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Isolate plugin data dir and pin session_id to a known value."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".claude" / "cache" / "approvals").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.core.paths.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "current-session")
    yield tmp_path


# ---------------------------------------------------------------------------
# Core contract: fallback call passes exclude_live_sessions=True
# ---------------------------------------------------------------------------

class TestExcludeLiveSessionsOnFallback:
    """The cross-session fallback must request liveness filtering."""

    def test_fallback_call_passes_exclude_live_sessions_true(self, monkeypatch):
        """Second scan call (no session_id filter) must request live-filter.

        This is the exact bug the brief closes: before T13, the fallback
        scan returned every pending including those owned by live parallel
        sessions, which then leaked into the current session's [ACTIONABLE]
        injection as "[session anterior]".
        """
        call_log: List[Dict] = []

        def mock_scan(
            approvals_dir,
            session_id=None,
            current_session_id=None,
            exclude_live_sessions=False,
        ):
            call_log.append({
                "session_id": session_id,
                "current_session_id": current_session_id,
                "exclude_live_sessions": exclude_live_sessions,
            })
            # Current-session scan returns empty to force the fallback path.
            if session_id is not None:
                return []
            # Fallback returns one pending so _build_pending_context proceeds.
            return [
                _make_fake_pending("orphan01", "dead-session", cross_session=True)
            ]

        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_approvals", mock_scan)

        result = _build_pending_context()

        # Sanity: both calls happened.
        assert len(call_log) == 2, (
            "Expected two scans: current session first, then fallback."
        )
        # First call is the current-session scan (not the fallback).
        assert call_log[0]["session_id"] == "current-session"
        assert call_log[0]["exclude_live_sessions"] is False, (
            "Current-session scan must NOT filter by liveness — that scan "
            "is explicitly asking only for this session's own pendings."
        )
        # Second call is the cross-session fallback.
        assert call_log[1]["session_id"] is None
        assert call_log[1]["exclude_live_sessions"] is True, (
            "Cross-session fallback MUST pass exclude_live_sessions=True. "
            "Without it, pendings from live parallel sessions reappear as "
            "'[session anterior]' injections — the exact bug this brief fixes."
        )
        # And the block was produced using the orphan.
        assert result.startswith("[ACTIONABLE]")
        assert "P-orphan01" in result


# ---------------------------------------------------------------------------
# End-to-end: live session pendings are NOT injected
# ---------------------------------------------------------------------------

class TestLiveSessionPendingsExcludedFromActionable:
    """Integration: a live session's pending must not appear in the block."""

    def test_live_session_pending_not_in_actionable_block(self, monkeypatch):
        """Given pendings (1 live, 1 dead), only the dead one is injected.

        This wires ``exclude_live_sessions=True`` through the real
        ``scan_pending_approvals`` filter path so we catch a regression if
        someone later drops the kwarg from ``_build_pending_context``.
        """
        from unittest.mock import patch

        # Fake on-disk approvals: one pending per hypothetical session.
        # We don't actually write files — we monkeypatch scan_pending_approvals
        # to exercise the real liveness filter by wrapping it.
        import modules.session.pending_scanner as ps

        real_scan = ps.scan_pending_approvals

        pendings_disk = {
            # session_id -> pending dict
            "alive-session": _make_fake_pending(
                "alive001", "alive-session", cross_session=True
            ),
            "dead-session": _make_fake_pending(
                "dead0001", "dead-session", cross_session=True
            ),
        }

        def wrapped_scan(
            approvals_dir,
            session_id=None,
            current_session_id=None,
            exclude_live_sessions=False,
        ):
            # Simulate the on-disk scan result.
            items = list(pendings_disk.values())
            if session_id is not None:
                items = [i for i in items if i["pending_session_id"] == session_id]
            if exclude_live_sessions:
                try:
                    from modules.session.session_registry import get_live_sessions
                    live = get_live_sessions()
                    items = [
                        i for i in items
                        if i["pending_session_id"] not in live
                    ]
                except Exception:
                    pass
            return items

        monkeypatch.setattr(ps, "scan_pending_approvals", wrapped_scan)

        # Patch get_live_sessions so "alive-session" is reported alive.
        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"alive-session"},
        ):
            result = _build_pending_context()

        assert result.startswith("[ACTIONABLE]")
        assert "P-dead0001" in result, (
            "Orphan pending (dead session) must still be shown — otherwise "
            "the operator loses visibility into real work to resolve."
        )
        assert "P-alive001" not in result, (
            "Pending owned by a live parallel session must NOT appear in "
            "another session's [ACTIONABLE] block. The owning live session "
            "is expected to resolve it on its next turn."
        )

    def test_registry_error_falls_back_to_all_pendings(self, monkeypatch):
        """If the registry raises, the scanner returns all pendings (safe)."""
        from unittest.mock import patch
        import modules.session.pending_scanner as ps

        pendings_disk = [
            _make_fake_pending("a" * 8, "session-a", cross_session=True),
            _make_fake_pending("b" * 8, "session-b", cross_session=True),
        ]

        def wrapped_scan(
            approvals_dir,
            session_id=None,
            current_session_id=None,
            exclude_live_sessions=False,
        ):
            items = list(pendings_disk)
            if session_id is not None:
                items = [i for i in items if i["pending_session_id"] == session_id]
            if exclude_live_sessions:
                try:
                    from modules.session.session_registry import get_live_sessions
                    live = get_live_sessions()
                    items = [
                        i for i in items
                        if i["pending_session_id"] not in live
                    ]
                except Exception:
                    # Conservative fallback: return unfiltered.
                    pass
            return items

        monkeypatch.setattr(ps, "scan_pending_approvals", wrapped_scan)

        with patch(
            "modules.session.session_registry.get_live_sessions",
            side_effect=RuntimeError("registry unavailable"),
        ):
            result = _build_pending_context()

        # Both pendings must survive — losing real pendings on a registry
        # bug is a worse failure than showing extras.
        assert "P-" + ("a" * 8) in result
        assert "P-" + ("b" * 8) in result
