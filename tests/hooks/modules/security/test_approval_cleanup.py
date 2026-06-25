#!/usr/bin/env python3
"""Unit tests for approval_cleanup.cleanup() — DB-backed TTL expiry (Fix A).

P-3d23 invariant (Fix A): cleanup() runs at SubagentStop, but SubagentStop is
the NORMAL lifecycle of the block -> approve -> retry flow and subagents share
the main session_id. So cleanup() no longer revokes pendings by session
membership; it ONLY expires pendings that have aged past
DEFAULT_PENDING_TTL_MINUTES (24h). A fresh pending ALWAYS survives, regardless
of the stopping subagent's plan_status.

These are fast unit tests that mock list_pending / expire. The mocked rows must
carry an ``age_seconds`` field (cleanup gates on it) because the mock bypasses
list_pending's real age enrichment. End-to-end coverage against a real schema
DB (the actual TTL gate, the 'expired' transition, the status-has-event trigger,
and provenance) lives in tests/hooks/test_cleanup_pending_survival.py.

Tests verify:
  - Returns False (no-op) when the DB store is unavailable.
  - Returns False when no pending rows exist for the session.
  - A FRESH pending (< TTL) is NEVER expired (the P-3d23 invariant).
  - An AGED pending (>= TTL) IS expired, with provenance (agent_id + reason).
  - preserve_nonces shields an aged pending at the TTL edge (belt-and-suspenders).
  - Handles ValueError from expire() gracefully (already-transitioned race).
"""

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add hooks to path so module imports resolve.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_cleanup import cleanup
from modules.security.approval_grants import DEFAULT_PENDING_TTL_MINUTES

_TTL_SECONDS = DEFAULT_PENDING_TTL_MINUTES * 60
_FRESH_AGE = 60.0                         # 1 minute old -> well within TTL
_AGED = float(_TTL_SECONDS + 3600)       # 1h past the 24h pending window


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(approval_id: str, session_id: str = "sess-test",
              age_seconds: float = _FRESH_AGE) -> dict:
    """Build a minimal pending DB row dict with an explicit age.

    cleanup() reads ``age_seconds`` (list_pending enriches it in production);
    the mock must supply it directly. Default is FRESH so a bare _make_row is
    the survival case.
    """
    return {
        "id": approval_id,
        "session_id": session_id,
        "status": "pending",
        "payload_json": "{}",
        "created_at": "2026-06-24T00:00:00Z",
        "age_seconds": age_seconds,
        "stale": age_seconds > 3600.0,
    }


# ---------------------------------------------------------------------------
# No-op / error paths
# ---------------------------------------------------------------------------

