"""
E2E subprocess tests for gaia hooks.

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
    # - agent_id injected into payload: a subagent under the orchestrator
    #   routes T3 to deny + approval_id (the tests assert permissionDecision:
    #   deny for T3 commands), so we simulate a subagent to test the security
    #   layer directly.
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    if env_extras:
        env.update(env_extras)

    # Inject agent_id into payload so delegate mode treats this as a
    # subagent (allows all tools, letting the security layer be tested).
    if "agent_id" not in stdin_payload:
        stdin_payload = {**stdin_payload, "agent_id": "test-e2e-agent"}

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
    """Mutative (T3) commands from subagents should exit 0 with permissionDecision: deny.

    The e2e test harness injects agent_id into every payload, making the hook
    treat the command as a subagent invocation. Subagents get 'deny' with an
    approval_id so the orchestrator can present the approval to the user.
    Permanently blocked commands (rm -rf, etc.) still get exit 2.
    """

    HOOK = "pre_tool_use.py"

    def test_git_commit_allowed_subagent(self):
        """git commit is no longer mutative (removed from MUTATIVE_VERBS in v5).
        It passes through as safe-by-elimination."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_MUTATIVE)
        # commit is not mutative, so it's allowed (no JSON response)
        assert code == 0, f"Expected exit 0 (allow), got {code}. stderr: {stderr}"
        # No block response means allowed
        assert response is None, (
            f"Expected no response (allowed), got: {response}"
        )

    def test_kubectl_apply_deny_subagent(self):
        """kubectl apply from subagent is mutative, should get deny with approval_id."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_MUTATIVE_KUBECTL_APPLY)
        assert code == 0, f"Expected exit 0 (deny), got {code}. stderr: {stderr}"
        assert response is not None, "Expected JSON response for mutative deny"
        hook_output = response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "deny", (
            f"Expected deny, got: {hook_output.get('permissionDecision')}"
        )
        reason = hook_output.get("permissionDecisionReason", "")
        assert "approval_id:" in reason, (
            f"Expected approval_id in deny reason, got: {reason}"
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

    def test_git_reset_hard_t3_approvable(self):
        """git reset --hard is T3-approvable (exit 0 with deny + approval_id).

        Contract change: git reset --hard moved from BLOCKED to T3-approvable
        as part of the bash_validator AST redesign. It now follows the same
        nonce-based approval flow as other mutative commands -- not a
        permanent block.
        """
        code, response, stderr = run_hook(self.HOOK, PRETOOL_BASH_BLOCKED_GIT_RESET_HARD)
        assert code == 0, (
            f"Expected exit 0 (T3-approvable deny), got {code}. stderr: {stderr}"
        )
        assert response is not None, "Expected JSON response for T3 deny"
        hook_output = response.get("hookSpecificOutput", {})
        assert hook_output.get("permissionDecision") == "deny", (
            f"Expected deny, got: {hook_output.get('permissionDecision')}"
        )
        reason = hook_output.get("permissionDecisionReason", "")
        assert "[T3_BLOCKED]" in reason or "approval" in reason.lower(), (
            f"Expected T3 approval flow in reason, got: {reason}"
        )


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
        """developer agent should be allowed (exit 0)."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_AGENT_DEVOPS)
        assert code == 0, f"Expected exit 0 for devops agent, got {code}. stderr: {stderr}"


# ============================================================================
# PreToolUse E2E -- Pass-through tools
# ============================================================================


class TestPreToolUsePassthrough:
    """Non-Bash, non-Agent tools should pass through (exit 0, no output).

    A bare ``code == 0`` assertion was vacuous: ``adapt_pre_tool_use`` returns
    ``HookResponse(output={}, exit_code=0)`` for an unrecognized tool name,
    and pre_tool_use.py's own dispatch only prints when ``response.output``
    is a NON-EMPTY dict or string (see the ``and response.output`` guard) --
    an empty dict is falsy, so nothing is printed at all and stdout stays
    empty. That is the real observable: a genuine passthrough emits no JSON
    whatsoever, unlike an allow/ask/deny decision which always prints a
    ``hookSpecificOutput`` body. A regression that started attaching a
    permissionDecision to Read (or any other unrecognized tool) would flip
    this from ``None`` to a parsed dict and fail here.
    """

    HOOK = "pre_tool_use.py"

    def test_read_tool_passthrough(self):
        """Read tool passes through: exit 0 and no stdout at all."""
        code, response, stderr = run_hook(self.HOOK, PRETOOL_READ)
        assert code == 0, f"Expected exit 0 for passthrough, got {code}. stderr: {stderr}"
        assert response is None, (
            f"Expected no stdout JSON for a genuine passthrough, got: {response}"
        )


