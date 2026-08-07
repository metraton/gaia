#!/usr/bin/env python3
"""End-to-end approval relay tests for nonce-based T3 execution.

These tests exercise the real PreToolUse path (adapt_pre_tool_use, subagent
context) across:
  1. Bash T3 block -> deny with approval_id, pending approval persisted
  2. Grant activation (activate_db_pending_by_prefix) -> pending becomes grant
  3. Bash retry -> allowed only for the same approved command scope

They read the DB pending plane (gaia.approvals.store.get_pending) as the
deterministic source of nonce state instead of relying only on parsing
agent text. The filesystem pending plane (write_pending_approval /
activate_pending_approval / get_latest_pending_approval) was retired; these
tests now seed via tests.fixtures.db_helpers.seed_db_pending and activate
via approval_grants.activate_db_pending_by_prefix.
"""

import hashlib
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))


@pytest.fixture
def isolated_nonce_env(tmp_path, monkeypatch):
    """Create an isolated .claude environment for approval relay tests."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(tmp_path)
    # No CLAUDE_SESSION_ID env: the grant cycle is session-agnostic (Brief 71).
    # Every call below passes session_id explicitly, and the grant-match path
    # (activate_db_pending_by_prefix / list_command_set_grants_agnostic) never
    # filters on session, so a session env var would be dead weight here.

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import modules.core.paths as core_paths
    import modules.core.state as core_state
    import modules.security.approval_grants as approval_grants
    from tests.fixtures import pretool_adapter

    core_paths.clear_path_cache()
    approval_grants._grants_dir_created = False
    approval_grants._last_cleanup_time = 0.0

    monkeypatch.setattr(core_state, "find_claude_dir", lambda: claude_dir)
    monkeypatch.setattr(approval_grants, "get_plugin_data_dir", lambda: claude_dir)

    # Isolate the DB pending plane. The DB-backed pending functions
    # (insert_requested / get_pending / activate_db_pending_by_prefix) delegate
    # to gaia.store.writer._connect(); patch it to a per-test SQLite file so the
    # approvals + approval_grants tables are empty and isolated.
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
                revoked_at            TEXT,
                multi_use             INTEGER NOT NULL DEFAULT 0,
                confirmed             INTEGER NOT NULL DEFAULT 0,
                request_fingerprint   TEXT,
                next_index            INTEGER NOT NULL DEFAULT 0,
                reservation_index     INTEGER,
                reservation_session_id TEXT,
                reservation_tool_use_id TEXT,
                reservation_at        TEXT,
                failed_index          INTEGER,
                failure_reason        TEXT,
                source                TEXT NOT NULL DEFAULT 'legacy'
            );
            """
        )
        from tests.fixtures.db_helpers import apply_approvals_schema
        apply_approvals_schema(con)
        con.commit()
        return con

    import gaia.store.writer as _swriter
    monkeypatch.setattr(_swriter, "_connect", lambda db_path_arg=None: _make_writer_db())

    core_state.clear_hook_state()

    return {
        "claude_dir": claude_dir,
        "pretool_adapter": pretool_adapter,
        "core_state": core_state,
        "approval_grants": approval_grants,
    }


def _permission_reason(result: dict) -> str:
    return result["hookSpecificOutput"]["permissionDecisionReason"]


def _has_pending() -> bool:
    """DB oracle: True if any pending approval row exists.

    Replaces the retired filesystem oracle get_latest_pending_approval();
    reads the DB pending plane directly.
    """
    from gaia.approvals.store import get_pending
    return len(get_pending(all_sessions=True)) > 0