class TestNoOp:
    def test_returns_false_when_store_unavailable(self, monkeypatch):
        """When gaia.approvals.store cannot be imported, cleanup is a no-op."""
        import builtins
        real_import = builtins.__import__

        def _block_store(name, *args, **kwargs):
            if "gaia.approvals.store" in name or name == "gaia.approvals.store":
                raise ImportError("store unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_store)
        result = cleanup("test-agent", session_id="sess-test")
        assert result is False

    def test_returns_false_when_no_pending_rows(self):
        """When list_pending returns [], cleanup returns False."""
        mock_list = MagicMock(return_value=[])
        mock_expire = MagicMock()
        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False
        mock_expire.assert_not_called()

    def test_returns_false_when_list_pending_raises(self):
        """list_pending() raising must not propagate — cleanup is a no-op."""
        mock_list = MagicMock(side_effect=RuntimeError("DB unavailable"))
        with patch("gaia.approvals.store.list_pending", mock_list):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False


# ---------------------------------------------------------------------------
# P-3d23 invariant: fresh pendings are never expired
# ---------------------------------------------------------------------------

class TestFreshPendingSurvives:
    def test_fresh_pending_is_not_expired(self):
        """A FRESH pending for the session must NOT be expired (the core fix)."""
        row = _make_row("P-abc1234500000000", age_seconds=_FRESH_AGE)
        mock_list = MagicMock(return_value=[row])
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False, "Fresh pending must survive (P-3d23 invariant)"
        mock_expire.assert_not_called()

    def test_multiple_fresh_pendings_all_survive(self):
        """All fresh pendings survive a COMPLETE Stop (empty preserve_nonces)."""
        rows = [
            _make_row("P-aaa0000000000000", age_seconds=_FRESH_AGE),
            _make_row("P-bbb0000000000000", age_seconds=_FRESH_AGE),
        ]
        mock_list = MagicMock(return_value=rows)
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup("test-agent", session_id="sess-test", preserve_nonces=None)

        assert result is False
        mock_expire.assert_not_called()


# ---------------------------------------------------------------------------
# TTL expiry: aged pendings are expired with provenance
# ---------------------------------------------------------------------------

class TestExpiry:
    def test_aged_pending_is_expired_with_provenance(self):
        """An aged pending (>= TTL) is expired via store.expire(), carrying
        agent_id and a reason metadata so the event is never null-provenance."""
        row = _make_row("P-abc1234500000000", age_seconds=_AGED)
        mock_list = MagicMock(return_value=[row])
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is True
        mock_expire.assert_called_once()
        _args, kwargs = mock_expire.call_args
        assert mock_expire.call_args.args[0] == "P-abc1234500000000"
        assert kwargs.get("expirer_session") == "sess-test"
        assert kwargs.get("agent_id") == "test-agent", "provenance: agent_id"
        meta = json.loads(kwargs.get("metadata_json"))
        assert meta["reason"] == "expired_ttl"
        assert meta["source"] == "approval_cleanup.cleanup"

    def test_only_aged_pendings_are_expired(self):
        """In a mixed batch, only the aged rows are expired; fresh ones stay."""
        fresh = _make_row("P-fresh00000000000", age_seconds=_FRESH_AGE)
        aged = _make_row("P-aged000000000000", age_seconds=_AGED)
        mock_list = MagicMock(return_value=[fresh, aged])
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is True
        expired_ids = {c.args[0] for c in mock_expire.call_args_list}
        assert expired_ids == {"P-aged000000000000"}


# ---------------------------------------------------------------------------
# preserve_nonces — now belt-and-suspenders at the TTL edge
# ---------------------------------------------------------------------------

class TestPreserveNonces:
    def test_preserve_nonces_shields_aged_pending(self):
        """An aged pending in preserve_nonces is NOT expired (edge guard)."""
        nonce = "P-bbb0000000000000"
        rows = [_make_row(nonce, age_seconds=_AGED)]
        mock_list = MagicMock(return_value=rows)
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup(
                "test-agent",
                session_id="sess-test",
                preserve_nonces={nonce},
            )

        assert result is False
        mock_expire.assert_not_called()

    def test_preserve_emits_log_line_for_aged_row(self, caplog):
        nonce = "P-bbb0000000000000"
        rows = [_make_row(nonce, age_seconds=_AGED)]
        mock_list = MagicMock(return_value=rows)
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            with caplog.at_level(logging.INFO, logger="modules.security.approval_cleanup"):
                cleanup(
                    "test-agent",
                    session_id="sess-test",
                    preserve_nonces={nonce},
                )

        assert any(
            "Preserving pending approval_id=" in record.getMessage()
            for record in caplog.records
        ), "Cleanup must emit a 'Preserving pending approval_id=...' log line"

    def test_fresh_pending_not_in_preserve_still_survives(self):
        """A fresh pending NOT in preserve_nonces still survives -- TTL gate, not
        preserve_nonces, is what protects fresh pendings now."""
        rows = [_make_row("P-aaa0000000000000", age_seconds=_FRESH_AGE)]
        mock_list = MagicMock(return_value=rows)
        mock_expire = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            result = cleanup("test-agent", session_id="sess-test", preserve_nonces=set())

        assert result is False
        mock_expire.assert_not_called()


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestErrorResilience:
    def test_valueerror_from_expire_is_non_fatal(self):
        """ValueError from expire() (already-transitioned) must not propagate."""
        row = _make_row("P-abc1234500000000", age_seconds=_AGED)
        mock_list = MagicMock(return_value=[row])
        mock_expire = MagicMock(side_effect=ValueError("already approved"))

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", mock_expire):
            # Must not raise
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False

    def test_unexpected_expire_error_is_non_fatal(self):
        """Any Exception from expire() must not propagate; later rows still run."""
        rows = [
            _make_row("P-aaa0000000000000", age_seconds=_AGED),
            _make_row("P-bbb0000000000000", age_seconds=_AGED),
        ]
        mock_list = MagicMock(return_value=rows)

        call_count = [0]

        def _sometimes_fail(approval_id, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("transient DB error")

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.expire", side_effect=_sometimes_fail):
            result = cleanup("test-agent", session_id="sess-test")

        # Second row succeeded
        assert result is True
