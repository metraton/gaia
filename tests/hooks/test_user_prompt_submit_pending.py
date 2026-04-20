#!/usr/bin/env python3
"""Tests for _build_pending_context() in user_prompt_submit.

Validates:
1. Returns [ACTIONABLE] prefix when pending approvals exist
2. Returns empty string when no pending approvals
3. Cross-session fallback triggers when current-session scan returns empty
"""

import sys
import time
from pathlib import Path
from typing import Dict

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from user_prompt_submit import _build_pending_context
from modules.core.paths import clear_path_cache


def _make_fake_pending(nonce_short: str = "abcd1234", cross_session: bool = False) -> Dict:
    """Build a fake pending approval dict matching pending_scanner output."""
    return {
        "nonce_short": nonce_short,
        "nonce_full": nonce_short + "0" * 24,
        "command": "git push origin main",
        "verb": "push",
        "category": "GIT",
        "age_human": "5 min",
        "timestamp": time.time() - 300,
        "context": {
            "source": "developer",
            "description": "Push to remote",
            "risk": "HIGH",
        },
        "scope_type": "semantic_signature",
        "cross_session": cross_session,
        "pending_session_id": "other-session" if cross_session else "test-session",
    }


@pytest.fixture(autouse=True)
def setup_env(tmp_path, monkeypatch):
    """Set up temporary approvals directory and mock core dependencies."""
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.core.paths.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-session")
    yield grants_dir


class TestBuildPendingContext:
    """Test _build_pending_context() helper function."""

    def test_returns_actionable_when_pending(self, monkeypatch):
        """When pending approvals exist, result starts with [ACTIONABLE]."""
        fake_pendings = [_make_fake_pending()]

        def mock_scan(approvals_dir, session_id=None, current_session_id=None, exclude_live_sessions=False):
            return fake_pendings

        monkeypatch.setattr(
            "user_prompt_submit.scan_pending_approvals",
            mock_scan,
            raising=False,
        )
        # Patch at the module level where _build_pending_context imports from
        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_approvals", mock_scan)

        result = _build_pending_context()
        assert result.startswith("[ACTIONABLE]")
        assert "Pending approvals require your attention" in result
        assert "P-abcd1234" in result

    def test_returns_empty_when_no_pending(self, monkeypatch):
        """When no pending approvals, returns empty string."""
        def mock_scan(approvals_dir, session_id=None, current_session_id=None, exclude_live_sessions=False):
            return []

        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_approvals", mock_scan)

        result = _build_pending_context()
        assert result == ""

    def test_cross_session_fallback(self, monkeypatch):
        """When current session has no pendings, falls back to all sessions."""
        call_log = []
        cross_pending = [_make_fake_pending(nonce_short="cross123", cross_session=True)]

        def mock_scan(approvals_dir, session_id=None, current_session_id=None, exclude_live_sessions=False):
            call_log.append({
                "session_id": session_id,
                "current_session_id": current_session_id,
                "exclude_live_sessions": exclude_live_sessions,
            })
            # First call: current session filter -> empty
            # Second call: no session filter -> cross-session pending
            if session_id is not None:
                return []
            return cross_pending

        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_approvals", mock_scan)

        result = _build_pending_context()

        # Verify two calls were made
        assert len(call_log) == 2
        # First call had session_id filter
        assert call_log[0]["session_id"] is not None
        # Second call had no session_id filter (cross-session fallback)
        assert call_log[1]["session_id"] is None
        # Result should contain the cross-session pending
        assert result.startswith("[ACTIONABLE]")
        assert "P-cross123" in result

    def test_returns_empty_on_error(self, monkeypatch):
        """On any exception, returns empty string (fail-safe)."""
        def mock_scan(approvals_dir, session_id=None, current_session_id=None, exclude_live_sessions=False):
            raise RuntimeError("simulated failure")

        import modules.session.pending_scanner as ps
        monkeypatch.setattr(ps, "scan_pending_approvals", mock_scan)

        result = _build_pending_context()
        assert result == ""
