#!/usr/bin/env python3
"""
T017: PreToolUse Adapter Integration Tests.

Full flow integration: adapter parse -> bash_validator/task_validator -> adapter format response.

Tests that the ClaudeCodeAdapter correctly translates Claude Code stdin JSON
through the business logic pipeline and back to Claude Code hookSpecificOutput.

Modules under test:
  - hooks/adapters/claude_code.py (ClaudeCodeAdapter)
  - hooks/modules/tools/bash_validator.py (BashValidator)
  - hooks/modules/tools/task_validator.py (TaskValidator)
  - hooks/adapters/types.py (ValidationResult, HookResponse)
"""

import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup (follows existing project conventions)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import (
    HookEventType,
    HookResponse,
    PermissionDecision,
    ValidationResult,
)
from modules.tools.bash_validator import BashValidator
from modules.tools.task_validator import TaskValidator
from modules.security.tiers import SecurityTier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_pretool_stdin(tool_name: str, tool_input: dict) -> str:
    """Build a Claude Code PreToolUse stdin JSON payload."""
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "session_id": "integration-test-session",
        "tool_name": tool_name,
        "tool_input": tool_input,
    })


def _run_pretool_bash_flow(command: str) -> tuple:
    """Run the full PreToolUse flow for a Bash command.

    Returns:
        (event, bash_result, adapter_response) tuple.
    """
    adapter = ClaudeCodeAdapter()
    stdin_json = _build_pretool_stdin("Bash", {"command": command})

    # Step 1: Adapter parses stdin
    event = adapter.parse_event(stdin_json)

    # Step 2: Extract validation request
    validation_req = adapter.parse_pre_tool_use(event.payload)

    # Step 3: Business logic validates
    validator = BashValidator()
    bash_result = validator.validate(validation_req.command)

    # Step 4: Map BashValidationResult -> ValidationResult for adapter
    vr = ValidationResult(
        allowed=bash_result.allowed,
        reason=bash_result.reason,
        tier=str(bash_result.tier),
        modified_input=bash_result.modified_input,
        nonce=None,
    )

    # Step 5: For blocked commands (exit 2 path), use tier="BLOCKED" and no nonce
    if not bash_result.allowed and bash_result.block_response is None:
        vr = ValidationResult(
            allowed=False,
            reason=bash_result.reason,
            tier="BLOCKED",
            nonce=None,
        )

    response = adapter.format_validation_response(vr)
    return event, bash_result, response


# ============================================================================
# Test Suite: Safe Commands (allowed)
# ============================================================================

class TestSafeCommandFlow:
    """Integration: safe command -> adapter parse -> validate -> allowed response."""

    def test_kubectl_get_pods_flow(self):
        """Safe command: kubectl get pods -> allowed."""
        event, bash_result, response = _run_pretool_bash_flow("kubectl get pods")

        assert event.event_type == HookEventType.PRE_TOOL_USE
        assert bash_result.allowed is True
        assert bash_result.tier == SecurityTier.T0_READ_ONLY
        assert response.exit_code == 0
        assert response.output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_safe_by_elimination_docker_ps(self):
        """Safe by elimination: docker ps -> allowed."""
        event, bash_result, response = _run_pretool_bash_flow("docker ps")

        assert bash_result.allowed is True
        assert response.output["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert response.exit_code == 0

    def test_api_implicit_get(self):
        """API implicit GET: glab api 'projects/123' -> allowed."""
        event, bash_result, response = _run_pretool_bash_flow("glab api projects/123")

        assert bash_result.allowed is True
        assert response.output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_simulation_command_terraform_plan(self):
        """Simulation: terraform plan -> allowed."""
        event, bash_result, response = _run_pretool_bash_flow("terraform plan")

        assert bash_result.allowed is True
        assert response.output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_git_status_read_only(self):
        """Read-only: git status -> allowed."""
        event, bash_result, response = _run_pretool_bash_flow("git status")

        assert bash_result.allowed is True
        assert response.exit_code == 0


# ============================================================================
# Test Suite: Mutative Commands (denied with nonce)
# ============================================================================

class TestMutativeCommandFlow:
    """Integration: mutative command -> adapter parse -> validate -> deny response."""

    def test_git_commit_mutative_flow(self, tmp_path, monkeypatch):
        """Mutative: git commit -> denied with nonce."""
        import modules.security.approval_grants as ag
        ag._grants_dir_created = False
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / ".claude",
        )

        event, bash_result, response = _run_pretool_bash_flow(
            'git commit -m "feat(auth): add login"'
        )

        assert bash_result.allowed is False
        assert bash_result.tier == SecurityTier.T3_BLOCKED
        assert bash_result.block_response is not None
        reason = bash_result.block_response["hookSpecificOutput"]["permissionDecisionReason"]
        assert "NONCE:" in reason

    def test_terraform_apply_mutative_flow(self, tmp_path, monkeypatch):
        """Mutative: terraform apply -> denied."""
        import modules.security.approval_grants as ag
        ag._grants_dir_created = False
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / ".claude",
        )

        event, bash_result, response = _run_pretool_bash_flow("terraform apply")

        assert bash_result.allowed is False
        assert bash_result.tier == SecurityTier.T3_BLOCKED

    def test_api_explicit_post_mutative(self, tmp_path, monkeypatch):
        """API explicit POST: glab api -X POST -> denied (mutative)."""
        import modules.security.approval_grants as ag
        ag._grants_dir_created = False
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / ".claude",
        )

        event, bash_result, response = _run_pretool_bash_flow(
            "glab api -X POST /projects/123/notes"
        )

        assert bash_result.allowed is False
        assert bash_result.tier == SecurityTier.T3_BLOCKED

    def test_kubectl_apply_mutative(self, tmp_path, monkeypatch):
        """Mutative: kubectl apply -f x.yaml -> denied."""
        import modules.security.approval_grants as ag
        ag._grants_dir_created = False
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / ".claude",
        )

        event, bash_result, response = _run_pretool_bash_flow(
            "kubectl apply -f manifest.yaml"
        )

        assert bash_result.allowed is False
        assert bash_result.tier == SecurityTier.T3_BLOCKED


