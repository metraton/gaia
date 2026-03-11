#!/usr/bin/env python3
"""
Tests for Adapter Normalized Types.

Validates:
1. All dataclasses can be instantiated with valid data
2. Frozen dataclasses raise on mutation
3. Enum completeness (HookEventType, PermissionDecision, DistributionChannel)
4. Default field values
5. HookResponse serialization
"""

import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.types import (
    AgentCompletion,
    BootstrapResult,
    CompletionResult,
    ContextResult,
    DistributionChannel,
    HookEvent,
    HookEventType,
    HookResponse,
    PermissionDecision,
    ToolResult,
    ValidationRequest,
    ValidationResult,
)


class TestHookEventType:
    """Test HookEventType enum completeness."""

    def test_has_19_event_types(self):
        """HookEventType must have exactly 19 members."""
        assert len(HookEventType) == 19

    def test_p0_events(self):
        """P0 (currently implemented) events exist."""
        assert HookEventType.PRE_TOOL_USE.value == "PreToolUse"
        assert HookEventType.POST_TOOL_USE.value == "PostToolUse"
        assert HookEventType.SUBAGENT_STOP.value == "SubagentStop"

    def test_p1_events(self):
        """P1 events exist."""
        assert HookEventType.SESSION_START.value == "SessionStart"
        assert HookEventType.USER_PROMPT_SUBMIT.value == "UserPromptSubmit"

    def test_p2_events(self):
        """P2 events exist."""
        assert HookEventType.PERMISSION_REQUEST.value == "PermissionRequest"
        assert HookEventType.STOP.value == "Stop"
        assert HookEventType.TASK_COMPLETED.value == "TaskCompleted"
        assert HookEventType.SUBAGENT_START.value == "SubagentStart"

    def test_p3_events(self):
        """P3 events exist."""
        assert HookEventType.PRE_COMPACT.value == "PreCompact"
        assert HookEventType.CONFIG_CHANGE.value == "ConfigChange"
        assert HookEventType.SESSION_END.value == "SessionEnd"
        assert HookEventType.INSTRUCTIONS_LOADED.value == "InstructionsLoaded"
        assert HookEventType.POST_TOOL_USE_FAILURE.value == "PostToolUseFailure"

    def test_p4_events(self):
        """P4 events exist."""
        assert HookEventType.NOTIFICATION.value == "Notification"

    def test_p5_events(self):
        """P5 (additional) events exist."""
        assert HookEventType.TEAMMATE_IDLE.value == "TeammateIdle"
        assert HookEventType.WORKTREE_CREATE.value == "WorktreeCreate"
        assert HookEventType.WORKTREE_REMOVE.value == "WorktreeRemove"
        assert HookEventType.PROMPT_SUBMIT.value == "PromptSubmit"

    def test_prompt_submit_is_deprecated_alias(self):
        """PROMPT_SUBMIT is a distinct enum value (deprecated alias concept)."""
        # PROMPT_SUBMIT has its own value "PromptSubmit", distinct from
        # USER_PROMPT_SUBMIT which has value "UserPromptSubmit"
        assert HookEventType.PROMPT_SUBMIT.value != HookEventType.USER_PROMPT_SUBMIT.value

    @pytest.mark.parametrize("event_type", list(HookEventType))
    def test_all_values_are_strings(self, event_type):
        """Every HookEventType value is a non-empty string."""
        assert isinstance(event_type.value, str)
        assert len(event_type.value) > 0


class TestPermissionDecision:
    """Test PermissionDecision enum."""

    def test_has_three_values(self):
        """PermissionDecision must have ALLOW, DENY, ASK."""
        assert len(PermissionDecision) == 3

    def test_allow(self):
        assert PermissionDecision.ALLOW.value == "allow"

    def test_deny(self):
        assert PermissionDecision.DENY.value == "deny"

    def test_ask(self):
        assert PermissionDecision.ASK.value == "ask"


class TestDistributionChannel:
    """Test DistributionChannel enum."""

    def test_has_two_values(self):
        """DistributionChannel must have NPM and PLUGIN."""
        assert len(DistributionChannel) == 2

    def test_npm(self):
        assert DistributionChannel.NPM.value == "npm"

    def test_plugin(self):
        assert DistributionChannel.PLUGIN.value == "plugin"


