"""
E2E JSON fixture payloads for hook subprocess tests.

Realistic Claude Code JSON payloads as Python dicts, matching the stdin
protocol that hooks/pre_tool_use.py and hooks/post_tool_use.py expect.

Each fixture includes hook_event_name so the adapter layer can route it.

JSON fixture files are also available in tests/fixtures/plugin/*.json.
Use load_fixture(name) to load a JSON fixture by name.
"""

import json
from pathlib import Path

# Directory containing standalone JSON fixture files (FR-029)
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "plugin"


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file by name from tests/fixtures/plugin/.

    Args:
        name: Fixture name without .json extension (e.g. 'pretool_bash_safe').

    Returns:
        Parsed dict from the JSON fixture file.

    Raises:
        FileNotFoundError: If the fixture file does not exist.
        json.JSONDecodeError: If the fixture file is not valid JSON.
    """
    fixture_path = _FIXTURES_DIR / f"{name}.json"
    with open(fixture_path) as f:
        return json.load(f)

# ============================================================================
# PreToolUse Bash -- Safe (T0 read-only)
# ============================================================================

PRETOOL_BASH_SAFE = {
    "tool_name": "Bash",
    "tool_input": {"command": "kubectl get pods -n staging"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-safe-001",
}

PRETOOL_BASH_SAFE_LS = {
    "tool_name": "Bash",
    "tool_input": {"command": "ls -la /tmp"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-safe-002",
}

PRETOOL_BASH_SAFE_GIT_STATUS = {
    "tool_name": "Bash",
    "tool_input": {"command": "git status"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-safe-003",
}

PRETOOL_BASH_SAFE_CAT = {
    "tool_name": "Bash",
    "tool_input": {"command": "cat /etc/hostname"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-safe-004",
}

# ============================================================================
# PreToolUse Bash -- Mutative (T3, nonce-denied)
# ============================================================================

PRETOOL_BASH_MUTATIVE = {
    "tool_name": "Bash",
    "tool_input": {"command": "git commit -m 'feat: add login'"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-mutative-001",
}

PRETOOL_BASH_MUTATIVE_KUBECTL_APPLY = {
    "tool_name": "Bash",
    "tool_input": {"command": "kubectl apply -f manifest.yaml"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-mutative-002",
}

# ============================================================================
# PreToolUse Bash -- Blocked (permanently denied, exit 2)
# ============================================================================

PRETOOL_BASH_BLOCKED_TERRAFORM_DESTROY = {
    "tool_name": "Bash",
    "tool_input": {"command": "terraform destroy"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-blocked-002",
}

PRETOOL_BASH_BLOCKED_GIT_RESET_HARD = {
    "tool_name": "Bash",
    "tool_input": {"command": "git reset --hard"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-blocked-003",
}


def make_blocked_rm_fixture():
    """Build the rm -rf / fixture dynamically to avoid hook scanning."""
    cmd_parts = ["rm", "-rf", "/"]
    return {
        "tool_name": "Bash",
        "tool_input": {"command": " ".join(cmd_parts)},
        "hook_event_name": "PreToolUse",
        "session_id": "e2e-test-blocked-001",
    }


PRETOOL_BASH_BLOCKED = make_blocked_rm_fixture()

# ============================================================================
# PreToolUse Agent/Task -- Valid project agent
# ============================================================================

PRETOOL_AGENT = {
    "tool_name": "Agent",
    "tool_input": {
        "prompt": "Check pod status",
        "subagent_type": "cloud-troubleshooter",
    },
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-agent-001",
}

PRETOOL_AGENT_DEVOPS = {
    "tool_name": "Agent",
    "tool_input": {
        "prompt": "Run npm audit",
        "subagent_type": "developer",
    },
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-agent-002",
}

# ============================================================================
# PreToolUse -- Other tools (Read, Write, etc.) pass through
# ============================================================================

PRETOOL_READ = {
    "tool_name": "Read",
    "tool_input": {"file_path": "/tmp/test.txt"},
    "hook_event_name": "PreToolUse",
    "session_id": "e2e-test-read-001",
}

# ============================================================================
# PostToolUse -- Successful Bash result
# ============================================================================

POSTTOOL_BASH = {
    "tool_name": "Bash",
    "tool_input": {"command": "ls -la"},
    "tool_result": {
        "stdout": "total 42\ndrwxr-xr-x 5 user user 4096 Jan 01 00:00 .",
        "output": "total 42\ndrwxr-xr-x 5 user user 4096 Jan 01 00:00 .",
        "exit_code": 0,
        "duration_ms": 50,
    },
    "hook_event_name": "PostToolUse",
    "session_id": "e2e-test-post-001",
}

POSTTOOL_BASH_FAILED = {
    "tool_name": "Bash",
    "tool_input": {"command": "cat /nonexistent"},
    "tool_result": {
        "stdout": "",
        "output": "cat: /nonexistent: No such file or directory",
        "exit_code": 1,
        "duration_ms": 10,
    },
    "hook_event_name": "PostToolUse",
    "session_id": "e2e-test-post-002",
}

# ============================================================================
# Malformed / edge-case payloads for error handling tests
# ============================================================================

MALFORMED_MISSING_EVENT_NAME = {
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
    "session_id": "e2e-test-malformed-001",
    # Missing hook_event_name
}

MALFORMED_UNKNOWN_EVENT = {
    "tool_name": "Bash",
    "tool_input": {"command": "ls"},
    "hook_event_name": "NonExistentEvent",
    "session_id": "e2e-test-malformed-002",
}

# ============================================================================
# P2: Stop -- Session/agent stop event
# ============================================================================

STOP_EVENT = {
    "hook_event_name": "Stop",
    "session_id": "e2e-test-stop-001",
}

STOP_EVENT_WITH_REASON = {
    "hook_event_name": "Stop",
    "session_id": "e2e-test-stop-002",
    "stop_reason": "user_requested",
    "last_assistant_message": "Task complete. All tests passed.",
}

# ============================================================================
# P2: TaskCompleted -- Task completion verification
# ============================================================================

TASK_COMPLETED = {
    "task_id": "task-123",
    "hook_event_name": "TaskCompleted",
    "session_id": "e2e-test-task-001",
}

TASK_COMPLETED_WITH_OUTPUT = {
    "task_id": "task-456",
    "task_output": "All tests passed. 15/15 assertions verified.",
    "hook_event_name": "TaskCompleted",
    "session_id": "e2e-test-task-002",
}

# ============================================================================
# P2: SubagentStart -- Agent context injection
# ============================================================================

SUBAGENT_START = {
    "agent_type": "cloud-troubleshooter",
    "hook_event_name": "SubagentStart",
    "session_id": "e2e-test-substart-001",
}

SUBAGENT_START_DEVOPS = {
    "agent_type": "developer",
    "task_description": "Run npm audit and fix vulnerabilities",
    "hook_event_name": "SubagentStart",
    "session_id": "e2e-test-substart-002",
}
