#!/usr/bin/env python3
"""Tests for pending approval TTL semantics.

Context: two constants exist in approval_grants.py:
- DEFAULT_GRANT_TTL_MINUTES = 5    (active grant after user approval)
- DEFAULT_PENDING_TTL_MINUTES = 1440 (pending approval waiting for user response)

These must stay separate. The pending TTL (1440 = 24h) is the design:
user has a full day to come back and approve. Reducing it would break
legitimate workflows.

Since M2, cross-session surfacing of pendings has been removed: the DB feed
``scan_pending_db()`` that surfaced rows into the SessionStart [ACTIONABLE]
block no longer exists. The DB remains the canonical pending store; TTL hygiene
lives in ``approval_cleanup`` (exercised by
tests/hooks/modules/security/test_approval_cleanup.py and
tests/hooks/test_cleanup_pending_survival.py), and on-demand reads go through
``gaia approvals``. This file now only guards the TTL constants against drift.
"""

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
GAIA_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(GAIA_ROOT))

from modules.security.approval_grants import (
    DEFAULT_GRANT_TTL_MINUTES,
    DEFAULT_PENDING_TTL_MINUTES,
)
from gaia.store.writer import APPROVAL_GRANT_TTL_MINUTES


class TestTTLConstants:
    """Regression guards: the two TTL constants must not drift."""

    def test_default_pending_ttl_is_1440(self):
        """Pending TTL is 24h by design — user may come back next day."""
        assert DEFAULT_PENDING_TTL_MINUTES == 1440, (
            f"DEFAULT_PENDING_TTL_MINUTES must be 1440 (24h). "
            f"Got {DEFAULT_PENDING_TTL_MINUTES}. Reducing this would break "
            f"legitimate cross-session approval workflows."
        )

    def test_default_grant_ttl_is_5(self):
        """Grant TTL is 5 min by design (approvals redesign, M1).

        The grant is consumed AT THE MATCH (bash_validator flips the row
        PENDING->CONSUMED when it authorizes the command in PreToolUse), so the
        active-grant window only needs to cover the block -> approve -> retry
        round trip. It is SHORT relative to the 24h pending TTL -- the two stay
        distinct (see test_pending_and_grant_ttls_are_distinct).
        """
        assert DEFAULT_GRANT_TTL_MINUTES == 5, (
            f"DEFAULT_GRANT_TTL_MINUTES must be 5 minutes (M1). "
            f"Got {DEFAULT_GRANT_TTL_MINUTES}."
        )

    def test_grant_ttl_source_of_truth_is_5(self):
        """The single source of truth (writer.APPROVAL_GRANT_TTL_MINUTES) is 5,
        and the hooks-plane mirror (DEFAULT_GRANT_TTL_MINUTES) reflects it."""
        assert APPROVAL_GRANT_TTL_MINUTES == 5, (
            f"APPROVAL_GRANT_TTL_MINUTES must be 5 minutes (M1). "
            f"Got {APPROVAL_GRANT_TTL_MINUTES}."
        )
        assert DEFAULT_GRANT_TTL_MINUTES == APPROVAL_GRANT_TTL_MINUTES, (
            "DEFAULT_GRANT_TTL_MINUTES (hooks plane) must mirror the writer "
            "source of truth APPROVAL_GRANT_TTL_MINUTES."
        )

    def test_grant_ttl_differs_from_pending_ttl(self):
        """The grant TTL (5) must not be the pending TTL (1440 / 24h)."""
        assert DEFAULT_GRANT_TTL_MINUTES != DEFAULT_PENDING_TTL_MINUTES
        assert DEFAULT_PENDING_TTL_MINUTES == 1440
        assert DEFAULT_GRANT_TTL_MINUTES == 5

    def test_pending_and_grant_ttls_are_distinct(self):
        """Pending and grant TTLs must remain separate concepts."""
        assert DEFAULT_PENDING_TTL_MINUTES != DEFAULT_GRANT_TTL_MINUTES, (
            "Pending TTL (approval wait time) and grant TTL (active grant "
            "duration) must be different constants. Conflating them breaks "
            "either the approval window or the grant window."
        )