class TestHookEvent:
    """Test HookEvent frozen dataclass."""

    def test_instantiation(self):
        """HookEvent can be created with valid data."""
        event = HookEvent(
            event_type=HookEventType.PRE_TOOL_USE,
            session_id="test-session-123",
            payload={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            channel=DistributionChannel.NPM,
        )
        assert event.event_type == HookEventType.PRE_TOOL_USE
        assert event.session_id == "test-session-123"
        assert event.payload["tool_name"] == "Bash"
        assert event.channel == DistributionChannel.NPM
        assert event.plugin_root is None

    def test_with_plugin_root(self):
        """HookEvent with plugin_root set."""
        event = HookEvent(
            event_type=HookEventType.PRE_TOOL_USE,
            session_id="s1",
            payload={},
            channel=DistributionChannel.PLUGIN,
            plugin_root=Path("/opt/plugins/gaia-ops"),
        )
        assert event.plugin_root == Path("/opt/plugins/gaia-ops")

    def test_frozen(self):
        """HookEvent is immutable."""
        event = HookEvent(
            event_type=HookEventType.PRE_TOOL_USE,
            session_id="s1",
            payload={},
            channel=DistributionChannel.NPM,
        )
        with pytest.raises(AttributeError):
            event.session_id = "modified"


class TestValidationRequest:
    """Test ValidationRequest frozen dataclass."""

    def test_instantiation(self):
        req = ValidationRequest(
            tool_name="Bash",
            command="git status",
            tool_input={"command": "git status"},
            session_id="s1",
        )
        assert req.tool_name == "Bash"
        assert req.command == "git status"
        assert req.session_id == "s1"

    def test_frozen(self):
        req = ValidationRequest(
            tool_name="Bash",
            command="ls",
            tool_input={},
            session_id="s1",
        )
        with pytest.raises(AttributeError):
            req.command = "rm -rf /"


class TestValidationResult:
    """Test ValidationResult frozen dataclass with defaults."""

    def test_defaults(self):
        """ValidationResult defaults: allowed=True, tier=T0, nonce=None."""
        result = ValidationResult()
        assert result.allowed is True
        assert result.reason == ""
        assert result.tier == "T0"
        assert result.modified_input is None
        assert result.suggestions == []
        assert result.nonce is None

    def test_denied_with_nonce(self):
        result = ValidationResult(
            allowed=False,
            reason="Mutative operation requires approval",
            tier="T3",
            nonce="abc123",
        )
        assert result.allowed is False
        assert result.tier == "T3"
        assert result.nonce == "abc123"

    def test_with_modified_input(self):
        result = ValidationResult(
            allowed=True,
            reason="Footer stripped",
            modified_input={"command": "git status"},
        )
        assert result.modified_input == {"command": "git status"}

    def test_with_suggestions(self):
        result = ValidationResult(
            allowed=False,
            reason="Blocked",
            suggestions=["Use --dry-run", "Use terraform plan"],
        )
        assert len(result.suggestions) == 2

    def test_frozen(self):
        result = ValidationResult()
        with pytest.raises(AttributeError):
            result.allowed = False


class TestToolResult:
    """Test ToolResult frozen dataclass."""

    def test_instantiation(self):
        result = ToolResult(
            tool_name="Bash",
            command="git status",
            output="On branch main",
            exit_code=0,
            session_id="s1",
        )
        assert result.tool_name == "Bash"
        assert result.exit_code == 0
        assert result.output == "On branch main"

    def test_frozen(self):
        result = ToolResult(
            tool_name="Bash",
            command="ls",
            output="",
            exit_code=0,
            session_id="s1",
        )
        with pytest.raises(AttributeError):
            result.exit_code = 1


class TestAgentCompletion:
    """Test AgentCompletion frozen dataclass."""

    def test_instantiation(self):
        comp = AgentCompletion(
            agent_type="cloud-troubleshooter",
            agent_id="ct-abc-123",
            transcript_path="/tmp/transcript.jsonl",
            last_message="Task complete.",
            session_id="s1",
        )
        assert comp.agent_type == "cloud-troubleshooter"
        assert comp.agent_id == "ct-abc-123"
        assert comp.transcript_path == "/tmp/transcript.jsonl"

    def test_frozen(self):
        comp = AgentCompletion(
            agent_type="test",
            agent_id="id",
            transcript_path="/p",
            last_message="msg",
            session_id="s1",
        )
        with pytest.raises(AttributeError):
            comp.last_message = "modified"


class TestCompletionResult:
    """Test CompletionResult frozen dataclass with defaults."""

    def test_defaults(self):
        result = CompletionResult()
        assert result.contract_valid is True
        assert result.episode_id is None
        assert result.context_updated is False
        assert result.anomalies == []
        assert result.repair_needed is False

    def test_with_anomalies(self):
        result = CompletionResult(
            contract_valid=False,
            anomalies=[{"type": "missing_status", "severity": "high"}],
            repair_needed=True,
        )
        assert result.contract_valid is False
        assert len(result.anomalies) == 1
        assert result.repair_needed is True

    def test_frozen(self):
        result = CompletionResult()
        with pytest.raises(AttributeError):
            result.contract_valid = False


class TestContextResult:
    """Test ContextResult frozen dataclass."""

    def test_defaults(self):
        result = ContextResult()
        assert result.context_injected is False
        assert result.additional_context is None
        assert result.sections_provided == []

    def test_with_data(self):
        result = ContextResult(
            context_injected=True,
            additional_context="Extra prompt context here.",
            sections_provided=["project_identity", "environment"],
        )
        assert result.context_injected is True
        assert len(result.sections_provided) == 2

    def test_frozen(self):
        result = ContextResult()
        with pytest.raises(AttributeError):
            result.context_injected = True


class TestBootstrapResult:
    """Test BootstrapResult frozen dataclass."""

    def test_defaults(self):
        result = BootstrapResult()
        assert result.project_scanned is False
        assert result.context_path is None
        assert result.tools_detected == []

    def test_with_data(self):
        result = BootstrapResult(
            project_scanned=True,
            context_path=Path("/home/user/.claude/project-context.json"),
            tools_detected=["terraform", "kubectl", "helm"],
        )
        assert result.project_scanned is True
        assert result.context_path == Path("/home/user/.claude/project-context.json")
        assert len(result.tools_detected) == 3

    def test_frozen(self):
        result = BootstrapResult()
        with pytest.raises(AttributeError):
            result.project_scanned = True


class TestHookResponse:
    """Test HookResponse frozen dataclass and serialization."""

    def test_instantiation(self):
        resp = HookResponse(
            output={"permissionDecision": "allow"},
            exit_code=0,
        )
        assert resp.output == {"permissionDecision": "allow"}
        assert resp.exit_code == 0

    def test_default_exit_code(self):
        resp = HookResponse(output={"permissionDecision": "deny"})
        assert resp.exit_code == 0

    def test_exit_code_2_for_block(self):
        resp = HookResponse(
            output={"permissionDecision": "deny", "reason": "Permanently blocked"},
            exit_code=2,
        )
        assert resp.exit_code == 2

    def test_to_dict(self):
        """HookResponse.to_dict() returns a serializable dictionary."""
        resp = HookResponse(
            output={"permissionDecision": "allow", "updatedInput": {"command": "ls"}},
            exit_code=0,
        )
        d = resp.to_dict()
        assert d == {
            "output": {"permissionDecision": "allow", "updatedInput": {"command": "ls"}},
            "exit_code": 0,
        }

    def test_to_dict_roundtrip(self):
        """to_dict output can reconstruct a HookResponse."""
        original = HookResponse(
            output={"key": "value"},
            exit_code=2,
        )
        d = original.to_dict()
        reconstructed = HookResponse(**d)
        assert reconstructed == original

    def test_frozen(self):
        resp = HookResponse(output={}, exit_code=0)
        with pytest.raises(AttributeError):
            resp.exit_code = 1
