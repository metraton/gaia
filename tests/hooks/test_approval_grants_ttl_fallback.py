#!/usr/bin/env python3
"""Tests for the TTL fallback semantics in approval_grants.py.

Bug context: four sites in approval_grants.py read ttl_minutes from a
pending-file JSON with a fallback of DEFAULT_GRANT_TTL_MINUTES (= 5).
This is semantically wrong: a pending approval's TTL default should be
DEFAULT_PENDING_TTL_MINUTES (= 1440). If a pending file were ever written
without the ttl_minutes field, the current code would consider it expired
in 5 minutes instead of 24 hours — silently dropping legitimate pendings.

The four sites are:
- line 240  _rebuild_pending_index()
- line 627  activate_pending_approval()
- line 1185 cleanup_expired_pendings()
- line 1236 get_pending_approvals_for_session()

These tests simulate a pending file missing ttl_minutes and verify the
code uses DEFAULT_PENDING_TTL_MINUTES as fallback.
"""

import inspect
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import approval_grants as ag


def _read_source() -> str:
    """Return the full source of approval_grants.py for token inspection."""
    return inspect.getsource(ag)


class TestTTLFallbackUsesPendingConstant:
    """All four fallback call sites must use DEFAULT_PENDING_TTL_MINUTES."""

    def test_fallback_site_in_rebuild_pending_index(self):
        """Line ~240 in _rebuild_pending_index() must fallback to PENDING ttl."""
        source = _read_source()
        # Extract the body of _rebuild_pending_index
        func_src = inspect.getsource(ag._rebuild_pending_index)
        assert "data.get(\"ttl_minutes\", DEFAULT_PENDING_TTL_MINUTES)" in func_src, (
            f"_rebuild_pending_index must fallback to DEFAULT_PENDING_TTL_MINUTES. "
            f"Current source:\n{func_src}"
        )
        assert "DEFAULT_GRANT_TTL_MINUTES" not in func_src, (
            "_rebuild_pending_index should not reference DEFAULT_GRANT_TTL_MINUTES "
            "when reading a pending file's TTL — that conflates two concepts."
        )

    def test_fallback_site_in_activate_pending_approval(self):
        """Line ~627 in activate_pending_approval() must fallback to PENDING ttl."""
        func_src = inspect.getsource(ag.activate_pending_approval)
        # The activation reads pending_data.get("ttl_minutes", ...)
        assert "pending_data.get(\"ttl_minutes\", DEFAULT_PENDING_TTL_MINUTES)" in func_src, (
            "activate_pending_approval must fallback to DEFAULT_PENDING_TTL_MINUTES "
            "when reading a pending file's TTL."
        )

    def test_fallback_site_in_cleanup_expired_grants(self):
        """Line ~1185 in cleanup_expired_grants() (pending branch) must fallback to PENDING ttl."""
        func_src = inspect.getsource(ag.cleanup_expired_grants)
        assert "data.get(\"ttl_minutes\", DEFAULT_PENDING_TTL_MINUTES)" in func_src, (
            "cleanup_expired_grants, when iterating pending files, must fallback "
            "to DEFAULT_PENDING_TTL_MINUTES not DEFAULT_GRANT_TTL_MINUTES."
        )

    def test_fallback_site_in_get_pending_approvals_for_session(self):
        """Line ~1236 in get_pending_approvals_for_session() must fallback to PENDING ttl."""
        func_src = inspect.getsource(ag.get_pending_approvals_for_session)
        assert "data.get(\"ttl_minutes\", DEFAULT_PENDING_TTL_MINUTES)" in func_src, (
            "get_pending_approvals_for_session must fallback to "
            "DEFAULT_PENDING_TTL_MINUTES when reading a pending file's TTL."
        )


class TestNoWrongFallbackRemains:
    """Strong invariant: no site reading a pending file falls back to GRANT ttl.

    If any file read of 'ttl_minutes' falls back to DEFAULT_GRANT_TTL_MINUTES,
    a pending without the field would expire in 5 min instead of 24h. The
    legitimate use of DEFAULT_GRANT_TTL_MINUTES is in grant creation/validation,
    not in pending parsing.
    """

    def test_no_ttl_minutes_fallback_uses_grant_constant(self):
        source = _read_source()
        wrong_fallback = 'data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)'
        wrong_fallback_pending = 'pending_data.get("ttl_minutes", DEFAULT_GRANT_TTL_MINUTES)'
        assert wrong_fallback not in source, (
            f"Found wrong fallback pattern '{wrong_fallback}' in approval_grants.py. "
            f"When reading a pending file, fallback must be DEFAULT_PENDING_TTL_MINUTES."
        )
        assert wrong_fallback_pending not in source, (
            f"Found wrong fallback pattern '{wrong_fallback_pending}' in approval_grants.py. "
            f"When reading a pending file, fallback must be DEFAULT_PENDING_TTL_MINUTES."
        )
