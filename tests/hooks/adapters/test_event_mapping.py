"""
Tests for hook event mapping completeness.

Validates that:
1. All HookEventType enum values exist (15 core events)
2. PreToolUse, PostToolUse, SubagentStop are mapped to adapter methods
3. The adapter can handle all 3 currently-used (P0) events
4. Unknown events don't crash (graceful handling via ValueError)

Run: python3 -m pytest tests/hooks/adapters/test_event_mapping.py -v
"""

import json
import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import HookEventType


# ============================================================================
# T030: Event enumeration completeness
# ============================================================================


class TestHookEventTypeEnum:
    """Verify HookEventType enum has all expected members."""

    # The 15 event types from the Claude Code hook protocol
    EXPECTED_EVENTS = {
        # P0 - Currently implemented
        "PRE_TOOL_USE": "PreToolUse",
        "POST_TOOL_USE": "PostToolUse",
        "SUBAGENT_STOP": "SubagentStop",
        # P1
        "SESSION_START": "SessionStart",
        "USER_PROMPT_SUBMIT": "UserPromptSubmit",
        # P2
        "PERMISSION_REQUEST": "PermissionRequest",
        "STOP": "Stop",
        "TASK_COMPLETED": "TaskCompleted",
        "SUBAGENT_START": "SubagentStart",
        # P3
        "PRE_COMPACT": "PreCompact",
        "CONFIG_CHANGE": "ConfigChange",
        "SESSION_END": "SessionEnd",
        "INSTRUCTIONS_LOADED": "InstructionsLoaded",
        "POST_TOOL_USE_FAILURE": "PostToolUseFailure",
        # P4
        "NOTIFICATION": "Notification",
    }

    def test_enum_has_all_expected_members(self):
        """All 15 expected event types exist in the enum."""
        for attr_name, event_value in self.EXPECTED_EVENTS.items():
            assert hasattr(HookEventType, attr_name), (
                f"HookEventType missing member: {attr_name}"
            )
            assert HookEventType[attr_name].value == event_value, (
                f"HookEventType.{attr_name} has wrong value: "
                f"expected '{event_value}', got '{HookEventType[attr_name].value}'"
            )

    def test_enum_count(self):
        """Enum has exactly 15 members."""
        member_count = len(HookEventType)
        assert member_count == 15, (
            f"Expected 15 HookEventType members, got {member_count}. "
            f"Members: {[e.name for e in HookEventType]}"
        )

    def test_p0_events_exist(self):
        """The three P0 (currently implemented) events exist."""
        p0_events = ["PRE_TOOL_USE", "POST_TOOL_USE", "SUBAGENT_STOP"]
        for name in p0_events:
            assert hasattr(HookEventType, name), f"Missing P0 event: {name}"

    def test_enum_values_are_pascal_case(self):
        """All enum values follow PascalCase convention."""
        for member in HookEventType:
            assert member.value[0].isupper(), (
                f"Event value '{member.value}' does not start with uppercase"
            )
            assert " " not in member.value, (
                f"Event value '{member.value}' contains spaces"
            )

    def test_all_event_values_are_unique(self):
        """No duplicate event values."""
        values = [e.value for e in HookEventType]
        assert len(values) == len(set(values)), (
            f"Duplicate event values found: {[v for v in values if values.count(v) > 1]}"
        )


# ============================================================================
# T030: Adapter method mapping for P0 events
# ============================================================================


