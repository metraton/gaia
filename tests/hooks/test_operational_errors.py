"""Storage exhaustion is an operational hook failure, not a policy denial."""

from __future__ import annotations

import errno
import json
import sys
from pathlib import Path


HOOKS = Path(__file__).resolve().parents[2] / "hooks"
sys.path.insert(0, str(HOOKS))

from adapters.claude_code import ClaudeCodeAdapter
from modules.core.operational_errors import storage_exhaustion_message
from modules.security import approval_grants


def test_enospc_has_distinct_non_policy_message():
    message = storage_exhaustion_message(
        OSError(errno.ENOSPC, "No space left on device")
    )

    assert message is not None
    assert "Operational hook failure" in message
    assert "not a security-policy denial" in message


def test_unrelated_exception_is_not_reclassified():
    assert storage_exhaustion_message(RuntimeError("broken invariant")) is None


def test_adapter_stays_fail_closed_for_enospc(monkeypatch):
    def fail_cleanup():
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(approval_grants, "cleanup_expired_grants", fail_cleanup)
    adapter = ClaudeCodeAdapter()
    event = adapter.parse_event(
        json.dumps(
            {
                "hook_event_name": "PreToolUse",
                "session_id": "session-enospc",
                "agent_id": "a-enospc-test",
                "tool_name": "Bash",
                "tool_input": {"command": "git status"},
            }
        )
    )

    response = adapter.adapt_pre_tool_use(event)

    # Exit 2 is the only blocking exit code (exit 1 is a non-blocking hook
    # error, which would let the tool call through unvalidated); the distinct
    # message, not the exit code, is what marks this as operational.
    assert response.exit_code == 2
    assert isinstance(response.output, str)
    assert "Operational hook failure" in response.output
    assert "security validation" not in response.output
