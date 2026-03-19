#!/usr/bin/env python3
"""
T018: PostToolUse Adapter Integration Tests.

Full flow integration: adapter parse PostToolUse -> extract ToolResult -> verify fields.

Tests that the ClaudeCodeAdapter correctly parses PostToolUse events and
extracts execution metadata for audit/logging.

Modules under test:
  - hooks/adapters/claude_code.py (ClaudeCodeAdapter.parse_post_tool_use)
  - hooks/adapters/types.py (ToolResult, HookEventType)
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
from adapters.types import HookEventType, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_posttool_stdin(
    tool_name: str,
    tool_input: dict,
    tool_result: dict,
    session_id: str = "posttool-test-session",
) -> str:
    """Build a Claude Code PostToolUse stdin JSON payload."""
    return json.dumps({
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_result": tool_result,
    })


# ============================================================================
# Test Suite: Successful Bash Command
# ============================================================================

class TestSuccessfulBashPostTool:
    """PostToolUse: successful Bash command -> parse -> verify ToolResult fields."""

    def test_successful_bash_command_parsed(self):
        """Successful bash command (exit 0) -> ToolResult with correct fields."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "kubectl get pods -n default"},
            tool_result={
                "output": "NAME       READY   STATUS    RESTARTS   AGE\nnginx-pod  1/1     Running   0          2h",
                "exit_code": 0,
            },
        )

        event = adapter.parse_event(stdin_json)
        assert event.event_type == HookEventType.POST_TOOL_USE

        tool_result = adapter.parse_post_tool_use(event.payload)

        assert isinstance(tool_result, ToolResult)
        assert tool_result.tool_name == "Bash"
        assert tool_result.command == "kubectl get pods -n default"
        assert tool_result.exit_code == 0
        assert "nginx-pod" in tool_result.output
        assert tool_result.session_id == "posttool-test-session"

    def test_exit_code_zero_is_success(self):
        """Exit code 0 indicates success."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "ls -la"},
            tool_result={"output": "total 42\n-rw-r--r-- 1 user user 100 file.txt", "exit_code": 0},
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.exit_code == 0
        assert "file.txt" in tool_result.output


# ============================================================================
# Test Suite: Failed Bash Command
# ============================================================================

class TestFailedBashPostTool:
    """PostToolUse: failed Bash command (exit != 0) -> parse -> verify ToolResult."""

    def test_failed_bash_command_exit_1(self):
        """Failed bash command (exit 1) -> ToolResult with exit_code=1."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "kubectl get pods -n nonexistent"},
            tool_result={
                "output": "Error from server (NotFound): namespaces \"nonexistent\" not found",
                "exit_code": 1,
            },
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.exit_code == 1
        assert "NotFound" in tool_result.output
        assert tool_result.command == "kubectl get pods -n nonexistent"

    def test_failed_bash_command_exit_2(self):
        """Failed bash command (exit 2) -> ToolResult with exit_code=2."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "terraform apply"},
            tool_result={
                "output": "Error: permission denied",
                "exit_code": 2,
            },
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.exit_code == 2

    def test_critical_failure_exit_code_detected(self):
        """High exit codes (e.g., 127 command not found) are preserved."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "nonexistent-command"},
            tool_result={
                "output": "bash: nonexistent-command: command not found",
                "exit_code": 127,
            },
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.exit_code == 127
        assert "command not found" in tool_result.output


# ============================================================================
# Test Suite: ToolResult Field Completeness
# ============================================================================

class TestToolResultFields:
    """PostToolUse: verify ToolResult has all expected fields."""

    def test_all_fields_populated(self):
        """All ToolResult fields are populated from stdin payload."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "helm list -A"},
            tool_result={
                "output": "NAME\tNAMESPACE\tSTATUS\nnginx\tdefault\tdeployed",
                "exit_code": 0,
            },
            session_id="session-field-test",
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.tool_name == "Bash"
        assert tool_result.command == "helm list -A"
        assert tool_result.exit_code == 0
        assert "nginx" in tool_result.output
        assert tool_result.session_id == "session-field-test"

    def test_empty_output_handled(self):
        """Empty tool output is handled gracefully."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Bash",
            tool_input={"command": "true"},
            tool_result={"output": "", "exit_code": 0},
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.output == ""
        assert tool_result.exit_code == 0

    def test_missing_tool_result_defaults(self):
        """Missing tool_result fields default gracefully."""
        adapter = ClaudeCodeAdapter()
        stdin_json = json.dumps({
            "hook_event_name": "PostToolUse",
            "session_id": "test-defaults",
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "tool_result": {},
        })

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.output == ""
        assert tool_result.exit_code == 0

    def test_non_bash_tool_parsed(self):
        """Non-Bash tools (e.g., Read) are also parsed correctly."""
        adapter = ClaudeCodeAdapter()
        stdin_json = _build_posttool_stdin(
            tool_name="Read",
            tool_input={"file_path": "/tmp/test.txt"},
            tool_result={"output": "file contents here", "exit_code": 0},
        )

        event = adapter.parse_event(stdin_json)
        tool_result = adapter.parse_post_tool_use(event.payload)

        assert tool_result.tool_name == "Read"
        assert tool_result.command == ""  # Read tool has no "command" key
        assert tool_result.output == "file contents here"
