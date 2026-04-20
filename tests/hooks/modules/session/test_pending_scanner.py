#!/usr/bin/env python3
"""
Tests for pending_scanner.scan_pending_approvals() — exclude_live_sessions flag.

Validates T12 plumbing between session_registry (T11) and the
user_prompt_submit / CLI consumers (T13):

1. exclude_live_sessions=False (or omitted) keeps the pre-T12 behavior:
   every pending is returned, regardless of session liveness.
2. exclude_live_sessions=True filters out pendings whose session_id
   appears in session_registry.get_live_sessions().
3. If get_live_sessions() raises, the scanner logs a warning and
   returns all pendings unfiltered (conservative — do not lose pendings
   on a registry bug).
"""

import json
import logging
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

# Add hooks to path so `from modules.session...` resolves correctly.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session.pending_scanner import scan_pending_approvals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_pending(
    dir_path: Path,
    nonce: str,
    session_id: str,
    age_hours: float = 1.0,
    ttl_minutes: int = 1440,
) -> Path:
    """Write a fake pending-{nonce}.json file with given age and session."""
    file_path = dir_path / f"pending-{nonce}.json"
    payload = {
        "nonce": nonce,
        "session_id": session_id,
        "command": f"fake-cmd-{nonce[:6]}",
        "danger_verb": "update",
        "danger_category": "MUTATIVE",
        "timestamp": time.time() - (age_hours * 3600),
        "ttl_minutes": ttl_minutes,
        "context": {},
    }
    file_path.write_text(json.dumps(payload))
    return file_path


@pytest.fixture
def approvals_dir(tmp_path):
    """Per-test approvals directory."""
    d = tmp_path / "approvals"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Signature contract (AC from plan)
# ---------------------------------------------------------------------------

class TestSignature:
    """Contract guard: exclude_live_sessions must be a public parameter."""

    def test_exclude_live_sessions_is_a_parameter(self):
        """The new parameter must appear in the function signature."""
        import inspect
        params = inspect.signature(scan_pending_approvals).parameters
        assert "exclude_live_sessions" in params, (
            "exclude_live_sessions must be a public parameter of "
            "scan_pending_approvals — consumed by T13 (user_prompt_submit "
            "and `gaia approvals list --orphans-only`)."
        )

    def test_exclude_live_sessions_defaults_to_false(self):
        """Default must be False to preserve backward compat."""
        import inspect
        params = inspect.signature(scan_pending_approvals).parameters
        assert params["exclude_live_sessions"].default is False, (
            "Default must remain False. All pre-T12 callers pass 0-3 "
            "positional args and must keep the old behavior."
        )


# ---------------------------------------------------------------------------
# Behavior: default path (flag False / omitted)
# ---------------------------------------------------------------------------

class TestExcludeLiveSessionsDefault:
    """exclude_live_sessions=False (or omitted) = pre-T12 behavior."""

    def test_exclude_live_sessions_false_returns_all_pendings(self, approvals_dir):
        """With flag False, both live- and dead-session pendings are returned."""
        _write_pending(approvals_dir, "a" * 32, "session-alive")
        _write_pending(approvals_dir, "b" * 32, "session-dead")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            results = scan_pending_approvals(
                approvals_dir, exclude_live_sessions=False
            )

        sids = sorted(r["pending_session_id"] for r in results)
        assert sids == ["session-alive", "session-dead"], (
            "With exclude_live_sessions=False, every pending must be "
            "returned regardless of registry state."
        )

    def test_exclude_live_sessions_omitted_returns_all_pendings(self, approvals_dir):
        """Omitting the flag entirely must still return all pendings."""
        _write_pending(approvals_dir, "c" * 32, "session-alive")
        _write_pending(approvals_dir, "d" * 32, "session-dead")

        # Patch just to prove we never consulted the registry in this path:
        # if the omitted-flag code accidentally called get_live_sessions and
        # got {"session-alive"}, it would filter, and this assertion would
        # fail on len(results) == 2.
        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            results = scan_pending_approvals(approvals_dir)

        assert len(results) == 2, (
            "Calling scan_pending_approvals() with no flag must not "
            "filter by registry — backward-compat contract."
        )


# ---------------------------------------------------------------------------
# Behavior: flag True (filter path)
# ---------------------------------------------------------------------------

class TestExcludeLiveSessionsTrue:
    """exclude_live_sessions=True drops pendings from live sessions."""

    def test_exclude_live_sessions_true_drops_live_session_pendings(
        self, approvals_dir
    ):
        """With 2 pendings (1 live, 1 dead), only the dead one is returned."""
        _write_pending(approvals_dir, "1" * 32, "session-alive")
        _write_pending(approvals_dir, "2" * 32, "session-dead")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            results = scan_pending_approvals(
                approvals_dir, exclude_live_sessions=True
            )

        assert len(results) == 1, (
            "With exclude_live_sessions=True, pendings whose session_id is "
            "in the live-set must be filtered out."
        )
        assert results[0]["pending_session_id"] == "session-dead", (
            "Only the pending from the non-live (orphaned) session should "
            "survive the filter."
        )

    def test_exclude_live_sessions_true_all_live_returns_empty(self, approvals_dir):
        """If every pending is from a live session, result is empty."""
        _write_pending(approvals_dir, "3" * 32, "session-alive")
        _write_pending(approvals_dir, "4" * 32, "session-alive")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value={"session-alive"},
        ):
            results = scan_pending_approvals(
                approvals_dir, exclude_live_sessions=True
            )

        assert results == [], (
            "When all pendings belong to live sessions, the filtered list "
            "must be empty."
        )

    def test_exclude_live_sessions_true_no_live_returns_all(self, approvals_dir):
        """Empty live-set = no filtering = all pendings returned."""
        _write_pending(approvals_dir, "5" * 32, "session-A")
        _write_pending(approvals_dir, "6" * 32, "session-B")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            return_value=set(),
        ):
            results = scan_pending_approvals(
                approvals_dir, exclude_live_sessions=True
            )

        assert len(results) == 2, (
            "An empty live-set means no pending should be filtered — "
            "every session is orphaned by definition."
        )


# ---------------------------------------------------------------------------
# Behavior: registry error path (conservative fallback)
# ---------------------------------------------------------------------------

class TestExcludeLiveSessionsRegistryError:
    """On registry failure, return all pendings + log warning."""

    def test_registry_exception_returns_all_pendings(self, approvals_dir, caplog):
        """get_live_sessions() raising must NOT lose pendings."""
        _write_pending(approvals_dir, "7" * 32, "session-alive")
        _write_pending(approvals_dir, "8" * 32, "session-dead")

        def _boom():
            raise RuntimeError("registry file corrupt")

        with patch(
            "modules.session.session_registry.get_live_sessions",
            side_effect=_boom,
        ):
            with caplog.at_level(logging.WARNING, logger="modules.session.pending_scanner"):
                results = scan_pending_approvals(
                    approvals_dir, exclude_live_sessions=True
                )

        assert len(results) == 2, (
            "When get_live_sessions() raises, the scanner must fall back "
            "to returning all pendings. Losing real pendings on a "
            "registry bug would be a worse failure mode than showing "
            "extras that already-live sessions will also resolve."
        )
        # Warning must be logged so the bug is not silent.
        assert any(
            "get_live_sessions() failed" in record.getMessage()
            for record in caplog.records
        ), (
            "A warning must be logged when get_live_sessions() raises, "
            "so the underlying registry bug is still visible."
        )
