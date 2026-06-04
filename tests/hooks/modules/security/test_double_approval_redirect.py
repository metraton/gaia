#!/usr/bin/env python3
"""Reproduction + regression test for the double-approval grant bug.

Root cause (investigated in-session): the approval signature bound shell
redirect tokens (`2>&1`, `> file`, `2> file`) into its identity. A T3 command
blocked as `git push`, then retried as `git push 2>&1` (the agent appended a
redirect), produced a DIFFERENT signature -- so:
  * the active grant minted for the first form did NOT match the retry, and
  * _find_pending_in_db() (byte-exact) did NOT see the existing pending,
    minting a FRESH approval_id.
The net effect: the user was asked to approve "the same command" twice.

Fixes under test:
  A -- analyze_command / tokenize_command strip redirect tokens, so
       `git push` and `git push 2>&1` share one semantic signature.
  B -- _find_pending_in_db uses the SAME semantic matcher as the consumption
       path, so a semantically-matching retry REUSES the existing pending
       instead of minting a new nonce.

Policy under test (USER DECISION): the `-C <path>` / `--chdir` directory stays
part of the signature (directory = intent). A SAME-path variant matches; a
DIFFERENT-path variant does NOT.

The DB / filesystem isolation fixtures mirror test_activation_db_bridge.py.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Sys-path setup (mirror test_activation_db_bridge.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[5]
HOOKS_DIR = _REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _sha256(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures -- isolated approvals DB + isolated writer DB + temp grants dir.
# Same shape as test_activation_db_bridge.py so both the approvals chain
# (get_pending / insert_requested) and the writer chain
# (check_db_semantic_grant / consume_db_semantic_grant) read test-local DBs.
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_and_store(tmp_path, monkeypatch):
    db_path = tmp_path / "test_dbl.db"
    con = sqlite3.connect(str(db_path))
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("gaia_sha256", 1, lambda v: _sha256(v), deterministic=True)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS approvals (
            id           TEXT PRIMARY KEY,
            agent_id     TEXT,
            session_id   TEXT,
            status       TEXT NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending','approved','rejected','revoked','expired')),
            fingerprint  TEXT,
            payload_json TEXT,
            created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            decided_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS approval_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            approval_id   TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            agent_id      TEXT,
            session_id    TEXT,
            payload_json  TEXT,
            fingerprint   TEXT,
            prev_hash     TEXT,
            this_hash     TEXT,
            metadata_json TEXT,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            FOREIGN KEY (approval_id) REFERENCES approvals(id)
        );
        """
    )
    con.commit()

    monkeypatch.setattr(
        "gaia.approvals.store._open_db",
        lambda: sqlite3.connect(str(db_path)),
    )

    import gaia.approvals.store as store
    orig_get_pending = store.get_pending

    def patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = sqlite3.connect(str(db_path))
        return orig_get_pending(session_id=session_id, all_sessions=all_sessions, con=con)

    monkeypatch.setattr("gaia.approvals.store.get_pending", patched_get_pending)

    yield db_path, con, store
    con.close()