class TestAdapterEventMethodMapping:
    """Verify ClaudeCodeAdapter has methods for all P0 events."""

    @pytest.fixture
    def adapter(self):
        """Fresh adapter instance."""
        import os
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        return ClaudeCodeAdapter()

    def test_adapter_has_parse_event(self, adapter):
        """Adapter exposes parse_event for all events."""
        assert hasattr(adapter, "parse_event")
        assert callable(adapter.parse_event)

    def test_adapter_has_parse_pre_tool_use(self, adapter):
        """Adapter has parse_pre_tool_use for PreToolUse extraction."""
        assert hasattr(adapter, "parse_pre_tool_use")
        assert callable(adapter.parse_pre_tool_use)

    def test_adapter_has_parse_post_tool_use(self, adapter):
        """Adapter has parse_post_tool_use for PostToolUse extraction."""
        assert hasattr(adapter, "parse_post_tool_use")
        assert callable(adapter.parse_post_tool_use)

    def test_adapter_has_parse_agent_completion(self, adapter):
        """Adapter has parse_agent_completion for SubagentStop extraction."""
        assert hasattr(adapter, "parse_agent_completion")
        assert callable(adapter.parse_agent_completion)

    def test_adapter_has_format_validation_response(self, adapter):
        """Adapter has format_validation_response for PreToolUse output."""
        assert hasattr(adapter, "format_validation_response")
        assert callable(adapter.format_validation_response)

    def test_adapter_has_format_completion_response(self, adapter):
        """Adapter has format_completion_response for SubagentStop output."""
        assert hasattr(adapter, "format_completion_response")
        assert callable(adapter.format_completion_response)

    def test_adapter_has_format_context_response(self, adapter):
        """Adapter has format_context_response for future UserPromptSubmit."""
        assert hasattr(adapter, "format_context_response")
        assert callable(adapter.format_context_response)


# ============================================================================
# T030: All P0 events can be parsed by the adapter
# ============================================================================


class TestAdapterHandlesP0Events:
    """Verify that all 3 P0 events are parseable end-to-end."""

    @pytest.fixture
    def adapter(self):
        """Fresh adapter instance."""
        import os
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        return ClaudeCodeAdapter()

    def test_parse_pre_tool_use_event(self, adapter):
        """PreToolUse event parses without error."""
        stdin_data = json.dumps({
            "hook_event_name": "PreToolUse",
            "session_id": "test-001",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        })
        event = adapter.parse_event(stdin_data)
        assert event.event_type == HookEventType.PRE_TOOL_USE

    def test_parse_post_tool_use_event(self, adapter):
        """PostToolUse event parses without error."""
        stdin_data = json.dumps({
            "hook_event_name": "PostToolUse",
            "session_id": "test-002",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_result": {"output": "file.txt", "exit_code": 0},
        })
        event = adapter.parse_event(stdin_data)
        assert event.event_type == HookEventType.POST_TOOL_USE

    def test_parse_subagent_stop_event(self, adapter):
        """SubagentStop event parses without error."""
        stdin_data = json.dumps({
            "hook_event_name": "SubagentStop",
            "session_id": "test-003",
            "agent_type": "cloud-troubleshooter",
            "agent_id": "a123456",
            "agent_transcript_path": "/tmp/transcript.jsonl",
            "last_assistant_message": "Done.",
        })
        event = adapter.parse_event(stdin_data)
        assert event.event_type == HookEventType.SUBAGENT_STOP


# ============================================================================
# T030: Unknown events are handled gracefully
# ============================================================================


class TestUnknownEventHandling:
    """Verify unknown events raise ValueError (not crash/hang)."""

    @pytest.fixture
    def adapter(self):
        """Fresh adapter instance."""
        import os
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        return ClaudeCodeAdapter()

    def test_unknown_event_raises_value_error(self, adapter):
        """Unknown event type raises ValueError with descriptive message."""
        stdin_data = json.dumps({
            "hook_event_name": "CompletelyMadeUpEvent",
            "session_id": "test-unknown",
        })
        with pytest.raises(ValueError, match="Unknown hook event type"):
            adapter.parse_event(stdin_data)

    def test_empty_event_name_raises_value_error(self, adapter):
        """Empty hook_event_name raises ValueError."""
        stdin_data = json.dumps({
            "hook_event_name": "",
            "session_id": "test-empty",
        })
        with pytest.raises(ValueError, match="Missing required field"):
            adapter.parse_event(stdin_data)

    def test_all_non_p0_events_still_parse(self, adapter):
        """All P1-P4 events parse (even though no business logic exists yet)."""
        non_p0_events = [
            "SessionStart", "UserPromptSubmit",
            "PermissionRequest", "Stop", "TaskCompleted", "SubagentStart",
            "PreCompact", "ConfigChange", "SessionEnd",
            "InstructionsLoaded", "PostToolUseFailure",
            "Notification",
        ]
        for event_name in non_p0_events:
            stdin_data = json.dumps({
                "hook_event_name": event_name,
                "session_id": "test-parse",
            })
            event = adapter.parse_event(stdin_data)
            assert event.event_type.value == event_name, (
                f"Failed to parse event: {event_name}"
            )
