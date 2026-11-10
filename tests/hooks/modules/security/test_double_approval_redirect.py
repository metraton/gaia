#!/usr/bin/env python3
"""Redirect variants share a semantic signature, yet a grant and a pending serve only the bytes they sealed.

``tokenize_command`` drops output redirects, so `git push` and `git push 2>&1`
classify alike and `-C <path>` stays part of the signature. Authority is not
decided there: the grant lookup and the pending reuse both compare bytes.
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
    from gaia.store import writer

    con.executescript(writer._SCHEMA_PATH.read_text(encoding="utf-8"))
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

    # Same file as db_and_store: atomic activation writes the grant through the
    # approvals connection, so the grant plane cannot live in a separate file.
    writer_db_path = tmp_path / "test_dbl.db"

    def _make_writer_db() -> sqlite3.Connection:
        import gaia.store.writer as writer

        is_new = not writer_db_path.exists()
        con = sqlite3.connect(str(writer_db_path))
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.create_function(
            "gaia_sha256", 1, lambda v: _sha256(v), deterministic=True,
        )
        if is_new:
            con.executescript(writer._SCHEMA_PATH.read_text(encoding="utf-8"))
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


def _blocked_id(command: str, session_id: str) -> str:
    """Validate ``command`` as a subagent, assert it is denied, and return the approval_id it names."""
    import re

    from modules.tools.bash_validator import validate_bash_command

    result = validate_bash_command(
        command, is_subagent=True, agent_type="gaia-system", session_id=session_id,
    )
    assert not result.allowed, result.reason
    reason = result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
    return re.search(r"approval_id:\s*(P-[\w-]+)", reason).group(1)


class TestSignedBytesOnly:
    def test_only_the_blocked_bytes_consume_the_grant(self, db_and_store, monkeypatch):
        import modules.tools.bash_validator as bash_validator
        from modules.tools.bash_validator import validate_bash_command
        from modules.security.approval_grants import activate_db_pending_by_id
        from modules.security.comment_push_gate import GateVerdict

        # The comment gate measures the checkout under the cwd; this test is about grants.
        monkeypatch.setattr(bash_validator, "check_comment_push", lambda *a, **k: GateVerdict(True))
        session_id = "test-dbl-session"
        signed = "git push 2>&1"
        approval_id = _blocked_id(signed, session_id)
        assert activate_db_pending_by_id(approval_id, current_session_id=session_id).success

        other_spelling = _blocked_id("git push", session_id)
        retry = validate_bash_command(
            signed, is_subagent=True, agent_type="gaia-system", session_id=session_id,
        )
        replay = _blocked_id(signed, session_id)

        assert other_spelling != approval_id
        assert retry.allowed and retry.consumed_approval_id == approval_id
        assert replay not in (approval_id, other_spelling)
        assert _blocked_id("git push 1>&2", session_id) != replay
        assert _blocked_id(signed, session_id) == replay

    def test_find_pending_in_db_names_only_the_sealed_bytes(self, db_and_store):
        from modules.tools.bash_validator import _find_pending_in_db, _build_sealed_payload
        import gaia.approvals.store as astore

        session_id = "test-dbl-session"
        payload = _build_sealed_payload(
            "git push 2>&1", verb="push", category="MUTATIVE", agent_type="t",
            session_id=session_id,
        )
        approval_id = astore.insert_requested(payload, session_id=session_id)

        assert _find_pending_in_db(session_id, "git push 2>&1", "t") == approval_id
        assert _find_pending_in_db(session_id, "git push", "t") is None
        assert _find_pending_in_db(session_id, "git pull", "t") is None


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
            session_id=session_id,
        )
        approval_id = astore.insert_requested(payload, session_id=session_id)

        assert _find_pending_in_db(session_id, "git -C /repo/a push", "t") == approval_id
        assert _find_pending_in_db(session_id, "git -C /repo/b push", "t") is None