# ============================================================================
# PostToolUse E2E
# ============================================================================


class TestPostToolUseE2E:
    """PostToolUse hook should record the tool's real outcome in the audit log.

    A bare ``exit == 0`` assertion here was vacuous: this hook never blocks
    (see adapt_post_tool_use), so it exits 0 regardless of whether the
    underlying tool call succeeded or failed. The real observable effect is
    the audit record log_execution() writes -- assert on ITS exit_code,
    isolated to a per-test data dir via CLAUDE_PLUGIN_DATA so the assertion
    reads the record this test produced, not ambient audit history.
    """

    HOOK = "post_tool_use.py"

    def _read_last_audit_record(self, data_dir: Path) -> dict:
        """Read the last JSONL record from today's audit log under data_dir."""
        from datetime import datetime, timezone

        log_file = (
            data_dir / "logs" / f"audit-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        )
        assert log_file.exists(), f"Expected audit log at {log_file}"
        lines = [ln for ln in log_file.read_text().splitlines() if ln.strip()]
        assert lines, f"Audit log {log_file} is empty"
        return json.loads(lines[-1])

    def test_successful_command_recorded_with_exit_code_0(self, tmp_path):
        """A successful Bash result (dict tool_response) must audit exit_code 0."""
        code, response, stderr = run_hook(
            self.HOOK, POSTTOOL_BASH, env_extras={"CLAUDE_PLUGIN_DATA": str(tmp_path)}
        )
        assert code == 0, f"Expected exit 0, got {code}. stderr: {stderr}"
        record = self._read_last_audit_record(tmp_path)
        assert record["tool_name"] == "Bash"
        assert record["exit_code"] == 0, (
            f"Expected the audited exit_code to be 0 for a successful command, got {record}"
        )

    def test_failed_command_recorded_with_exit_code_1(self, tmp_path):
        """A failed Bash result (bare-string tool_response) must audit exit_code 1."""
        code, response, stderr = run_hook(
            self.HOOK, POSTTOOL_BASH_FAILED, env_extras={"CLAUDE_PLUGIN_DATA": str(tmp_path)}
        )
        assert code == 0, f"Expected exit 0 (post-hook never blocks), got {code}. stderr: {stderr}"
        record = self._read_last_audit_record(tmp_path)
        assert record["tool_name"] == "Bash"
        assert record["exit_code"] == 1, (
            f"Expected the audited exit_code to be 1 for a failed command, got {record}"
        )


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
    """Stop hook exits 0 AND emits a real quality verdict.

    A bare ``code == 0`` assertion here was vacuous: ``_handle_stop`` calls
    ``sys.exit(0)`` unconditionally (see stop_hook.py), so the exit code can
    never distinguish a working quality assessment from a silently broken
    one. The real observable effect is ``format_quality_response``'s output
    (see adapters/claude_code.py): a dict carrying ``quality_sufficient``,
    ``score``, and ``recommendation``. Asserting on those catches a
    regression that made ``adapt_stop``/``format_quality_response`` raise,
    return the wrong shape, or drop a required key -- exactly the class of
    break the old assertion could never see (mirrors the fix documented on
    TestPostToolUseE2E above).
    """

    HOOK = "stop_hook.py"

    def test_stop_event_runs(self):
        """Stop hook returns a well-formed quality verdict."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, STOP_EVENT)
        assert code == 0, (
            f"Expected exit 0 for Stop hook, got {code}. stderr: {stderr}"
        )
        assert response is not None, f"Expected a JSON quality verdict. stderr: {stderr}"
        assert isinstance(response.get("quality_sufficient"), bool), response
        assert isinstance(response.get("score"), (int, float)), response
        assert isinstance(response.get("recommendation"), str) and response["recommendation"], response

    def test_stop_event_with_reason_runs(self):
        """Stop hook with stop_reason still returns the same verdict shape."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, STOP_EVENT_WITH_REASON)
        assert code == 0, (
            f"Expected exit 0 for Stop hook, got {code}. stderr: {stderr}"
        )
        assert response is not None, f"Expected a JSON quality verdict. stderr: {stderr}"
        assert isinstance(response.get("quality_sufficient"), bool), response
        assert isinstance(response.get("score"), (int, float)), response
        assert isinstance(response.get("recommendation"), str) and response["recommendation"], response


# ============================================================================
# P2: TaskCompleted E2E
# ============================================================================


class TestTaskCompletedE2E:
    """TaskCompleted hook exits 0 AND emits a real verification verdict.

    Same defect class as TestStopE2E: ``_handle_task_completed`` also exits 0
    unconditionally. The observable effect is
    ``format_verification_response``'s output -- ``criteria_met`` and
    ``block_completion`` -- not the exit code.
    """

    HOOK = "task_completed.py"

    def test_task_completed_runs(self):
        """TaskCompleted returns a well-formed verification verdict."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, TASK_COMPLETED)
        assert code == 0, (
            f"Expected exit 0 for TaskCompleted hook, got {code}. stderr: {stderr}"
        )
        assert response is not None, f"Expected a JSON verification verdict. stderr: {stderr}"
        assert isinstance(response.get("criteria_met"), bool), response
        assert isinstance(response.get("block_completion"), bool), response

    def test_task_completed_with_output_runs(self):
        """TaskCompleted with task_output still returns the same verdict shape."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, TASK_COMPLETED_WITH_OUTPUT)
        assert code == 0, (
            f"Expected exit 0 for TaskCompleted hook, got {code}. stderr: {stderr}"
        )
        assert response is not None, f"Expected a JSON verification verdict. stderr: {stderr}"
        assert isinstance(response.get("criteria_met"), bool), response
        assert isinstance(response.get("block_completion"), bool), response


# ============================================================================
# P2: SubagentStart E2E
# ============================================================================


class TestSubagentStartE2E:
    """SubagentStart hook exits 0 AND returns the documented response shape.

    Neither fixture here is preceded by a PreToolUse:Agent dispatch, so there
    is no cached context and no born dispatch-kernel row to claim --
    ``adapt_subagent_start`` takes its cache-miss/no-draft/no-kernel lane.
    ``format_context_response`` (adapters/claude_code.py) then emits ONLY
    ``{"hookSpecificOutput": {"hookEventName": "SubagentStart"}}`` -- no
    ``additionalContext`` key, since Claude Code only appends that key to
    the subagent's prompt when it is present. A bare ``code == 0`` assertion
    could not tell this genuine no-injection response apart from a broken
    handler that crashed before printing (subagent_start.py catches nothing
    itself) or, worse, one that leaked a STALE ``additionalContext`` from an
    unrelated dispatch. Asserting the exact shape catches both.
    """

    HOOK = "subagent_start.py"

    def test_subagent_start_runs(self):
        """No prior dispatch -> hookEventName only, no additionalContext."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, SUBAGENT_START)
        assert code == 0, (
            f"Expected exit 0 for SubagentStart hook, got {code}. stderr: {stderr}"
        )
        assert response is not None, f"Expected a JSON response. stderr: {stderr}"
        hook_output = response.get("hookSpecificOutput", {})
        assert hook_output.get("hookEventName") == "SubagentStart", response
        assert "additionalContext" not in hook_output, (
            f"Expected no additionalContext with no prior dispatch: {response}"
        )

    def test_subagent_start_devops_runs(self):
        """Same no-injection shape for a different agent_type/task_description."""
        if not _hook_script_is_nonempty(self.HOOK):
            pytest.skip(f"{self.HOOK} not found or empty (stub only)")

        code, response, stderr = run_hook(self.HOOK, SUBAGENT_START_DEVOPS)
        assert code == 0, (
            f"Expected exit 0 for SubagentStart hook, got {code}. stderr: {stderr}"
        )
        assert response is not None, f"Expected a JSON response. stderr: {stderr}"
        hook_output = response.get("hookSpecificOutput", {})
        assert hook_output.get("hookEventName") == "SubagentStart", response
        assert "additionalContext" not in hook_output, (
            f"Expected no additionalContext with no prior dispatch: {response}"
        )
