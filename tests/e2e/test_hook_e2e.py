"""
E2E subprocess tests for gaia-ops hooks.

These tests run the actual hook scripts (pre_tool_use.py, post_tool_use.py)
as subprocesses, piping JSON on stdin and asserting exit codes + stdout JSON.

This validates the FULL hook lifecycle: stdin JSON -> adapter parse -> business
logic -> adapter format -> stdout JSON + exit code.

Run: python3 -m pytest tests/e2e/test_hook_e2e.py -v
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Import fixtures
from tests.e2e.fixtures import (
    MALFORMED_MISSING_EVENT_NAME,
    MALFORMED_UNKNOWN_EVENT,
    POSTTOOL_BASH,
    POSTTOOL_BASH_FAILED,
    PRETOOL_AGENT,
    PRETOOL_AGENT_DEVOPS,
    PRETOOL_BASH_BLOCKED,
    PRETOOL_BASH_BLOCKED_GIT_RESET_HARD,
    PRETOOL_BASH_BLOCKED_TERRAFORM_DESTROY,
    PRETOOL_BASH_MUTATIVE,
    PRETOOL_BASH_MUTATIVE_KUBECTL_APPLY,
    PRETOOL_BASH_SAFE,
    PRETOOL_BASH_SAFE_CAT,
    PRETOOL_BASH_SAFE_GIT_STATUS,
    PRETOOL_BASH_SAFE_LS,
    PRETOOL_READ,
    STOP_EVENT,
    STOP_EVENT_WITH_REASON,
    SUBAGENT_START,
    SUBAGENT_START_DEVOPS,
    TASK_COMPLETED,
    TASK_COMPLETED_WITH_OUTPUT,
)

# Worktree root where hooks live
WORKTREE = Path(__file__).resolve().parents[2]
HOOKS_DIR = WORKTREE / "hooks"


def run_hook(script_name, stdin_payload, env_extras=None):
    """Run a hook script as subprocess and return (exit_code, stdout_json, stderr).

    Args:
        script_name: Relative path from hooks dir (e.g. "pre_tool_use.py").
        stdin_payload: Dict to serialize as JSON on stdin.
        env_extras: Optional dict of extra environment variables.

    Returns:
        Tuple of (exit_code, parsed_stdout_json_or_None, stderr_text).
    """
    script_path = HOOKS_DIR / script_name
    assert script_path.exists(), f"Hook script not found: {script_path}"

    env = os.environ.copy()
    # Isolate hook subprocess from the host environment so tests are
    # deterministic regardless of where they run:
    # - CLAUDE_PLUGIN_ROOT: would activate plugin-dir mode detection
    # - ORCHESTRATOR_DELEGATE_MODE: would block Bash before security
    #   checks run (the disk fallback reads settings.json, so we must
    #   explicitly set "false" rather than just popping the var)
    # - GAIA_PLUGIN_MODE: force "ops" so the adapter uses nonce-deny
    #   flow (the tests assert permissionDecision: deny for T3 commands)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    env["ORCHESTRATOR_DELEGATE_MODE"] = "false"
    env["GAIA_PLUGIN_MODE"] = "ops"
    if env_extras:
        env.update(env_extras)

    result = subprocess.run(
        [sys.executable, str(script_path)],
        input=json.dumps(stdin_payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        cwd=str(WORKTREE),
    )

    stdout_json = None
    if result.stdout.strip():
        # The hook may print multiple lines; the JSON response is the last line
        for line in reversed(result.stdout.strip().split("\n")):
            try:
                stdout_json = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    return result.returncode, stdout_json, result.stderr


# ============================================================================
# PreToolUse E2E -- Safe commands
# ============================================================================


class TestPreToolUseSafe:
    """Safe (T0) commands should exit 0 with no blocking response."""

    HOOK = "pre_tool_use.py"

    def test_kubectl_get_allowed(self):
        """kubectl get pods is read-only, should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_SAFE)
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"

    def test_ls_allowed(self):
        """ls -la is read-only, should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_SAFE_LS)
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"

    def test_git_status_allowed(self):
        """git status is read-only, should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_SAFE_GIT_STATUS)
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"

    def test_cat_allowed(self):
        """cat is read-only, should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_SAFE_CAT)
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"


# ============================================================================
# PreToolUse E2E -- Mutative commands (T3 ask, exit 0)
# ============================================================================


class TestPreToolUseMutative:
    """Mutative (T3) commands should exit 0 with permissionDecision: ask.

    The hook uses Claude Code's native 'ask' dialog for T3 commands so the
    user sees the confirmation prompt.  This replaced the older nonce-deny
    flow.  Permanently blocked commands (rm -rf, etc.) still get 'deny'.
    """

    HOOK = "pre_tool_use.py"

    def test_git_commit_ask(self):
        """git commit is mutative, should trigger native ask dialog."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_MUTATIVE)
        assert code == 0, f"Expected exit 0 (ask), got {code}. stderr: {stderr}"
        assert response is not None, "Expected JSON response for mutative ask"
        hook_output = response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", (
            f"Expected ask, got: {hook_output.get('permissionDecision')}"
        )

    def test_kubectl_apply_ask(self):
        """kubectl apply is mutative, should trigger native ask dialog."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_MUTATIVE_KUBECTL_APPLY)
        assert code == 0, f"Expected exit 0 (ask), got {code}. stderr: {stderr}"
        assert response is not None, "Expected JSON response for mutative ask"
        hook_output = response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "ask", (
            f"Expected ask, got: {hook_output.get('permissionDecision')}"
        )


# ============================================================================
# PreToolUse E2E -- Blocked commands (permanently denied, exit 2)
# ============================================================================


class TestPreToolUseBlocked:
    """Blocked commands should exit 2 (permanent block)."""

    HOOK = "pre_tool_use.py"

    def test_rm_rf_root_blocked(self):
        """rm -rf / is permanently blocked (exit 2)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_BLOCKED)
        assert code == 2, f"Expected exit 2 (permanent block), got {code}. stderr: {stderr}"

    def test_terraform_destroy_blocked(self):
        """terraform destroy (no -target) is permanently blocked (exit 2)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_BLOCKED_TERRAFORM_DESTROY)
        assert code == 2, f"Expected exit 2 (permanent block), got {code}. stderr: {stderr}"

    def test_git_reset_hard_blocked(self):
        """git reset --hard is permanently blocked (exit 2)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_BLOCKED_GIT_RESET_HARD)
        assert code == 2, f"Expected exit 2 (permanent block), got {code}. stderr: {stderr}"


