#!/usr/bin/env python3
"""Tests for pending approval TTL semantics.

Context: two constants exist in approval_grants.py:
- DEFAULT_GRANT_TTL_MINUTES = 5    (active grant after user approval)
- DEFAULT_PENDING_TTL_MINUTES = 1440 (pending approval waiting for user response)

These must stay separate. The pending TTL (1440 = 24h) is the design:
user has a full day to come back and approve. Reducing it would break
legitimate workflows.

The scanner in pending_scanner.py honors the stored ttl_minutes field
on each pending file. This test suite locks that contract.
"""

import json
import sys
import tempfile
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_grants import (
    DEFAULT_GRANT_TTL_MINUTES,
    DEFAULT_PENDING_TTL_MINUTES,
)
from modules.session.pending_scanner import scan_pending_approvals


def _write_pending(dir_path: Path, nonce: str, session_id: str, age_hours: float,
                   ttl_minutes: int = 1440, status: str = None) -> Path:
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
    if status:
        payload["status"] = status
    file_path.write_text(json.dumps(payload))
    return file_path


def _fresh_dir() -> Path:
    """Mint a fresh tmp dir per test helper invocation."""
    return Path(tempfile.mkdtemp(prefix="gaia-pending-test-"))


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
        """Grant TTL is 5 min by design — short re-execution window."""
        assert DEFAULT_GRANT_TTL_MINUTES == 5, (
            f"DEFAULT_GRANT_TTL_MINUTES must be 5 minutes. "
            f"Got {DEFAULT_GRANT_TTL_MINUTES}. Increasing this would let "
            f"approved grants survive beyond the intended re-try window."
        )

    def test_pending_and_grant_ttls_are_distinct(self):
        """Pending and grant TTLs must remain separate concepts."""
        assert DEFAULT_PENDING_TTL_MINUTES != DEFAULT_GRANT_TTL_MINUTES, (
            "Pending TTL (approval wait time) and grant TTL (active grant "
            "duration) must be different constants. Conflating them breaks "
            "either the approval window or the grant window."
        )


class TestScannerRespectsStoredTTL:
    """Scanner honors the ttl_minutes field stored in each JSON file."""

    def test_legacy_1440_pending_expired_at_25h_is_unlinked(self):
        """Pending with ttl=1440 and age 25h is past TTL -> scanner unlinks."""
        dir_path = _fresh_dir()
        expired = _write_pending(
            dir_path,
            nonce="1111111111111111aaaaaaaaaaaaaaaa",
            session_id="any-session",
            age_hours=25.0,
            ttl_minutes=1440,
        )
        results = scan_pending_approvals(dir_path, current_session_id="any-session")
        assert not expired.exists(), (
            "Pending older than its stored ttl must be cleaned by the scanner"
        )
        assert len(results) == 0

    def test_legacy_1440_pending_at_20h_is_preserved(self):
        """Pending with ttl=1440 and age 20h is WITHIN TTL -> preserved.

        This is the exact scenario of the real ghost bug: the 20h-old
        pendings were NOT expired yet. Scanner must preserve them and
        report them as pendings.
        """
        dir_path = _fresh_dir()
        alive = _write_pending(
            dir_path,
            nonce="2222222222222222bbbbbbbbbbbbbbbb",
            session_id="any-session",
            age_hours=20.0,
            ttl_minutes=1440,
        )
        results = scan_pending_approvals(dir_path, current_session_id="any-session")
        assert alive.exists(), (
            "Pending within stored TTL must be preserved; the real fix for "
            "ghosts is at the creation side (Fix A: --help exemption), not "
            "at the scanner side."
        )
        assert len(results) == 1

    def test_rejected_pending_is_always_cleaned(self):
        """A pending with status='rejected' is cleaned regardless of age."""
        dir_path = _fresh_dir()
        rejected = _write_pending(
            dir_path,
            nonce="3333333333333333cccccccccccccccc",
            session_id="any-session",
            age_hours=0.1,
            ttl_minutes=1440,
            status="rejected",
        )
        results = scan_pending_approvals(dir_path, current_session_id="any-session")
        assert not rejected.exists()
        assert len(results) == 0


class TestNoCrossSessionDelete:
    """Scanner must NOT delete pendings based on session_id mismatch.

    Two Claude Code sessions running in parallel in the same cwd would
    each have their own session_id. The scanner must not interpret
    'different session_id' as 'dead session' and wipe live pendings.
    """

    def test_cross_session_pending_within_ttl_is_preserved(self):
        """Cross-session pending, young or old, stays until natural TTL."""
        dir_path = _fresh_dir()
        cross = _write_pending(
            dir_path,
            nonce="4444444444444444dddddddddddddddd",
            session_id="other-live-session",
            age_hours=5.0,
            ttl_minutes=1440,
        )
        results = scan_pending_approvals(dir_path, current_session_id="current-session")
        assert cross.exists(), (
            "Scanner must NOT delete cross-session pendings. A parallel "
            "live session may still be awaiting approval on this pending."
        )
