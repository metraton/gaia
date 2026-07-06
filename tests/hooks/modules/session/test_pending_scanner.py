#!/usr/bin/env python3
"""Tests for pending_scanner after cross-session surfacing removal (M2).

Cross-session surfacing of pendings has been eliminated:

  * ``scan_pending_db()`` -- the DB feed that surfaced pendings into the
    SessionStart [ACTIONABLE] block -- has been DELETED. These tests lock that
    removal in: the symbol must no longer exist on the module.
  * ``scan_pending_approvals()`` remains a retired stub returning [] (Task E FS
    retirement); a smoke-test verifies that contract.

The DB is still the canonical pending store, but nothing in pending_scanner
surfaces it: TTL hygiene reads gaia.approvals.store.list_pending directly, and
the user inspects pendings on demand via `gaia approvals`.
"""

import json
import sys
from pathlib import Path

import pytest

# Add hooks to path so `from modules.session...` resolves correctly.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import pending_scanner
from modules.session.pending_scanner import scan_pending_approvals


# ---------------------------------------------------------------------------
# scan_pending_db — surfacing feed REMOVED (M2)
# ---------------------------------------------------------------------------

class TestScanPendingDbRemoved:
    """The DB surfacing feed no longer exists on the module."""

    def test_scan_pending_db_symbol_is_gone(self):
        """scan_pending_db must no longer be importable from pending_scanner."""
        assert not hasattr(pending_scanner, "scan_pending_db"), (
            "scan_pending_db was the cross-session surfacing feed; it must be "
            "removed. Pendings are no longer injected into session context."
        )

    def test_scan_pending_db_cannot_be_imported(self):
        """A direct import of scan_pending_db must fail (ImportError)."""
        with pytest.raises(ImportError):
            from modules.session.pending_scanner import scan_pending_db  # noqa: F401


# ---------------------------------------------------------------------------
# scan_pending_approvals — retired stub contract (unchanged)
# ---------------------------------------------------------------------------

class TestScanPendingApprovalsRetired:
    """scan_pending_approvals() is retired; it returns [] without scanning."""

    def test_returns_empty_list(self, tmp_path):
        """Stub must return [] regardless of directory contents."""
        approvals_dir = tmp_path / "approvals"
        approvals_dir.mkdir()
        # Write a fake pending file — the retired stub must ignore it.
        (approvals_dir / "pending-abc123.json").write_text(
            json.dumps({"nonce": "abc123", "session_id": "any", "command": "x"})
        )
        result = scan_pending_approvals(approvals_dir)
        assert result == [], "Retired stub must return [] unconditionally"

    def test_exclude_live_sessions_is_a_parameter(self):
        """Signature still accepts exclude_live_sessions for backward compat."""
        import inspect
        params = inspect.signature(scan_pending_approvals).parameters
        assert "exclude_live_sessions" in params

    def test_exclude_live_sessions_defaults_to_false(self):
        """Default value preserved for backward compat."""
        import inspect
        params = inspect.signature(scan_pending_approvals).parameters
        assert params["exclude_live_sessions"].default is False


# ---------------------------------------------------------------------------
# format_pending_summary — retained generic formatter (no surfacing feed)
# ---------------------------------------------------------------------------

class TestFormattersRetained:
    """The formatting helpers remain as generic utilities and never raise."""

    def test_format_pending_summary_empty_is_empty_string(self):
        from modules.session.pending_scanner import format_pending_summary
        assert format_pending_summary([]) == ""
