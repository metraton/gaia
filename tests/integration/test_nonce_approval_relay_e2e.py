#!/usr/bin/env python3
"""End-to-end approval relay tests for nonce-based T3 execution.

These tests exercise the real pre_tool_use hook path across:
  1. Bash T3 block -> pending approval persisted
  2. SendMessage with APPROVE:<nonce> -> pending activates to grant
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
    monkeypatch.setenv("CLAUDE_SESSION_ID", "e2e-relay-session")

    import modules.core.paths as core_paths
    import modules.core.state as core_state
    import modules.security.approval_grants as approval_grants
    import pre_tool_use

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
                confirmed             INTEGER NOT NULL DEFAULT 0
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
        "pre_tool_use": pre_tool_use,
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
    """T3 approval cycle tests using the new flow.

    The bash_validator now returns 'ask' for orchestrator T3 commands and
    'deny' for subagent T3 commands. The nonce relay via SendMessage was
    removed -- grants are activated by the UserPromptSubmit hook.

    These tests exercise the direct grant management APIs to verify the
    full deny -> activate -> retry cycle.
    """

    def test_same_command_can_retry_after_grant_activation(self, isolated_nonce_env):
        """T3 command gets 'ask' from orchestrator; grant passthrough works after activation.

        Note: git commit removed from MUTATIVE_VERBS in v5; uses git push instead.
        """
        pre_tool_use = isolated_nonce_env["pre_tool_use"]
        core_state = isolated_nonce_env["core_state"]
        approval_grants = isolated_nonce_env["approval_grants"]

        command = "git push origin feat/relay"

        # T3 command returns "ask" (orchestrator context, no agent_id)
        block = pre_tool_use.pre_tool_use_hook("Bash", {"command": command})
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "ask"

        # No pending approval is created by the hook in orchestrator mode
        assert not _has_pending()

        # Manually create a DB pending approval and activate it (simulates subagent flow)
        from tests.fixtures.db_helpers import seed_db_pending
        nonce = approval_grants.generate_nonce()
        seed_db_pending(
            command=command,
            session_id="e2e-relay-session",
            danger_verb="push",
            danger_category="MUTATIVE",
            nonce=nonce,
        )
        activation = approval_grants.activate_db_pending_by_prefix(
            nonce[:8], current_session_id="e2e-relay-session",
        )
        assert activation.success, f"Activation should succeed: {activation.reason}"
        assert not _has_pending()

        # After grant activation, retry is auto-allowed (passthrough)
        retry = pre_tool_use.pre_tool_use_hook("Bash", {"command": command})
        assert retry is None

        retry_state = core_state.get_hook_state()
        assert retry_state is not None
        assert retry_state.command == command

    def test_approved_nonce_does_not_bleed_into_different_command(self, isolated_nonce_env):
        """Grant for one command does not cover a different command."""
        pre_tool_use = isolated_nonce_env["pre_tool_use"]
        approval_grants = isolated_nonce_env["approval_grants"]

        deploy_cmd = "kubectl apply -f deployment.yaml"
        push_cmd = "git push origin main"

        # T3 command returns "ask"
        block = pre_tool_use.pre_tool_use_hook("Bash", {"command": deploy_cmd})
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "ask"

        # Create and activate a grant for deploy directly
        from tests.fixtures.db_helpers import seed_db_pending
        nonce = approval_grants.generate_nonce()
        seed_db_pending(
            command=deploy_cmd,
            session_id="e2e-relay-session",
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce,
        )
        activation = approval_grants.activate_db_pending_by_prefix(
            nonce[:8], current_session_id="e2e-relay-session",
        )
        assert activation.success

        # Different command should still be blocked with "ask"
        push_block = pre_tool_use.pre_tool_use_hook("Bash", {"command": push_cmd})
        assert isinstance(push_block, dict)
        assert push_block["hookSpecificOutput"]["permissionDecision"] == "ask"

    def test_compound_command_reuses_component_nonce_on_retry(self, isolated_nonce_env):
        """Compound with T3 component returns 'ask'; grant passthrough works after activation."""
        pre_tool_use = isolated_nonce_env["pre_tool_use"]
        approval_grants = isolated_nonce_env["approval_grants"]

        compound = "ls -la && terraform apply"

        # Compound T3 command returns "ask"
        block = pre_tool_use.pre_tool_use_hook("Bash", {"command": compound})
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "ask"

        # Create and activate a grant for the T3 component directly
        from tests.fixtures.db_helpers import seed_db_pending
        nonce = approval_grants.generate_nonce()
        seed_db_pending(
            command="terraform apply",
            session_id="e2e-relay-session",
            danger_verb="apply",
            danger_category="MUTATIVE",
            nonce=nonce,
        )
        activation = approval_grants.activate_db_pending_by_prefix(
            nonce[:8], current_session_id="e2e-relay-session",
        )
        assert activation.success

        # After grant activation, retry is auto-allowed (passthrough)
        retry = pre_tool_use.pre_tool_use_hook("Bash", {"command": compound})
        assert retry is None
