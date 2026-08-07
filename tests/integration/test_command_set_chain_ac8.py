#!/usr/bin/env python3
"""AC-8 (brief: endurecimiento-de-tests-del-security-core).

Chains of T3 sub-commands joined by ``&&`` / ``;`` must be covered by ONE
COMMAND_SET pending approval, not re-blocked sub-command by sub-command.

The gap this guards against
---------------------------
Before the fix, a subagent running ``cmd1 && cmd2 && cmd3`` where each
sub-command is T3 hit a double-approval: _validate_compound_command iterated
the components, the FIRST ungranted T3 minted a single-signature pending and
short-circuited, so one approval covered only ``cmd1``; ``cmd2`` re-blocked
with a fresh single pending and the user had to approve again.

The fix (bash_validator)
------------------------
_validate_compound_command now collects per-component results first. When >= 2
sub-commands are ungranted T3 (and none is hard-blocked), it mints ONE
COMMAND_SET pending over exactly those T3 sub-commands via
decide_t3_outcome(command_set=...). One approval covers the chain; each
sub-command is still consumed byte-for-byte by its own signature at retry --
no consent is widened, the commands are only grouped under one approval_id.

Controls
--------
  * a single T3 sub-command in a chain keeps the singular semantic-signature
    pending (no command_set key);
  * a chain with no T3 sub-command mints no pending and is allowed.

A subagent context (is_subagent=True) routes to deny + approval_id (Gaia flow).
"""

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(REPO_ROOT))

from modules.tools.bash_validator import validate_bash_command  # noqa: E402

from tests.fixtures.db_helpers import apply_approvals_schema  # noqa: E402


SESSION = "ac8-chain-session"


# ---------------------------------------------------------------------------
# Isolated DB fixture: one temp SQLite file carrying BOTH the approvals /
# approval_events tables (the pending plane) AND approval_grants (the grant
# plane), wired into gaia.store.writer._connect so the full intake ->
# activation -> consume cycle runs against the test-local DB.
# ---------------------------------------------------------------------------

@pytest.fixture()
def chain_db(tmp_path, monkeypatch):
    db_file = tmp_path / "ac8_chain.db"

    def _make_con(db_path_arg=None):
        con = sqlite3.connect(str(db_file))
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
                revoked_at            TEXT,
                multi_use             INTEGER NOT NULL DEFAULT 0,
                confirmed             INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        apply_approvals_schema(con)
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", _make_con)
    # gaia.approvals.store._open_db delegates to writer._connect, but it also
    # has its own _open_db; patch get_pending to honour the isolated con too.
    import gaia.approvals.store as astore
    monkeypatch.setattr(astore, "_open_db", _make_con)
    _orig_get_pending = astore.get_pending

    def _patched_get_pending(session_id=None, all_sessions=False, con=None):
        if con is None:
            con = _make_con()
        return _orig_get_pending(
            session_id=session_id, all_sessions=all_sessions, con=con
        )

    monkeypatch.setattr(astore, "get_pending", _patched_get_pending)

    return _make_con


def _hook_output(result):
    return result.block_response["hookSpecificOutput"]


def _pending_rows(make_con):
    con = make_con()
    try:
        return [
            dict(r)
            for r in con.execute(
                "SELECT id, payload_json FROM approvals WHERE status = 'pending'"
            ).fetchall()
        ]
    finally:
        con.close()


# ===========================================================================
# AC-8 core: a chain of 2 T3 sub-commands mints ONE COMMAND_SET pending.
# ===========================================================================

class TestLegacyCompoundCommandSetDisabled:
    """Compound T3 text is denied without minting any approval."""

    CHAIN = "git push origin main && docker push registry/app:1.0"

    def test_two_t3_chain_denies_with_single_approval_id(self, chain_db):
        result = validate_bash_command(
            self.CHAIN, is_subagent=True, session_id=SESSION,
        )
        assert not result.allowed
        assert result.block_response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "Compound T3 execution is disabled" in result.reason

    def test_two_t3_chain_persists_exactly_one_command_set_pending(self, chain_db):
        result = validate_bash_command(
            self.CHAIN, is_subagent=True, session_id=SESSION,
        )
        assert not result.allowed

        assert _pending_rows(chain_db) == []

    def test_semicolon_chain_also_groups_into_command_set(self, chain_db):
        result = validate_bash_command(
            "git push origin main ; docker push registry/app:2.0",
            is_subagent=True,
            session_id=SESSION,
        )
        assert not result.allowed
        assert _pending_rows(chain_db) == []


# ===========================================================================
# AC-8 end-to-end: one approval of the COMMAND_SET pending covers BOTH
# sub-commands on retry (each consumed by its own byte-for-byte signature).
# ===========================================================================

class TestCompoundCannotEnterApprovalLifecycle:

    def test_one_approval_then_both_subcommands_allowed(self, chain_db):
        chain = "git push origin main && docker push registry/app:1.0"
        result = validate_bash_command(chain, is_subagent=True, session_id=SESSION)
        assert not result.allowed
        retry = validate_bash_command(chain, is_subagent=True, session_id=SESSION)
        assert not retry.allowed
        assert _pending_rows(chain_db) == []


# ===========================================================================
# Controls: single T3 in a chain, and no T3 at all -- behaviour unchanged.
# ===========================================================================

class TestControlsUnchanged:
    def test_single_t3_in_chain_is_categorical_deny(self, chain_db):
        # Only the second component is T3 (echo is safe).
        result = validate_bash_command(
            "echo starting && git push origin main",
            is_subagent=True,
            session_id=SESSION,
        )
        assert not result.allowed
        assert result.block_response["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert _pending_rows(chain_db) == []

    def test_chain_with_no_t3_is_allowed_and_mints_no_pending(self, chain_db):
        result = validate_bash_command(
            "echo hello && ls -la",
            is_subagent=True,
            session_id=SESSION,
        )
        assert result.allowed, f"safe chain must be allowed: {result.reason}"
        assert _pending_rows(chain_db) == [], "no T3 -> no pending approval"

    def test_single_standalone_t3_unchanged(self, chain_db):
        # Not a chain: the plain single-command path is untouched.
        result = validate_bash_command(
            "git push origin main", is_subagent=True, session_id=SESSION,
        )
        assert not result.allowed
        rows = _pending_rows(chain_db)
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload.get("command_set") is None