# ============================================================================
# Test Suite: Blocked Commands (exit 2)
# ============================================================================

class TestBlockedCommandFlow:
    """Integration: blocked command -> adapter parse -> validate -> exit 2 response."""

    def test_rm_rf_root_blocked(self):
        """Blocked: rm -rf / -> exit 2."""
        event, bash_result, response = _run_pretool_bash_flow("rm -rf /")

        assert bash_result.allowed is False
        assert bash_result.tier == SecurityTier.T3_BLOCKED
        assert bash_result.block_response is None
        assert response.exit_code == 2
        assert response.output["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_kubectl_delete_namespace_blocked(self):
        """Blocked: kubectl delete namespace -> exit 2."""
        event, bash_result, response = _run_pretool_bash_flow(
            "kubectl delete namespace production"
        )

        assert bash_result.allowed is False
        assert bash_result.block_response is None
        assert response.exit_code == 2

    def test_git_push_force_blocked(self):
        """Blocked: git push --force -> exit 2."""
        event, bash_result, response = _run_pretool_bash_flow(
            "git push --force origin main"
        )

        assert bash_result.allowed is False
        assert bash_result.block_response is None
        assert response.exit_code == 2

    def test_terraform_destroy_blocked(self):
        """Blocked: terraform destroy (without -target) -> exit 2."""
        event, bash_result, response = _run_pretool_bash_flow("terraform destroy")

        assert bash_result.allowed is False
        assert response.exit_code == 2

    def test_git_reset_hard_blocked(self):
        """Blocked: git reset --hard -> exit 2."""
        event, bash_result, response = _run_pretool_bash_flow("git reset --hard HEAD~1")

        assert bash_result.allowed is False
        assert response.exit_code == 2


# ============================================================================
# Test Suite: Compound Commands
# ============================================================================

class TestCompoundCommandFlow:
    """Integration: compound command -> adapter parse -> validate -> appropriate response."""

    def test_compound_with_mutative_part_denied(self, tmp_path, monkeypatch):
        """Compound: ls && terraform apply -> denied (mutative part)."""
        import modules.security.approval_grants as ag
        ag._grants_dir_created = False
        monkeypatch.setattr(
            "modules.security.approval_grants.get_plugin_data_dir",
            lambda: tmp_path / ".claude",
        )

        event, bash_result, response = _run_pretool_bash_flow(
            "ls -la && terraform apply"
        )

        assert bash_result.allowed is False
        assert bash_result.tier == SecurityTier.T3_BLOCKED

    def test_all_safe_compound_allowed(self):
        """Compound: ls && pwd -> allowed."""
        event, bash_result, response = _run_pretool_bash_flow("ls -la && pwd")

        assert bash_result.allowed is True
        assert response.output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_compound_with_blocked_part(self):
        """Compound: ls && kubectl delete namespace prod -> blocked."""
        event, bash_result, response = _run_pretool_bash_flow(
            "ls && kubectl delete namespace production"
        )

        assert bash_result.allowed is False


# ============================================================================
# Test Suite: Task/Agent Tool Flow
# ============================================================================

class TestTaskToolFlow:
    """Integration: Task tool -> adapter parse -> task_validator -> response."""

    def test_valid_agent_task_allowed(self):
        """Task with valid agent -> allowed."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_pretool_stdin("Task", {
            "subagent_type": "cloud-troubleshooter",
            "prompt": "Diagnose pod health in namespace test",
            "description": "Check pod status",
        })

        event = adapter.parse_event(stdin_json)
        assert event.event_type == HookEventType.PRE_TOOL_USE

        validation_req = adapter.parse_pre_tool_use(event.payload)
        assert validation_req.tool_name == "Task"

        task_validator = TaskValidator()
        task_result = task_validator.validate(event.payload.get("tool_input", {}))

        assert task_result.allowed is True
        assert task_result.agent_name == "cloud-troubleshooter"

    def test_invalid_agent_task_denied(self):
        """Task with invalid agent -> denied."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_pretool_stdin("Task", {
            "subagent_type": "nonexistent-agent",
            "prompt": "Do something",
        })

        event = adapter.parse_event(stdin_json)
        task_validator = TaskValidator()
        task_result = task_validator.validate(event.payload.get("tool_input", {}))

        assert task_result.allowed is False
        assert "Unknown agent" in task_result.reason


# ============================================================================
# Test Suite: Adapter Parse Edge Cases
# ============================================================================

class TestAdapterParseEdgeCases:
    """Integration: adapter parse error handling."""

    def test_empty_stdin_raises(self):
        """Empty stdin -> ValueError."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(ValueError, match="Empty stdin"):
            adapter.parse_event("")

    def test_invalid_json_raises(self):
        """Invalid JSON -> ValueError."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(ValueError, match="Invalid JSON"):
            adapter.parse_event("{not valid json}")

    def test_missing_event_name_raises(self):
        """Missing hook_event_name -> ValueError."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(ValueError, match="Missing required field"):
            adapter.parse_event(json.dumps({"session_id": "test"}))

    def test_unknown_event_type_raises(self):
        """Unknown event type -> ValueError."""
        adapter = ClaudeCodeAdapter()
        with pytest.raises(ValueError, match="Unknown hook event type"):
            adapter.parse_event(json.dumps({
                "hook_event_name": "UnknownEvent",
                "session_id": "test",
            }))