@pytest.fixture(autouse=True)
def isolated_grants_and_writer(tmp_path, monkeypatch):
    import modules.security.approval_grants as ag

    grants_dir = tmp_path / ".claude" / "cache" / "approvals"
    grants_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "modules.security.approval_grants.get_plugin_data_dir",
        lambda: tmp_path / ".claude",
    )
    monkeypatch.setenv("CLAUDE_SESSION_ID", "test-dbl-session")
    ag._last_cleanup_time = 0.0
    ag._grants_dir_created = False

    writer_db_path = tmp_path / "writer_isolation.db"

    def _make_writer_db() -> sqlite3.Connection:
        con = sqlite3.connect(str(writer_db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1, lambda v: _sha256(v), deterministic=True,
        )
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id           TEXT PRIMARY KEY,
                agent_id     TEXT,
                session_id   TEXT,
                status       TEXT NOT NULL DEFAULT 'pending',
                fingerprint  TEXT,
                payload_json TEXT,
                created_at   TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                decided_at   TEXT
            );
            CREATE TABLE IF NOT EXISTS approval_events (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id   TEXT NOT NULL,
                event_type    TEXT NOT NULL,
                agent_id      TEXT,
                session_id    TEXT,
                payload_json  TEXT,
                fingerprint   TEXT,
                prev_hash     TEXT,
                this_hash     TEXT,
                metadata_json TEXT,
                created_at    TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (approval_id) REFERENCES approvals(id)
            );
            CREATE TABLE IF NOT EXISTS approval_grants (
                approval_id          TEXT PRIMARY KEY,
                agent_id             TEXT,
                session_id           TEXT,
                command_set_json     TEXT NOT NULL,
                scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',
                created_at           TEXT NOT NULL
                    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                expires_at           TEXT,
                status               TEXT NOT NULL DEFAULT 'PENDING',
                consumed_indexes_json TEXT,
                consumed_at          TEXT,
                revoked_at           TEXT
            );
            """
        )
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", lambda db_path_arg=None: _make_writer_db())

    yield grants_dir


# ---------------------------------------------------------------------------
# A -- redirect-stripped signature equality (the unit-level proof)
# ---------------------------------------------------------------------------

class TestRedirectNormalization:
    def test_git_push_and_redirect_share_signature(self):
        from modules.security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
        )

        sig_plain = build_approval_signature(
            "git push", scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push", danger_category="MUTATIVE",
        )
        sig_redir = build_approval_signature(
            "git push 2>&1", scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push", danger_category="MUTATIVE",
        )
        assert sig_plain is not None and sig_redir is not None
        assert sig_plain.semantic_tokens == sig_redir.semantic_tokens
        assert sig_plain.normalized_flags == sig_redir.normalized_flags
        assert sig_plain.base_cmd == sig_redir.base_cmd
        # exact_tokens are also redirect-free.
        assert sig_plain.exact_tokens == sig_redir.exact_tokens == ("git", "push")

    def test_redirect_variant_matches_plain_grant(self):
        from modules.security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
            matches_approval_signature,
        )

        sig_redir = build_approval_signature(
            "git push 2>&1", scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push", danger_category="MUTATIVE",
        )
        # Grant built from the redirect form matches the plain retry, and vice versa.
        assert matches_approval_signature(sig_redir, "git push") is True

    def test_pipe_decoration_still_distinct(self):
        from modules.security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
            matches_approval_signature,
        )

        sig = build_approval_signature(
            "git push", scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push", danger_category="MUTATIVE",
        )
        # A pipe is NOT a bare redirect -- it changes identity, must not match.
        assert matches_approval_signature(sig, "git push | tee log") is False


# ---------------------------------------------------------------------------
# The reproduction: block 'git push 2>&1' -> approve -> retry 'git push'.
# Asserts the active grant matches + is consumed, and NO new pending is minted.
# ---------------------------------------------------------------------------

class TestDoubleApprovalReproduction:
    def test_redirect_block_then_plain_retry_reuses_grant(self, db_and_store):
        import gaia.approvals.store as astore
        from modules.tools.bash_validator import validate_bash_command
        from modules.security.approval_grants import activate_db_pending_by_prefix

        db_path, assert_con, store = db_and_store
        session_id = "test-dbl-session"
        blocked_form = "git push 2>&1"   # what the agent first ran
        retry_form = "git push"          # the redirect-stripped retry

        # Step 1: block the redirect form -> DB REQUESTED row + approval_id.
        result1 = validate_bash_command(
            blocked_form, is_subagent=True, session_id=session_id,
        )
        assert not result1.allowed, "T3 push must be blocked"
        reason = result1.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        import re
        m = re.search(r"approval_id:\s*(P-[\w-]+)", reason)
        assert m, f"no approval_id in deny reason: {reason}"
        approval_id_1 = m.group(1)

        pending = astore.get_pending(all_sessions=True, con=assert_con)
        assert len(pending) == 1, f"exactly one pending expected, got {len(pending)}"

        # Step 2: user approves -> grant activated.
        nonce_prefix = approval_id_1[len("P-"):len("P-") + 8]
        activation = activate_db_pending_by_prefix(
            nonce_prefix, current_session_id=session_id,
        )
        assert activation.success, f"activation failed: {activation.reason}"

        # Step 3: retry with the redirect-STRIPPED form -> must be ALLOWED
        # (active grant matches across the redirect difference -- Fix A).
        result2 = validate_bash_command(
            retry_form, is_subagent=True, session_id=session_id,
        )
        assert result2.allowed, (
            f"plain retry must match the grant minted for the redirect form, "
            f"got: {result2.reason}"
        )

        # Step 4: grant is CONSUMED -- a second retry of the SAME operation no
        # longer matches an active grant, so it re-blocks (replay protection).
        from gaia.store.writer import check_db_semantic_grant
        assert check_db_semantic_grant(retry_form, session_id=session_id) is None, (
            "grant must be CONSUMED after the matching retry"
        )

        # Step 5: with the step-1 grant now consumed, re-blocking the operation
        # mints ONE fresh pending; a SECOND block of a semantically-equal variant
        # (different fd-dup redirect) must REUSE that pending, not mint a third.
        #
        # NOTE: we use fd-duplication redirects (`2>&1`, `1>&2`) here, not a
        # trailing `> file` -- the latter is rewritten away by the sanitizer
        # (phase 3d) BEFORE it reaches the T3 block path, so it never blocks.
        # fd-dups are deliberately NOT sanitized and reach the T3 classifier.
        result_reblock_a = validate_bash_command(
            "git push 2>&1", is_subagent=True, session_id=session_id,
        )
        assert not result_reblock_a.allowed
        reason_a = result_reblock_a.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        id_a = re.search(r"approval_id:\s*(P-[\w-]+)", reason_a).group(1)

        result_reblock_b = validate_bash_command(
            "git push 1>&2", is_subagent=True, session_id=session_id,
        )
        assert not result_reblock_b.allowed
        reason_b = result_reblock_b.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        id_b = re.search(r"approval_id:\s*(P-[\w-]+)", reason_b).group(1)

        assert id_a == id_b, (
            "Fix B: a semantically-equal re-block (different redirect) must REUSE "
            f"the same pending nonce, not mint a new one. got {id_a} vs {id_b}"
        )

    def test_find_pending_in_db_semantic_reuse(self, db_and_store):
        """Direct unit test of Fix B: _find_pending_in_db matches semantically."""
        from modules.tools.bash_validator import _find_pending_in_db, _build_sealed_payload
        import gaia.approvals.store as astore

        db_path, assert_con, store = db_and_store
        session_id = "test-dbl-session"

        # Mint a pending whose stored exact_content carries a redirect.
        payload = _build_sealed_payload(
            "git push 2>&1", verb="push", category="MUTATIVE", agent_type="t",
        )
        approval_id = astore.insert_requested(payload, session_id=session_id)

        # The redirect-stripped form must find the SAME pending semantically.
        found = _find_pending_in_db(session_id, "git push")
        assert found == approval_id, (
            f"semantic dedup must reuse pending {approval_id}, got {found}"
        )
        # A byte-exact match still works too.
        assert _find_pending_in_db(session_id, "git push 2>&1") == approval_id

        # A genuinely different command must NOT match.
        assert _find_pending_in_db(session_id, "git pull") is None


# ---------------------------------------------------------------------------
# Keep-path policy: -C <path> binds to the signature.
# ---------------------------------------------------------------------------

class TestChdirPathPolicy:
    def test_same_chdir_path_matches(self):
        from modules.security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
            matches_approval_signature,
        )

        sig = build_approval_signature(
            "git -C /repo/a push", scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push", danger_category="MUTATIVE",
        )
        # Same path, redirect appended on retry -> must match (path binds, redirect strips).
        assert matches_approval_signature(sig, "git -C /repo/a push 2>&1") is True

    def test_different_chdir_path_does_not_match(self):
        from modules.security.approval_scopes import (
            SCOPE_SEMANTIC_SIGNATURE,
            build_approval_signature,
            matches_approval_signature,
        )

        sig = build_approval_signature(
            "git -C /repo/a push", scope_type=SCOPE_SEMANTIC_SIGNATURE,
            danger_verb="push", danger_category="MUTATIVE",
        )
        # Different -C path is a different intent -> must NOT match (keep-path policy).
        assert matches_approval_signature(sig, "git -C /repo/b push") is False

    def test_chdir_path_find_pending_distinct(self, db_and_store):
        """_find_pending_in_db must keep distinct -C paths separate."""
        from modules.tools.bash_validator import _find_pending_in_db, _build_sealed_payload
        import gaia.approvals.store as astore

        db_path, assert_con, store = db_and_store
        session_id = "test-dbl-session"

        payload = _build_sealed_payload(
            "git -C /repo/a push", verb="push", category="MUTATIVE", agent_type="t",
        )
        approval_id = astore.insert_requested(payload, session_id=session_id)

        # Same path (with a redirect) reuses; different path does not.
        assert _find_pending_in_db(session_id, "git -C /repo/a push 2>&1") == approval_id
        assert _find_pending_in_db(session_id, "git -C /repo/b push") is None
