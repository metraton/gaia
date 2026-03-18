#!/usr/bin/env python3
"""End-to-end approval relay tests for nonce-based T3 execution.

These tests exercise the real pre_tool_use hook path across:
  1. Bash T3 block -> pending approval persisted
  2. SendMessage with APPROVE:<nonce> -> pending activates to grant
  3. Bash retry -> allowed only for the same approved command scope

They intentionally use get_latest_pending_approval() as the deterministic
source of nonce state instead of relying only on parsing agent text.
"""

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
    monkeypatch.setattr(approval_grants, "find_claude_dir", lambda: claude_dir)

    core_state.clear_hook_state()

    return {
        "claude_dir": claude_dir,
        "pre_tool_use": pre_tool_use,
        "core_state": core_state,
        "approval_grants": approval_grants,
    }


def _permission_reason(result: dict) -> str:
    return result["hookSpecificOutput"]["permissionDecisionReason"]


class TestNonceApprovalRelayE2E:
    """The nonce relay should work across Bash block, SendMessage resume, and retry."""

    def test_same_command_can_retry_after_nonce_resume(self, isolated_nonce_env):
        pre_tool_use = isolated_nonce_env["pre_tool_use"]
        core_state = isolated_nonce_env["core_state"]
        approval_grants = isolated_nonce_env["approval_grants"]

        command = 'git commit -m "feat(auth): add relay coverage"'

        block = pre_tool_use.pre_tool_use_hook("Bash", {"command": command})
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "deny"

        pending = approval_grants.get_latest_pending_approval()
        assert pending is not None
        assert pending["command"] == command
        assert f"NONCE:{pending['nonce']}" in _permission_reason(block)
        assert core_state.get_hook_state() is None

        resume = pre_tool_use.pre_tool_use_hook(
            "SendMessage",
            {"to": "a12345", "message": f"APPROVE:{pending['nonce']}\n\nRetry the approved commit."},
        )
        assert resume is None
        assert approval_grants.get_latest_pending_approval() is None

        resume_state = core_state.get_hook_state()
        assert resume_state is not None
        assert resume_state.command == "SendMessage:a12345"
        assert resume_state.metadata["has_approval"] is True

        # First retry returns "ask" (double-barrier: native dialog confirmation)
        ask_result = pre_tool_use.pre_tool_use_hook("Bash", {"command": command})
        assert isinstance(ask_result, dict)
        assert ask_result["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert "Confirm execution" in ask_result["hookSpecificOutput"]["permissionDecisionReason"]

        # Simulate post_tool_use confirming the grant after native dialog accepts.
        # In production, post_tool_use.py fires after the command executes and
        # confirms the grant. The bash_validator no longer confirms inline.
        approval_grants.confirm_grant(command)

        # After grant is confirmed, subsequent retries are auto-allowed
        retry = pre_tool_use.pre_tool_use_hook("Bash", {"command": command})
        assert retry is None

        retry_state = core_state.get_hook_state()
        assert retry_state is not None
        assert retry_state.command == command

    def test_approved_nonce_does_not_bleed_into_different_command(self, isolated_nonce_env):
        pre_tool_use = isolated_nonce_env["pre_tool_use"]
        approval_grants = isolated_nonce_env["approval_grants"]

        commit_cmd = 'git commit -m "feat(auth): scoped approval"'
        push_cmd = "git push origin main"

        block = pre_tool_use.pre_tool_use_hook("Bash", {"command": commit_cmd})
        assert isinstance(block, dict)

        pending = approval_grants.get_latest_pending_approval()
        assert pending is not None

        resume = pre_tool_use.pre_tool_use_hook(
            "SendMessage",
            {"to": "a12345", "message": f"APPROVE:{pending['nonce']}"},
        )
        assert resume is None

        push_block = pre_tool_use.pre_tool_use_hook("Bash", {"command": push_cmd})
        assert isinstance(push_block, dict)
        assert push_block["hookSpecificOutput"]["permissionDecision"] == "deny"

        latest = approval_grants.get_latest_pending_approval()
        assert latest is not None
        assert latest["command"] == push_cmd
        assert f"NONCE:{latest['nonce']}" in _permission_reason(push_block)

    def test_compound_command_reuses_component_nonce_on_retry(self, isolated_nonce_env):
        pre_tool_use = isolated_nonce_env["pre_tool_use"]
        approval_grants = isolated_nonce_env["approval_grants"]

        compound = "ls -la && terraform apply"

        block = pre_tool_use.pre_tool_use_hook("Bash", {"command": compound})
        assert isinstance(block, dict)
        assert block["hookSpecificOutput"]["permissionDecision"] == "deny"

        pending = approval_grants.get_latest_pending_approval()
        assert pending is not None
        assert pending["command"] == "terraform apply"
        assert f"NONCE:{pending['nonce']}" in _permission_reason(block)

        resume = pre_tool_use.pre_tool_use_hook(
            "SendMessage",
            {"to": "a12345", "message": f"APPROVE:{pending['nonce']}\n\nRetry the exact compound command."},
        )
        assert resume is None

        # First retry returns "ask" (double-barrier: native dialog confirmation)
        ask_result = pre_tool_use.pre_tool_use_hook("Bash", {"command": compound})
        assert isinstance(ask_result, dict)
        assert ask_result["hookSpecificOutput"]["permissionDecision"] == "ask"

        # Simulate post_tool_use confirming the grant after native dialog accepts
        approval_grants.confirm_grant("terraform apply")

        # After grant is confirmed, subsequent retries are auto-allowed
        retry = pre_tool_use.pre_tool_use_hook("Bash", {"command": compound})
        assert retry is None
