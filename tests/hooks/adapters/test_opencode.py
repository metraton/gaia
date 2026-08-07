"""Contract tests for the OpenCode adapter's normalized event boundary."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.opencode import OpenCodeAdapter, _apply_patch_paths
from adapters.registry import get_adapter
from adapters.types import HookEventType, HookResponse, HostCapability, ValidationResult


def test_parses_tool_before_with_immutable_correlation_fields():
    adapter = OpenCodeAdapter()

    event = adapter.parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-parent",
        "callID": "call-1",
        "agentID": "agent-parent",
        "dispatchID": "dispatch-1",
        "parentDispatchID": "dispatch-root",
        "tool": "bash",
        "args": {"command": "git status"},
    }))

    assert event.event_type is HookEventType.PRE_TOOL_USE
    assert event.session_id == "ses-parent"
    assert event.call_id == "call-1"
    assert event.host_agent_id == "agent-parent"
    assert event.dispatch_id == "dispatch-1"
    assert event.parent_dispatch_id == "dispatch-root"
    assert event.payload["tool_name"] == "bash"
    assert event.payload["tool_input"] == {"command": "git status"}


def test_rejects_event_without_stable_session_identity():
    with pytest.raises(ValueError, match="sessionID"):
        OpenCodeAdapter().parse_event(json.dumps({
            "event": "tool.execute.before",
            "tool": "bash",
        }))


def test_formats_a_rewritten_input_without_claude_protocol_fields():
    response = OpenCodeAdapter().format_validation_response(ValidationResult(
        allowed=True,
        reason="normalized",
        modified_input={"command": "git status"},
    ))

    assert response.output == {
        "action": "allow",
        "reason": "normalized",
        "updated_input": {"command": "git status"},
        "tier": "T0",
    }


def test_reads_and_updates_only_the_opencode_response_shape():
    adapter = OpenCodeAdapter()
    response = {"action": "ask", "reason": "approval required"}

    assert adapter.read_permission_decision(response) == "ask"
    assert adapter.read_permission_reason(response) == "approval required"
    assert adapter.inject_updated_input(response, {"command": "git status"}) == {
        "action": "ask",
        "reason": "approval required",
        "updated_input": {"command": "git status"},
    }


def test_fails_closed_when_the_policy_rejects_missing_bash_input():
    response = OpenCodeAdapter().adapt_pre_tool_use(OpenCodeAdapter().parse_event(
        json.dumps({
            "event": "tool.execute.before",
            "sessionID": "ses-parent",
            "tool": "bash",
        })
    ))

    assert response.exit_code == 2
    assert response.output["action"] == "deny"


def test_translates_the_policy_adapter_response_without_claude_fields(monkeypatch):
    from adapters.claude_code import ClaudeCodeAdapter

    def fake_policy(_self, event):
        assert event.payload["tool_name"] == "Bash"
        assert event.payload["tool_use_id"] == "call-1"
        return HookResponse(
            output={
                "hookSpecificOutput": {
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "rewritten",
                    "updatedInput": {"command": "git status"},
                }
            }
        )

    monkeypatch.setattr(ClaudeCodeAdapter, "adapt_pre_tool_use", fake_policy)
    response = OpenCodeAdapter().adapt_pre_tool_use(OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.before",
        "sessionID": "ses-parent",
        "callID": "call-1",
        "tool": "bash",
    })))

    assert response.output == {
        "action": "allow",
        "reason": "rewritten",
        "updated_input": {"command": "git status"},
    }


def test_forwards_post_tool_use_to_the_policy_adapter(monkeypatch):
    from adapters.claude_code import ClaudeCodeAdapter

    def fake_policy(_self, event):
        assert event.payload["tool_name"] == "Bash"
        assert event.payload["tool_use_id"] == "call-2"
        assert event.payload["tool_response"] == {"output": "ok"}
        return HookResponse(output={})

    monkeypatch.setattr(ClaudeCodeAdapter, "adapt_post_tool_use", fake_policy)
    response = OpenCodeAdapter().adapt_post_tool_use(OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.after",
        "sessionID": "ses-parent",
        "callID": "call-2",
        "tool": "bash",
        "args": {"command": "git status"},
        "result": {"output": "ok"},
    })))

    assert response.output == {"action": "allow"}


def test_parse_post_tool_use_preserves_structured_bash_failure():
    event = OpenCodeAdapter().parse_event(json.dumps({
        "event": "tool.execute.after", "sessionID": "ses-child", "tool": "bash",
        "args": {"command": "false"},
        "result": {"output": "", "exit_code": 7, "is_error": True},
    }))
    result = OpenCodeAdapter().parse_post_tool_use(event.payload)
    assert result.exit_code == 7


def test_declares_the_capabilities_confirmed_by_the_spike():
    assert OpenCodeAdapter().capabilities() == frozenset({
        HostCapability.INTERACTIVE_CONSENT,
        HostCapability.OUT_OF_BAND_APPROVAL,
        HostCapability.STRUCTURED_PERMISSION_DECISION,
        HostCapability.UPDATED_INPUT,
    })


def test_registry_selects_opencode_only_when_explicitly_requested(monkeypatch):
    monkeypatch.setenv("GAIA_HOST", "opencode")
    assert isinstance(get_adapter(), OpenCodeAdapter)


def test_apply_patch_extracts_all_file_and_move_paths():
    assert _apply_patch_paths("""*** Begin Patch
*** Add File: src/a.py
*** Update File: src/b.py
*** Move to: src/c.py
*** Delete File: src/d.py
*** End Patch""") == ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]


@pytest.mark.parametrize("patch", ["", "*** Begin Patch\n*** End Patch", "*** Begin Patch\n*** Rename File: a\n*** End Patch"])
def test_apply_patch_rejects_malformed_or_unsupported_markers(patch):
    with pytest.raises(ValueError):
        _apply_patch_paths(patch)
