#!/usr/bin/env python3
"""Tests for approval_cleanup.cleanup() — preserve_nonces hardening.

Phase 2 introduced ``preserve_nonces`` so the SubagentStop cleanup does not
destroy pending approvals that the agent's final agent_contract_handoff still
references via APPROVAL_REQUEST. The user needs those files to act on the
[ACTIONABLE] block surfaced by the orchestrator.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

# Add hooks to path so module imports resolve.
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security import approval_cleanup
from modules.security.approval_cleanup import cleanup


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def approvals_dir(tmp_path, monkeypatch):
    """Redirect _get_approvals_dir() to a tmp directory for each test."""
    d = tmp_path / "approvals"
    d.mkdir()
    monkeypatch.setattr(
        approval_cleanup, "_get_approvals_dir", lambda: d
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-test")
    yield d


def _write_pending(approvals_dir: Path, nonce: str, session_id: str = "sess-test") -> Path:
    """Write a minimal pending file for the given nonce / session."""
    file_path = approvals_dir / f"pending-{nonce}.json"
    file_path.write_text(
        json.dumps(
            {
                "nonce": nonce,
                "session_id": session_id,
                "command": f"fake-cmd-{nonce[:6]}",
                "danger_verb": "update",
                "danger_category": "MUTATIVE",
                "timestamp": 1700000000.0,
            }
        )
    )
    return file_path


# ---------------------------------------------------------------------------
# Back-compat: no preserve_nonces argument behaves as before
# ---------------------------------------------------------------------------

class TestBackCompat:
    def test_cleanup_without_preserve_deletes_all_session_pendings(self, approvals_dir):
        a = _write_pending(approvals_dir, "a" * 32)
        b = _write_pending(approvals_dir, "b" * 32)

        consumed = cleanup("test-agent", session_id="sess-test")

        assert consumed is True
        assert not a.exists()
        assert not b.exists()

    def test_cleanup_with_none_preserve_nonces_deletes_all(self, approvals_dir):
        a = _write_pending(approvals_dir, "a" * 32)
        b = _write_pending(approvals_dir, "b" * 32)

        cleanup("test-agent", session_id="sess-test", preserve_nonces=None)

        assert not a.exists()
        assert not b.exists()

    def test_cleanup_with_empty_preserve_set_deletes_all(self, approvals_dir):
        a = _write_pending(approvals_dir, "a" * 32)

        cleanup("test-agent", session_id="sess-test", preserve_nonces=set())

        assert not a.exists()


# ---------------------------------------------------------------------------
# preserve_nonces — the core Phase 2 contract
# ---------------------------------------------------------------------------

class TestPreserveNonces:
    def test_preserve_nonces_keeps_listed_pendings(self, approvals_dir):
        """3 pendings of the same session, preserve B. Expect A and C deleted, B kept."""
        nonce_a = "a" * 32
        nonce_b = "b" * 32
        nonce_c = "c" * 32

        file_a = _write_pending(approvals_dir, nonce_a)
        file_b = _write_pending(approvals_dir, nonce_b)
        file_c = _write_pending(approvals_dir, nonce_c)

        consumed = cleanup(
            "test-agent",
            session_id="sess-test",
            preserve_nonces={nonce_b},
        )

        assert consumed is True
        assert not file_a.exists()
        assert file_b.exists(), (
            "Preserved nonce must NOT be deleted — the agent's contract still "
            "references it via APPROVAL_REQUEST and the user needs the file."
        )
        assert not file_c.exists()

    def test_preserve_emits_log_line(self, approvals_dir, caplog):
        nonce = "b" * 32
        _write_pending(approvals_dir, nonce)

        with caplog.at_level(
            logging.INFO, logger="modules.security.approval_cleanup"
        ):
            cleanup(
                "test-agent",
                session_id="sess-test",
                preserve_nonces={nonce},
            )

        assert any(
            "Preserving pending nonce=" in record.getMessage()
            and nonce[:12] in record.getMessage()
            for record in caplog.records
        ), (
            "Cleanup must emit a 'Preserving pending nonce=...' log line so "
            "the operator can audit which pendings survived the sweep."
        )

    def test_preserve_does_not_protect_other_sessions(self, approvals_dir):
        """preserve_nonces only narrows the in-session cleanup. Other-session
        pendings were already untouched by cleanup (session filter), and that
        does not change.
        """
        nonce_other = "d" * 32
        other = _write_pending(
            approvals_dir, nonce_other, session_id="sess-other"
        )

        cleanup(
            "test-agent",
            session_id="sess-test",
            preserve_nonces={nonce_other},
        )

        # The other-session pending was never deletion-eligible anyway.
        assert other.exists()

    def test_preserve_skips_only_exact_match(self, approvals_dir):
        """preserve_nonces uses full-nonce equality; partial/prefix doesn't shield."""
        nonce_full = "e" * 32
        file_full = _write_pending(approvals_dir, nonce_full)

        cleanup(
            "test-agent",
            session_id="sess-test",
            preserve_nonces={nonce_full[:8]},  # prefix only -- must NOT match
        )

        assert not file_full.exists(), (
            "Cleanup must compare full nonces, not prefixes. A short [P-xxxx] "
            "tag that happens to share a prefix should not accidentally keep "
            "an unrelated pending alive."
        )

    def test_corrupt_files_are_removed_regardless_of_preserve(self, approvals_dir):
        """A corrupt pending file cannot be matched against preserve_nonces."""
        bad = approvals_dir / "pending-corrupt.json"
        bad.write_text("{ not valid json")

        cleanup(
            "test-agent",
            session_id="sess-test",
            preserve_nonces={"any-nonce"},
        )

        assert not bad.exists()