class TestNonceApprovalRelayE2E:
    """T3 approval cycle tests on the real subagent lane.

    A subagent's blocked T3 produces 'deny' carrying an approval_id, and the
    hook itself persists the pending approval; activation
    (activate_db_pending_by_prefix, as the ElicitationResult /
    UserPromptSubmit flow does) turns it into a grant the byte-identical
    retry consumes.
    """

    SESSION = "e2e-relay-session"

    def test_same_command_can_retry_after_grant_activation(self, isolated_nonce_env):
        """Subagent T3 gets 'deny' + pending; retry passes after activation.

        Note: git commit removed from MUTATIVE_VERBS in v5; uses git push instead.
        """
        adapter_fx = isolated_nonce_env["pretool_adapter"]
        core_state = isolated_nonce_env["core_state"]
        approval_grants = isolated_nonce_env["approval_grants"]

        command = "git push origin feat/relay"

        block = adapter_fx.compat_shape(
            adapter_fx.run_subagent_bash(command, session_id=self.SESSION)
        )
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "deny"

        # The hook itself persisted the pending approval for this command.
        from gaia.approvals.store import get_pending
        pending = get_pending(all_sessions=True)
        assert pending, "subagent T3 deny must persist a pending approval"
        approval_id = pending[-1]["id"]
        assert approval_id.startswith("P-")

        # Activation is keyed by the nonce prefix AFTER the 'P-' marker
        # (activate_db_pending_by_prefix matches id LIKE 'P-<prefix>%').
        activation = approval_grants.activate_db_pending_by_prefix(
            approval_id[2:10], current_session_id=self.SESSION,
        )
        assert activation.success, f"Activation should succeed: {activation.reason}"
        assert not _has_pending()

        # After grant activation, the byte-identical retry is auto-allowed.
        # The real subagent lane allows WITH updatedInput: the dispatch
        # identity (GAIA_DISPATCH_AGENT) is stamped onto the command's env.
        retry = adapter_fx.compat_shape(
            adapter_fx.run_subagent_bash(command, session_id=self.SESSION)
        )
        assert isinstance(retry, dict)
        retry_out = retry["hookSpecificOutput"]
        assert retry_out["permissionDecision"] == "allow"
        effective = retry_out.get("updatedInput", {}).get("command", "")
        assert effective.endswith(command)

        retry_state = core_state.get_hook_state()
        assert retry_state is not None
        assert retry_state.command.endswith(command)

    def test_approved_nonce_does_not_bleed_into_different_command(self, isolated_nonce_env):
        """Grant for one command does not cover a different command."""
        adapter_fx = isolated_nonce_env["pretool_adapter"]
        approval_grants = isolated_nonce_env["approval_grants"]

        deploy_cmd = "kubectl apply -f deployment.yaml"
        push_cmd = "git push origin main"

        block = adapter_fx.compat_shape(
            adapter_fx.run_subagent_bash(deploy_cmd, session_id=self.SESSION)
        )
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "deny"

        from gaia.approvals.store import get_pending
        pending = get_pending(all_sessions=True)
        assert pending
        approval_id = pending[-1]["id"]
        activation = approval_grants.activate_db_pending_by_prefix(
            approval_id[2:10], current_session_id=self.SESSION,
        )
        assert activation.success

        # A different command is still denied -- the grant does not bleed.
        push_block = adapter_fx.compat_shape(
            adapter_fx.run_subagent_bash(push_cmd, session_id=self.SESSION)
        )
        assert isinstance(push_block, dict)
        assert push_block["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_compound_with_t3_component_is_refused_without_pending(self, isolated_nonce_env):
        """A compound carrying a T3 component is refused categorically.

        Compound T3 execution is disabled at the validator: the deny is not
        approvable (no pending approval is persisted) and instructs a
        plan-first request-set with one command per Bash call. This pins the
        production semantics that replaced the old approve-the-compound flow.
        """
        adapter_fx = isolated_nonce_env["pretool_adapter"]

        component = "git push origin feat/compound"
        compound = f"ls -la && {component}"

        block = adapter_fx.compat_shape(
            adapter_fx.run_subagent_bash(compound, session_id=self.SESSION)
        )
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = _permission_reason(block)
        assert "request-set" in reason, (
            f"compound T3 refusal should instruct a plan-first request-set: {reason}"
        )

        # No pending approval: the refusal is categorical, not approvable.
        assert not _has_pending()
