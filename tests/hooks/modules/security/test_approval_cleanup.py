#!/usr/bin/env python3
"""Tests for approval_cleanup.cleanup() — DB-backed revocation (Task E FS retirement).

Since Task E, cleanup() revokes pending DB rows instead of deleting
pending-*.json files.  Tests verify:
  - Returns False (no-op) when the DB store is unavailable.
  - Returns False when no pending rows exist for the session.
  - Returns True and revokes rows that match the session.
  - Skips rows in preserve_nonces (still live APPROVAL_REQUEST).
  - Logs a 'Preserving' line for skipped rows.
  - Leaves other-session rows untouched.
  - Handles ValueError from revoke() gracefully (already-transitioned race).
"""

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add hooks to path so module imports resolve.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_cleanup import cleanup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(approval_id: str, session_id: str = "sess-test") -> dict:
    """Build a minimal pending DB row dict."""
    return {
        "id": approval_id,
        "session_id": session_id,
        "status": "pending",
        "payload_json": "{}",
        "created_at": "2026-06-24T00:00:00Z",
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

    def test_returns_false_when_no_pending_rows(self, monkeypatch):
        """When list_pending returns [], cleanup returns False."""
        mock_list = MagicMock(return_value=[])
        mock_revoke = MagicMock()
        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False
        mock_revoke.assert_not_called()

    def test_returns_false_when_list_pending_raises(self, monkeypatch):
        """list_pending() raising must not propagate — cleanup is a no-op."""
        mock_list = MagicMock(side_effect=RuntimeError("DB unavailable"))
        with patch("gaia.approvals.store.list_pending", mock_list):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False


# ---------------------------------------------------------------------------
# Revocation happy paths
# ---------------------------------------------------------------------------

class TestRevocation:
    def test_revokes_session_pending(self):
        """A pending row for the session is revoked and True is returned."""
        row = _make_row("P-abc1234500000000")
        mock_list = MagicMock(return_value=[row])
        mock_revoke = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is True
        mock_revoke.assert_called_once_with("P-abc1234500000000", revoker_session="sess-test")

    def test_revokes_multiple_pending_rows(self):
        """All pending rows for the session are revoked."""
        rows = [_make_row("P-aaa0000000000000"), _make_row("P-bbb0000000000000")]
        mock_list = MagicMock(return_value=rows)
        mock_revoke = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            result = cleanup("test-agent", session_id="sess-test")

        assert result is True
        assert mock_revoke.call_count == 2


# ---------------------------------------------------------------------------
# preserve_nonces — the core Phase 2 contract (now with DB rows)
# ---------------------------------------------------------------------------

class TestPreserveNonces:
    def test_preserve_nonces_skips_matching_approval_id(self):
        """A row whose approval_id is in preserve_nonces must NOT be revoked."""
        nonce_a = "P-aaa0000000000000"
        nonce_b = "P-bbb0000000000000"
        nonce_c = "P-ccc0000000000000"

        rows = [_make_row(nonce_a), _make_row(nonce_b), _make_row(nonce_c)]
        mock_list = MagicMock(return_value=rows)
        mock_revoke = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            result = cleanup(
                "test-agent",
                session_id="sess-test",
                preserve_nonces={nonce_b},
            )

        assert result is True
        revoked_ids = {c.args[0] for c in mock_revoke.call_args_list}
        assert nonce_a in revoked_ids
        assert nonce_b not in revoked_ids, "Preserved nonce must NOT be revoked"
        assert nonce_c in revoked_ids

    def test_preserve_emits_log_line(self, caplog):
        nonce = "P-bbb0000000000000"
        rows = [_make_row(nonce)]
        mock_list = MagicMock(return_value=rows)
        mock_revoke = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
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

    def test_none_preserve_nonces_revokes_all(self):
        """preserve_nonces=None revokes all session pendings."""
        rows = [_make_row("P-aaa0000000000000"), _make_row("P-bbb0000000000000")]
        mock_list = MagicMock(return_value=rows)
        mock_revoke = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            result = cleanup("test-agent", session_id="sess-test", preserve_nonces=None)

        assert result is True
        assert mock_revoke.call_count == 2

    def test_empty_preserve_set_revokes_all(self):
        """preserve_nonces=set() (empty) revokes all session pendings."""
        rows = [_make_row("P-aaa0000000000000")]
        mock_list = MagicMock(return_value=rows)
        mock_revoke = MagicMock()

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            result = cleanup("test-agent", session_id="sess-test", preserve_nonces=set())

        assert result is True


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------

class TestErrorResilience:
    def test_valueerror_from_revoke_is_non_fatal(self):
        """ValueError from revoke() (already-transitioned) must not propagate."""
        row = _make_row("P-abc1234500000000")
        mock_list = MagicMock(return_value=[row])
        mock_revoke = MagicMock(side_effect=ValueError("already approved"))

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", mock_revoke):
            # Must not raise
            result = cleanup("test-agent", session_id="sess-test")

        assert result is False

    def test_unexpected_revoke_error_is_non_fatal(self):
        """Any Exception from revoke() must not propagate."""
        rows = [_make_row("P-aaa0000000000000"), _make_row("P-bbb0000000000000")]
        mock_list = MagicMock(return_value=rows)

        call_count = [0]
        def _sometimes_fail(approval_id, revoker_session):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("transient DB error")

        with patch("gaia.approvals.store.list_pending", mock_list), \
             patch("gaia.approvals.store.revoke", side_effect=_sometimes_fail):
            result = cleanup("test-agent", session_id="sess-test")

        # Second row succeeded
        assert result is True
