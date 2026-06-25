#!/usr/bin/env python3
"""Tests for the ElicitationResult hook.

Validates:
1. Approval response activates pending grants
2. Rejection response does NOT activate grants
3. Empty/malformed input exits 0 (no crash)
4. No pending approvals = no-op
5. Response extraction from various event schemas
"""

import json
import sys
import time
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from elicitation_result import _extract_response, _is_approval, _activate_grants
from modules.security.approval_grants import (
    check_approval_grant,
    generate_nonce,
    get_pending_approvals_for_session,
)
from modules.core.paths import clear_path_cache
from tests.fixtures.db_helpers import seed_db_pending


@pytest.fixture(autouse=True)
def clean_grants_dir(tmp_path, monkeypatch):
    """Use a temporary directory for grants and an isolated writer DB.

    Test isolation note (Brief 71, Change 4): the session-agnostic CONSUMED
    replay guard in check_approval_grant() calls gaia.store.writer._connect(); we
    patch it to a per-test SQLite file so the guard cannot read the real
    ~/.gaia/gaia.db and spuriously suppress a legitimate test grant.
    """
    import sqlite3
    import hashlib

    import modules.security.approval_grants as ag

    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-elicitation-session")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False

    writer_db_path = tmp_path / "writer_isolation.db"

    def _make_writer_db() -> sqlite3.Connection:
        con = sqlite3.connect(str(writer_db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1,
            lambda v: hashlib.sha256((v or "").encode()).hexdigest(),
            deterministic=True,
        )
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS approval_grants (
                approval_id           TEXT PRIMARY KEY,
                agent_id              TEXT,
                session_id            TEXT,
                command_set_json      TEXT NOT NULL,
                scope                 TEXT NOT NULL DEFAULT 'COMMAND_SET',
                created_at            TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                expires_at            TEXT,
                status                TEXT NOT NULL DEFAULT 'PENDING',
                consumed_indexes_json TEXT,
                consumed_at           TEXT,
                revoked_at            TEXT
            );
            """
        )
        # The DB pending plane (seed_db_pending -> insert_requested / get_pending)
        # reads the approvals + approval_events tables; materialize them in the
        # isolation DB so the migrated pending functions work test-locally.
        from tests.fixtures.db_helpers import apply_approvals_schema
        apply_approvals_schema(con)
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", lambda db_path_arg=None: _make_writer_db())

    yield grants_dir
    clear_path_cache()


class TestExtractResponse:
    """Test response extraction from various ElicitationResult event schemas."""

    def test_extract_from_result_field(self):
        event = {"result": "Approve"}
        assert _extract_response(event) == "Approve"

    def test_extract_from_answer_field(self):
        event = {"answer": "Approve"}
        assert _extract_response(event) == "Approve"

    def test_extract_from_selected_field(self):
        event = {"selected": "Reject"}
        assert _extract_response(event) == "Reject"

    def test_extract_from_nested_result(self):
        event = {"result": {"answer": "Approve"}}
        assert _extract_response(event) == "Approve"

    def test_extract_from_nested_selected(self):
        event = {"hookEventInput": {"selected": "Approve"}}
        assert _extract_response(event) == "Approve"

    def test_extract_from_answers_dict(self):
        event = {"result": {"answers": {"approval": "Approve"}}}
        assert _extract_response(event) == "Approve"

    def test_extract_returns_none_for_empty_event(self):
        assert _extract_response({}) is None

    def test_extract_returns_none_for_no_recognized_fields(self):
        event = {"unrelated_field": "something", "another": 42}
        assert _extract_response(event) is None

    def test_extract_skips_none_values(self):
        event = {"result": None, "answer": "Approve"}
        assert _extract_response(event) == "Approve"

    def test_extract_skips_empty_strings(self):
        event = {"result": "", "answer": "Approve"}
        assert _extract_response(event) == "Approve"


class TestIsApproval:
    """Test approval detection logic."""

    def test_approve_exact(self):
        assert _is_approval("Approve") is True

    def test_approve_lowercase(self):
        assert _is_approval("approve") is True

    def test_approved_past_tense(self):
        assert _is_approval("Approved") is True

    def test_yes(self):
        assert _is_approval("yes") is True

    def test_accept(self):
        assert _is_approval("Accept") is True

    def test_confirm(self):
        assert _is_approval("confirm") is True

    def test_allow(self):
        assert _is_approval("Allow") is True

    def test_reject_is_not_approval(self):
        assert _is_approval("Reject") is False

    def test_modify_is_not_approval(self):
        assert _is_approval("Modify") is False

    def test_no_is_not_approval(self):
        assert _is_approval("no") is False

    def test_empty_is_not_approval(self):
        assert _is_approval("") is False

    def test_whitespace_only_is_not_approval(self):
        assert _is_approval("   ") is False

    def test_cancel_is_not_approval(self):
        assert _is_approval("cancel") is False

    def test_approve_with_description(self):
        assert _is_approval("Approve -- Allow the operation to proceed") is True


class TestActivateGrants:
    """Test grant activation via _activate_grants."""

    def test_approval_activates_pending_grant(self):
        """A nonce-tagged approval response should activate the pending grant."""
        session_id = "test-elicitation-session"
        command = "terraform apply"

        # Create a DB pending approval
        nonce = generate_nonce()
        seed_db_pending(
            command=command,
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce,
        )

        # Verify pending exists
        pending = get_pending_approvals_for_session(session_id)
        assert len(pending) == 1

        # Activate grants via the nonce-targeted elicitation path
        response = f"Approve -- {command} [P-{nonce[:8]}]"
        _activate_grants(session_id, response=response)

        # Verify grant is now active
        grant = check_approval_grant(command)
        assert grant is not None, "Grant should be active after activation"
        assert grant.approved_scope == command

        # Verify pending is consumed
        pending_after = get_pending_approvals_for_session(session_id)
        assert len(pending_after) == 0, "Pending should be consumed after activation"

    def test_no_pending_is_noop(self):
        """No pending approvals should be a silent no-op."""
        session_id = "test-elicitation-session"

        # No pending approvals exist -- should not raise
        _activate_grants(session_id)

        # No grants created
        grant = check_approval_grant("terraform apply")
        assert grant is None

    def test_multiple_pending_all_activated(self):
        """Multiple pending approvals are each activated by their own nonce.

        The session-wide FS sweep (activate_grants_for_session) was retired with
        the filesystem pending plane; activation is now per-nonce via the DB
        bridge. Activating each pending by its nonce leaves all grants active and
        all pending consumed -- the behavior this test guards.
        """
        session_id = "test-elicitation-session"

        nonce1 = generate_nonce()
        seed_db_pending(
            command="terraform apply",
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce1,
        )

        nonce2 = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_id,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce2,
        )

        # Verify both pending exist before activation
        pending_before = get_pending_approvals_for_session(session_id)
        assert len(pending_before) == 2, "Should have 2 pending approvals"

        # Activate each pending via its own nonce-targeted elicitation response
        _activate_grants(
            session_id, response=f"Approve -- terraform apply [P-{nonce1[:8]}]",
        )
        _activate_grants(
            session_id, response=f"Approve -- git push origin main [P-{nonce2[:8]}]",
        )

        # Both grants should be findable
        grant1 = check_approval_grant("terraform apply")
        assert grant1 is not None, "First grant should be active"
        grant2 = check_approval_grant("git push origin main")
        assert grant2 is not None, "Second grant should be active"

        # Verify all pending consumed
        pending_after = get_pending_approvals_for_session(session_id)
        assert len(pending_after) == 0, "All pending should be consumed"


class TestNonceTargetedActivation:
    """Test nonce-targeted activation via _activate_grants(response=...)."""

    def test_nonce_targeted_activation_via_elicitation(self):
        """Response with [P-<nonce>] activates only that specific pending grant."""
        session_id = "test-elicitation-session"

        # Create two pending approvals
        nonce1 = generate_nonce()
        seed_db_pending(
            command="terraform apply",
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce1,
        )

        nonce2 = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_id,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce2,
        )

        # Build a response that contains the nonce prefix for the first pending
        nonce_prefix = nonce1[:8]
        response = f"Approve -- terraform apply [P-{nonce_prefix}]"

        # Activate using the nonce-targeted path
        _activate_grants(session_id, response=response)

        # The first grant (terraform apply) should be activated
        grant1 = check_approval_grant("terraform apply")
        assert grant1 is not None, "Targeted grant should be active"
        assert grant1.approved_scope == "terraform apply"

        # The second grant (git push) should still be pending -- NOT activated
        pending_after = get_pending_approvals_for_session(session_id)
        assert len(pending_after) == 1, (
            "Only the targeted pending should be consumed; the other should remain"
        )

    def test_no_nonce_response_activates_nothing(self):
        """Response without [P-...] activates nothing.

        The session-wide FS fallback was retired with the filesystem pending
        plane. _activate_grants() now resolves a pending strictly by its nonce
        prefix (activate_db_pending_by_prefix); a response carrying no [P-...]
        tag is a no-op and every pending stays pending.
        """
        session_id = "test-elicitation-session"

        nonce1 = generate_nonce()
        seed_db_pending(
            command="terraform apply",
            session_id=session_id,
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce1,
        )

        nonce2 = generate_nonce()
        seed_db_pending(
            command="git push origin main",
            session_id=session_id,
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce2,
        )

        # Response has no [P-...] tag -- nothing to target
        response = "Approve -- Allow the operation to proceed"

        _activate_grants(session_id, response=response)

        # No grant should be activated without a nonce target
        assert check_approval_grant("terraform apply") is None
        assert check_approval_grant("git push origin main") is None

        # Both pending remain
        pending_after = get_pending_approvals_for_session(session_id)
        assert len(pending_after) == 2, "No pending should be consumed without a nonce"

    def test_cross_session_nonce_activation_via_elicitation(self):
        """Response nonce from a prior session creates grant under current session.

        The DB bridge (activate_db_pending_by_prefix) queries pending across all
        sessions and creates the grant under the current session, so a pending
        seeded in a prior session is activatable from the current one.
        """
        prior_session = "prior-session-abc"
        current_session = "test-elicitation-session"

        # Create pending in a DIFFERENT session
        nonce = generate_nonce()
        seed_db_pending(
            command="kubectl delete pod nginx",
            session_id=prior_session,
            danger_verb="delete",
            danger_category="DESTRUCTIVE",
            nonce=nonce,
        )

        nonce_prefix = nonce[:8]
        response = f"Approve -- kubectl delete pod nginx [P-{nonce_prefix}]"

        # Activate from the current session -- cross-session path
        _activate_grants(current_session, response=response)

        # The grant should exist and be usable from the current session
        grant = check_approval_grant("kubectl delete pod nginx")
        assert grant is not None, (
            "Cross-session grant should be active under current session"
        )
        assert grant.approved_scope == "kubectl delete pod nginx"


class TestMalformedInput:
    """Test that malformed/empty input does not crash the hook."""

    def test_empty_string_extracts_none(self):
        assert _extract_response({}) is None

    def test_non_dict_values_handled(self):
        event = {"result": 42, "answer": True}
        # Should not crash, should return None (no string match)
        result = _extract_response(event)
        assert result is None

    def test_deeply_nested_event_does_not_crash(self):
        event = {
            "result": {
                "nested": {
                    "deep": "value"
                }
            }
        }
        # Should not crash even with unexpected nesting
        _extract_response(event)