# ============================================================================
# PreToolUse E2E -- Agent/Task tools
# ============================================================================


class TestPreToolUseAgent:
    """Agent tool invocations should be allowed (exit 0)."""

    HOOK = "pre_tool_use.py"

    def test_valid_agent_allowed(self):
        """Known project agent should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_AGENT)
        assert code == 0, f"Expected exit 0 for valid agent, got {code}. stderr: {stderr}"

    def test_devops_agent_allowed(self):
        """devops-developer agent should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_AGENT_DEVOPS)
        assert code == 0, f"Expected exit 0 for devops agent, got {code}. stderr: {stderr}"


# ============================================================================
# PreToolUse E2E -- Pass-through tools
# ============================================================================


class TestPreToolUsePassthrough:
    """Non-Bash, non-Agent tools should pass through (exit 0)."""

    HOOK = "pre_tool_use.py"

    def test_read_tool_passthrough(self):
        """Read tool should pass through without validation (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_READ)
        assert code == 0, f"Expected exit 0 for passthrough, got {code}. stderr: {stderr}"


# ============================================================================
# PostToolUse E2E
# ============================================================================


class TestPostToolUseE2E:
    """PostToolUse hook should process results without errors."""

    HOOK = "post_tool_use.py"

    def test_successful_command(self):
        """Successful command result should exit 0."""
        code, response, stderr = run_hook(self.HOOK, POSTTOOL_BASH)
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"

    def test_failed_command(self):
        """Failed command result should still exit 0 (post-hook never blocks)."""
        code, response, stderr = run_hook(self.HOOK, POSTTOOL_BASH_FAILED)
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"


# ============================================================================
# Error handling E2E
# ============================================================================


class TestErrorHandlingE2E:
    """Test error handling for malformed inputs."""

    HOOK = "pre_tool_use.py"

    def test_malformed_json_exits_nonzero(self):
        """Non-JSON stdin should cause a non-zero exit."""
        script_path = HOOKS_DIR / self.HOOK
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input="this is not json at all {{{",
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(WORKTREE),
        )
        assert result.returncode != 0, "Expected non-zero exit for malformed JSON"

    def test_missing_event_name_exits_nonzero(self):
        """Missing hook_event_name should cause exit 1."""
        code, response, stderr = run_hook(self.HOOK, MALFORMED_MISSING_EVENT_NAME)
        assert code == 1, f"Expected exit 1 for missing event name, got {code}"

    def test_unknown_event_exits_nonzero(self):
        """Unknown hook event type should cause exit 1."""
        code, response, stderr = run_hook(self.HOOK, MALFORMED_UNKNOWN_EVENT)
        assert code == 1, f"Expected exit 1 for unknown event, got {code}"

    def test_empty_stdin_exits_nonzero(self):
        """Empty stdin should cause a non-zero exit."""
        script_path = HOOKS_DIR / self.HOOK
        result = subprocess.run(
            [sys.executable, str(script_path)],
            input="",
            capture_output=True,
            text=True,
            timeout=15,
            cwd=str(WORKTREE),
        )
        # Empty stdin: has_stdin_data returns False when stdin is empty string,
        # so the hook falls through to "no args and no stdin" -> exit 1
        assert result.returncode != 0, "Expected non-zero exit for empty stdin"


# ============================================================================
# Plugin channel detection E2E
# ============================================================================


class TestPluginChannelE2E:
    """Test plugin channel detection via CLAUDE_PLUGIN_ROOT env var."""

    HOOK = "pre_tool_use.py"

    def test_plugin_channel_safe_command(self):
        """Safe command should still be allowed with CLAUDE_PLUGIN_ROOT set."""
        code, response, stderr = run_hook(
            self.HOOK,
            PRETOOL_BASH_SAFE_LS,
            env_extras={"CLAUDE_PLUGIN_ROOT": str(WORKTREE)},
        )
        assert code == 0, f"Expected exit 0 with plugin channel, got {code}. stderr: {stderr}"

    def test_plugin_channel_blocked_command(self):
        """Blocked command should still be blocked with CLAUDE_PLUGIN_ROOT set."""
        code, response, stderr = run_hook(
            self.HOOK,
            PRETOOL_BASH_BLOCKED,
            env_extras={"CLAUDE_PLUGIN_ROOT": str(WORKTREE)},
        )
        assert code == 2, f"Expected exit 2 with plugin channel, got {code}. stderr: {stderr}"


def _hook_script_is_nonempty(script_name: str) -> bool:
    """Check if a hook script exists and has content (not just a 0-byte stub)."""
    script_path = HOOKS_DIR / script_name
    return script_path.exists() and script_path.stat().st_size > 0


# ============================================================================
# P2: Stop E2E
# ============================================================================


class TestStopE2E:
    """Stop hook runs and exits 0."""

    HOOK = "stop_hook.py"

    def test_stop_event_runs(self):
        """Stop hook exits 0."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, STOP_EVENT)
        assert code == 0, (
            f"Expected exit 0 for Stop hook, got {code}. stderr: {stderr}"
        )

    def test_stop_event_with_reason_runs(self):
        """Stop hook with stop_reason exits 0."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, STOP_EVENT_WITH_REASON)
        assert code == 0, (
            f"Expected exit 0 for Stop hook, got {code}. stderr: {stderr}"
        )


# ============================================================================
# P2: TaskCompleted E2E
# ============================================================================


class TestTaskCompletedE2E:
    """TaskCompleted hook runs and exits 0."""

    HOOK = "task_completed.py"

    def test_task_completed_runs(self):
        """TaskCompleted hook exits 0."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, TASK_COMPLETED)
        assert code == 0, (
            f"Expected exit 0 for TaskCompleted hook, got {code}. stderr: {stderr}"
        )

    def test_task_completed_with_output_runs(self):
        """TaskCompleted with output exits 0."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, TASK_COMPLETED_WITH_OUTPUT)
        assert code == 0, (
            f"Expected exit 0 for TaskCompleted hook, got {code}. stderr: {stderr}"
        )


# ============================================================================
# P2: SubagentStart E2E
# ============================================================================


class TestSubagentStartE2E:
    """SubagentStart hook runs and exits 0."""

    HOOK = "subagent_start.py"

    def test_subagent_start_runs(self):
        """SubagentStart hook exits 0."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, SUBAGENT_START)
        assert code == 0, (
            f"Expected exit 0 for SubagentStart hook, got {code}. stderr: {stderr}"
        )

    def test_subagent_start_devops_runs(self):
        """SubagentStart for devops-developer exits 0."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, SUBAGENT_START_DEVOPS)
        assert code == 0, (
            f"Expected exit 0 for SubagentStart hook, got {code}. stderr: {stderr}"
        )
