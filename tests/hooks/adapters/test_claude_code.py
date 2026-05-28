#!/usr/bin/env python3
"""
Tests for ClaudeCodeAdapter.

Validates:
1. parse_event with realistic Claude Code JSON payloads
2. parse_pre_tool_use for Bash and Agent tools
3. parse_post_tool_use with result data
4. parse_agent_completion with SubagentStop data
5. format_validation_response (allow, deny with nonce, permanent block)
6. format_completion_response (valid, needs repair)
7. format_context_response
8. format_ask_response
9. detect_channel (PLUGIN via env var, NPM default)
10. Edge cases: missing fields, malformed JSON
"""

import sys
import json
import os
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import (
    AgentCompletion,
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


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def adapter():
    """Fresh ClaudeCodeAdapter instance with clean env."""
    # Ensure CLAUDE_PLUGIN_ROOT is not set for default tests
    old_val = os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
    a = ClaudeCodeAdapter()
    yield a
    # Restore env
    if old_val is not None:
        os.environ["CLAUDE_PLUGIN_ROOT"] = old_val
    else:
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)


@pytest.fixture
def pre_tool_use_bash_payload():
    """Realistic PreToolUse Bash event from Claude Code."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-abc123",
        "tool_name": "Bash",
        "tool_input": {
            "command": "git status",
            "description": "Show working tree status",
        },
    }


@pytest.fixture
def pre_tool_use_agent_payload():
    """Realistic PreToolUse Agent/Task event from Claude Code."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": "sess-def456",
        "tool_name": "Agent",
        "tool_input": {
            "subagent_type": "cloud-troubleshooter",
            "prompt": "Investigate pod crashloop in namespace prod",
            "description": "Diagnose pod crash in prod",
        },
    }


@pytest.fixture
def post_tool_use_payload():
    """Realistic PostToolUse event from Claude Code."""
    return {
        "hook_event_name": "PostToolUse",
        "session_id": "sess-abc123",
        "tool_name": "Bash",
        "tool_input": {
            "command": "kubectl get pods -n default",
        },
        "tool_response": {
            "output": "NAME           READY   STATUS    RESTARTS   AGE\nweb-abc-123    1/1     Running   0          2d",
            "exit_code": 0,
            "duration_ms": 450,
        },
    }


@pytest.fixture
def subagent_stop_payload():
    """Realistic SubagentStop event from Claude Code."""
    return {
        "hook_event_name": "SubagentStop",
        "session_id": "sess-ghi789",
        "agent_type": "cloud-troubleshooter",
        "agent_id": "a1b2c3d",
        "agent_transcript_path": "/tmp/transcripts/a1b2c3d.jsonl",
        "last_assistant_message": "Task complete. Pod was OOMKilled.\n\n```agent_contract_handoff\n{\"plan_status\": \"COMPLETE\", \"agent_id\": \"a1b2c3d\", \"pending_steps\": [], \"next_action\": \"done\"}\n```",
        "cwd": "/home/user/project",
        "stop_hook_active": True,
        "permission_mode": "default",
    }


# ============================================================================
# T004: parse_event tests
# ============================================================================


class TestParseEvent:
    """Test parse_event with realistic Claude Code JSON."""

    def test_parse_pre_tool_use_bash(self, adapter, pre_tool_use_bash_payload):
        """Parse a PreToolUse Bash event."""
        stdin_data = json.dumps(pre_tool_use_bash_payload)
        event = adapter.parse_event(stdin_data)

        assert event.event_type == HookEventType.PRE_TOOL_USE
        assert event.session_id == "sess-abc123"
        assert event.payload["tool_name"] == "Bash"
        assert event.payload["tool_input"]["command"] == "git status"
        assert event.channel == DistributionChannel.NPM
        assert event.plugin_root is None

    def test_parse_pre_tool_use_agent(self, adapter, pre_tool_use_agent_payload):
        """Parse a PreToolUse Agent event with subagent_type."""
        stdin_data = json.dumps(pre_tool_use_agent_payload)
        event = adapter.parse_event(stdin_data)

        assert event.event_type == HookEventType.PRE_TOOL_USE
        assert event.session_id == "sess-def456"
        assert event.payload["tool_name"] == "Agent"
        assert event.payload["tool_input"]["subagent_type"] == "cloud-troubleshooter"

    def test_parse_post_tool_use(self, adapter, post_tool_use_payload):
        """Parse a PostToolUse event with result."""
        stdin_data = json.dumps(post_tool_use_payload)
        event = adapter.parse_event(stdin_data)

        assert event.event_type == HookEventType.POST_TOOL_USE
        assert event.session_id == "sess-abc123"
        assert event.payload["tool_response"]["exit_code"] == 0

    def test_parse_subagent_stop(self, adapter, subagent_stop_payload):
        """Parse a SubagentStop event with transcript path."""
        stdin_data = json.dumps(subagent_stop_payload)
        event = adapter.parse_event(stdin_data)

        assert event.event_type == HookEventType.SUBAGENT_STOP
        assert event.session_id == "sess-ghi789"
        assert event.payload["agent_type"] == "cloud-troubleshooter"
        assert event.payload["agent_id"] == "a1b2c3d"

    def test_parse_event_empty_stdin(self, adapter):
        """Empty stdin raises ValueError."""
        with pytest.raises(ValueError, match="Empty stdin data"):
            adapter.parse_event("")

    def test_parse_event_whitespace_stdin(self, adapter):
        """Whitespace-only stdin raises ValueError."""
        with pytest.raises(ValueError, match="Empty stdin data"):
            adapter.parse_event("   \n\t  ")

    def test_parse_event_invalid_json(self, adapter):
        """Malformed JSON raises ValueError."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            adapter.parse_event("{not valid json}")

    def test_parse_event_missing_hook_event_name(self, adapter):
        """Missing hook_event_name raises ValueError."""
        with pytest.raises(ValueError, match="Missing required field: hook_event_name"):
            adapter.parse_event(json.dumps({"session_id": "s1"}))

    def test_parse_event_unknown_event_type(self, adapter):
        """Unknown event type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown hook event type"):
            adapter.parse_event(json.dumps({
                "hook_event_name": "NonExistentEvent",
                "session_id": "s1",
            }))

    def test_parse_event_json_array(self, adapter):
        """JSON array instead of object raises ValueError."""
        with pytest.raises(ValueError, match="Expected JSON object"):
            adapter.parse_event(json.dumps([1, 2, 3]))

    def test_parse_event_missing_session_id(self, adapter):
        """Missing session_id defaults to empty string (non-fatal)."""
        stdin_data = json.dumps({"hook_event_name": "PreToolUse"})
        event = adapter.parse_event(stdin_data)
        assert event.session_id == ""

    def test_parse_event_with_plugin_channel(self):
        """When CLAUDE_PLUGIN_ROOT is set, channel is PLUGIN."""
        os.environ["CLAUDE_PLUGIN_ROOT"] = "/opt/plugins/gaia-ops"
        try:
            a = ClaudeCodeAdapter()
            stdin_data = json.dumps({
                "hook_event_name": "PreToolUse",
                "session_id": "s1",
            })
            event = a.parse_event(stdin_data)
            assert event.channel == DistributionChannel.PLUGIN
            assert event.plugin_root == Path("/opt/plugins/gaia-ops")
        finally:
            del os.environ["CLAUDE_PLUGIN_ROOT"]


# ============================================================================
# T005: parse_pre_tool_use tests
# ============================================================================


class TestParsePreToolUse:
    """Test parse_pre_tool_use helper method."""

    def test_bash_command(self, adapter, pre_tool_use_bash_payload):
        """Extract command from Bash tool input."""
        req = adapter.parse_pre_tool_use(pre_tool_use_bash_payload)

        assert isinstance(req, ValidationRequest)
        assert req.tool_name == "Bash"
        assert req.command == "git status"
        assert req.tool_input == pre_tool_use_bash_payload["tool_input"]
        assert req.session_id == "sess-abc123"

    def test_agent_prompt(self, adapter, pre_tool_use_agent_payload):
        """Extract prompt from Agent tool input."""
        req = adapter.parse_pre_tool_use(pre_tool_use_agent_payload)

        assert isinstance(req, ValidationRequest)
        assert req.tool_name == "Agent"
        assert req.command == "Investigate pod crashloop in namespace prod"
        assert req.tool_input["subagent_type"] == "cloud-troubleshooter"

    def test_task_prompt(self, adapter):
        """Extract prompt from Task tool input (lowercase tool name)."""
        payload = {
            "tool_name": "Task",
            "tool_input": {"prompt": "Run terraform plan"},
            "session_id": "s1",
        }
        req = adapter.parse_pre_tool_use(payload)
        assert req.tool_name == "Task"
        assert req.command == "Run terraform plan"

    def test_unknown_tool_fallback(self, adapter):
        """Unknown tool falls back to command then prompt."""
        payload = {
            "tool_name": "CustomTool",
            "tool_input": {"prompt": "do something"},
            "session_id": "s1",
        }
        req = adapter.parse_pre_tool_use(payload)
        assert req.command == "do something"

    def test_missing_fields(self, adapter):
        """Missing fields default to empty strings/dicts."""
        req = adapter.parse_pre_tool_use({})
        assert req.tool_name == ""
        assert req.command == ""
        assert req.tool_input == {}
        assert req.session_id == ""


# ============================================================================
# T006: parse_post_tool_use tests
# ============================================================================


class TestParsePostToolUse:
    """Test parse_post_tool_use helper method."""

    def test_successful_bash_result(self, adapter, post_tool_use_payload):
        """Extract result from successful Bash execution."""
        result = adapter.parse_post_tool_use(post_tool_use_payload)

        assert isinstance(result, ToolResult)
        assert result.tool_name == "Bash"
        assert result.command == "kubectl get pods -n default"
        assert "web-abc-123" in result.output
        assert result.exit_code == 0
        assert result.session_id == "sess-abc123"

    def test_failed_command(self, adapter):
        """Extract result from failed command."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "kubectl get pods -n nonexistent"},
            "tool_response": {
                "output": "Error from server (NotFound): namespaces \"nonexistent\" not found",
                "exit_code": 1,
                "duration_ms": 200,
            },
            "session_id": "s1",
        }
        result = adapter.parse_post_tool_use(payload)
        assert result.exit_code == 1
        assert "NotFound" in result.output

    def test_missing_tool_response(self, adapter):
        """Missing tool_response defaults gracefully."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "s1",
        }
        result = adapter.parse_post_tool_use(payload)
        assert result.output == ""
        assert result.exit_code == 0


# ============================================================================
# T007: parse_agent_completion tests
# ============================================================================


class TestParseAgentCompletion:
    """Test parse_agent_completion helper method."""

    def test_full_payload(self, adapter, subagent_stop_payload):
        """Extract agent completion from full SubagentStop payload."""
        comp = adapter.parse_agent_completion(subagent_stop_payload)

        assert isinstance(comp, AgentCompletion)
        assert comp.agent_type == "cloud-troubleshooter"
        assert comp.agent_id == "a1b2c3d"
        assert comp.transcript_path == "/tmp/transcripts/a1b2c3d.jsonl"
        assert '"plan_status": "COMPLETE"' in comp.last_message
        assert comp.session_id == "sess-ghi789"

    def test_missing_fields(self, adapter):
        """Missing fields default to empty strings."""
        comp = adapter.parse_agent_completion({})
        assert comp.agent_type == ""
        assert comp.agent_id == ""
        assert comp.transcript_path == ""
        assert comp.last_message == ""
        assert comp.session_id == ""


# ============================================================================
# Bug B: adapt_subagent_stop must use event.session_id, not the synthetic
# env-derived id from get_or_create_session_id()
# ============================================================================


class TestAdaptSubagentStopSessionId:
    """Regression test for Bug B / P-a11d14e0.

    The old code called ``session_id = get_or_create_session_id()`` which
    falls back to an env-var-derived synthetic id when CLAUDE_SESSION_ID is
    not set. Pending approval records are persisted with the real
    ``event.session_id`` from the stdin event, so the synthetic id never
    matched and ``cleanup_approval`` / ``consume_session_grants`` never ran
    for the right session. The fix is to prefer ``event.session_id`` and
    only fall back to the synthetic id when the event carries none.
    """

    def test_session_id_resolution_prefers_event(
        self, adapter, subagent_stop_payload, monkeypatch
    ):
        """Even when CLAUDE_SESSION_ID env points at a different (synthetic)
        session, the adapter must use the session_id from the parsed event.
        """
        from adapters.types import DistributionChannel, HookEvent, HookEventType

        # Point the env-derived synthetic id at a value that does NOT match
        # the event's session_id. The old buggy code would pick this one.
        monkeypatch.setenv("CLAUDE_SESSION_ID", "synthetic-mismatch")

        event = HookEvent(
            event_type=HookEventType.SUBAGENT_STOP,
            session_id=subagent_stop_payload["session_id"],  # "sess-ghi789"
            payload=subagent_stop_payload,
            channel=DistributionChannel.NPM,
        )

        # Stub the modules adapt_subagent_stop pulls in. We only care about
        # which session_id reaches cleanup_approval / consume_session_grants.
        captured = {}

        def _fake_cleanup_approval(agent_type, session_id=None, preserve_nonces=None):
            captured["cleanup_agent_type"] = agent_type
            captured["cleanup_session_id"] = session_id
            captured["cleanup_preserve_nonces"] = preserve_nonces

        def _fake_consume_session_grants(session_id):
            captured["consumed_session_id"] = session_id
            return 0

        # Patch every module the adapter imports lazily so the test isolates
        # the session_id resolution path. Each lambda is intentionally
        # cheap — the goal is to let adapt_subagent_stop walk past every
        # side-effect call without doing anything that could mask the bug
        # under test.
        import sys as _sys
        import types as _types

        def _install_stub(module_name, attrs):
            module = _types.ModuleType(module_name)
            for k, v in attrs.items():
                setattr(module, k, v)
            monkeypatch.setitem(_sys.modules, module_name, module)

        _install_stub(
            "modules.agents.contract_validator",
            {
                "extract_commands_from_evidence": lambda *_a, **_k: [],
                "parse_contract": lambda *_a, **_k: None,
                "requires_consolidation_report": lambda *_a, **_k: False,
                "validate": lambda *_a, **_k: _types.SimpleNamespace(
                    is_valid=True, error_message=""
                ),
                "validate_approval_request": lambda *_a, **_k: None,
                "validate_verbatim_outputs_consistency": lambda *_a, **_k: None,
            },
        )
        _install_stub(
            "modules.agents.response_contract",
            {
                "save_validation_result": lambda *_a, **_k: None,
                "validate_response_contract": lambda *_a, **_k: _types.SimpleNamespace(
                    valid=True, errors=[], warnings=[]
                ),
                "resolve_agent_id": lambda *_a, **_k: "agent-id",
            },
        )
        _install_stub(
            "modules.agents.task_info_builder",
            {
                "build_task_info_from_hook_data": lambda hook_data, _agent_output: {
                    "agent": hook_data.get("agent_type", "unknown"),
                    "agent_id": hook_data.get("agent_id", "unknown"),
                    "task_id": "task-id",
                    "agent_transcript_path": hook_data.get(
                        "agent_transcript_path", ""
                    ),
                },
            },
        )
        _install_stub(
            "modules.agents.transcript_reader",
            {"read_transcript": lambda *_a, **_k: ""},
        )
        _install_stub(
            "modules.audit.workflow_auditor",
            {
                "audit": lambda *_a, **_k: None,
                "signal_gaia_analysis": lambda *_a, **_k: None,
            },
        )
        _install_stub(
            "modules.audit.workflow_recorder",
            {"record": lambda *_a, **_k: None},
        )
        _install_stub(
            "modules.context.context_writer",
            {
                "process_context_updates": lambda *_a, **_k: None,
                "process_update_contracts": lambda *_a, **_k: None,
            },
        )
        _install_stub(
            "modules.memory.episode_writer", {"write": lambda *_a, **_k: None}
        )
        _install_stub(
            "modules.security.approval_cleanup",
            {"cleanup": _fake_cleanup_approval},
        )
        _install_stub(
            "modules.security.approval_grants",
            {"consume_session_grants": _fake_consume_session_grants},
        )

        # Synthetic id (the buggy fallback) — confirm it differs from the
        # event id so the assertion below has teeth.
        from modules.session.session_manager import get_or_create_session_id

        synthetic = get_or_create_session_id()
        assert synthetic == "synthetic-mismatch", (
            "Test scaffolding broken: env-derived synthetic id should "
            "reflect the monkeypatched CLAUDE_SESSION_ID."
        )
        assert event.session_id != synthetic

        # Make the gaia-agents check pass so adapt_subagent_stop reaches
        # the cleanup branch instead of bailing on the native-agent path.
        monkeypatch.setattr(
            adapter,
            "_get_gaia_agent_names",
            lambda: [subagent_stop_payload["agent_type"]],
        )

        adapter.adapt_subagent_stop(event)

        assert captured.get("consumed_session_id") == event.session_id, (
            "adapt_subagent_stop must consume grants for the session_id "
            "carried by the stdin event (Bug B). Falling back to the "
            "env-derived synthetic id breaks cleanup for the real "
            "session that owned the pending approval."
        )


# ============================================================================
# Phase 2: adapt_subagent_stop must preserve pending nonces still referenced
# by the agent's final APPROVAL_REQUEST contract
# ============================================================================


class TestAdaptSubagentStopPreservesApprovalRequest:
    """adapt_subagent_stop must not delete pending files that the agent's
    final contract still references via plan_status=APPROVAL_REQUEST.

    The user needs those files to act on the [ACTIONABLE] block; cleaning
    them up at SubagentStop would silently void the approval request and
    leave the agent stuck in a loop on the next dispatch (regenerating a
    new nonce instead of resuming the one the user already saw).
    """

    def _install_module_stubs(self, monkeypatch, captured, parsed_contract):
        """Install the same module stubs as the Bug-B test, but let the
        adapter receive a synthesised parsed_contract value so we can drive
        the APPROVAL_REQUEST branch under test.
        """
        import sys as _sys
        import types as _types

        def _fake_cleanup_approval(agent_type, session_id=None, preserve_nonces=None):
            captured["cleanup_agent_type"] = agent_type
            captured["cleanup_session_id"] = session_id
            captured["cleanup_preserve_nonces"] = preserve_nonces

        def _fake_consume_session_grants(session_id):
            captured["consumed_session_id"] = session_id
            return 0

        def _install_stub(module_name, attrs):
            module = _types.ModuleType(module_name)
            for k, v in attrs.items():
                setattr(module, k, v)
            monkeypatch.setitem(_sys.modules, module_name, module)

        _install_stub(
            "modules.agents.contract_validator",
            {
                "extract_commands_from_evidence": lambda *_a, **_k: [],
                # The single behavioural difference vs the Bug-B test: we
                # return the synthesised contract instead of None so the
                # APPROVAL_REQUEST extraction branch executes.
                "parse_contract": lambda *_a, **_k: parsed_contract,
                "requires_consolidation_report": lambda *_a, **_k: False,
                "validate": lambda *_a, **_k: _types.SimpleNamespace(
                    is_valid=True, error_message=""
                ),
                "validate_approval_request": lambda *_a, **_k: None,
                "validate_verbatim_outputs_consistency": lambda *_a, **_k: None,
                "_resolve_status": lambda *_a, **_k: "APPROVAL_REQUEST",
            },
        )
        _install_stub(
            "modules.agents.response_contract",
            {
                "save_validation_result": lambda *_a, **_k: None,
                "validate_response_contract": lambda *_a, **_k: _types.SimpleNamespace(
                    valid=True, errors=[], warnings=[]
                ),
                "resolve_agent_id": lambda *_a, **_k: "agent-id",
            },
        )
        _install_stub(
            "modules.agents.task_info_builder",
            {
                "build_task_info_from_hook_data": lambda hook_data, _agent_output: {
                    "agent": hook_data.get("agent_type", "unknown"),
                    "agent_id": hook_data.get("agent_id", "unknown"),
                    "task_id": "task-id",
                    "agent_transcript_path": hook_data.get(
                        "agent_transcript_path", ""
                    ),
                },
            },
        )
        _install_stub(
            "modules.agents.transcript_reader",
            {"read_transcript": lambda *_a, **_k: ""},
        )
        _install_stub(
            "modules.audit.workflow_auditor",
            {
                "audit": lambda *_a, **_k: None,
                "signal_gaia_analysis": lambda *_a, **_k: None,
            },
        )
        _install_stub(
            "modules.audit.workflow_recorder",
            {"record": lambda *_a, **_k: None},
        )
        _install_stub(
            "modules.context.context_writer",
            {
                "process_context_updates": lambda *_a, **_k: None,
                "process_update_contracts": lambda *_a, **_k: None,
            },
        )
        _install_stub(
            "modules.memory.episode_writer", {"write": lambda *_a, **_k: None}
        )
        _install_stub(
            "modules.security.approval_cleanup",
            {"cleanup": _fake_cleanup_approval},
        )
        _install_stub(
            "modules.security.approval_grants",
            {"consume_session_grants": _fake_consume_session_grants},
        )

    def _build_event(self, subagent_stop_payload):
        from adapters.types import DistributionChannel, HookEvent, HookEventType
        return HookEvent(
            event_type=HookEventType.SUBAGENT_STOP,
            session_id=subagent_stop_payload["session_id"],
            payload=subagent_stop_payload,
            channel=DistributionChannel.NPM,
        )

    def test_approval_request_contract_preserves_its_nonce(
        self, adapter, subagent_stop_payload, monkeypatch
    ):
        """A contract with plan_status=APPROVAL_REQUEST and an approval_id
        must produce preserve_nonces={approval_id} in the cleanup call.
        """
        captured = {}
        parsed_contract = {
            "agent_status": {
                "plan_status": "APPROVAL_REQUEST",
                "agent_id": "a12345abc",
            },
            "approval_request": {
                "approval_id": "preserved-nonce-deadbeef",
                "operation": "Bash",
                "exact_content": "kubectl apply -f manifest.yaml",
            },
        }

        self._install_module_stubs(monkeypatch, captured, parsed_contract)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-ghi789")
        monkeypatch.setattr(
            adapter,
            "_get_gaia_agent_names",
            lambda: [subagent_stop_payload["agent_type"]],
        )

        adapter.adapt_subagent_stop(self._build_event(subagent_stop_payload))

        assert captured.get("cleanup_preserve_nonces") == {"preserved-nonce-deadbeef"}, (
            "adapt_subagent_stop must extract approval_id from an "
            "APPROVAL_REQUEST contract and pass it as preserve_nonces to "
            "cleanup. Without this, the pending file is destroyed at "
            "SubagentStop and the user can no longer approve it."
        )
        assert captured.get("cleanup_session_id") == "sess-ghi789", (
            "Cleanup must also receive the session_id from the event "
            "(Phase 1 contract). Preserve-nonces alone is insufficient; "
            "the cleanup scan still needs to match the session."
        )

    def test_complete_contract_passes_no_preserve_nonces(
        self, adapter, subagent_stop_payload, monkeypatch
    ):
        """A normal COMPLETE contract carries no approval_id; cleanup must
        receive preserve_nonces=None so the legacy delete-all behaviour
        applies. This proves the preserve path is opt-in, not default.
        """
        captured = {}
        parsed_contract = {
            "agent_status": {
                "plan_status": "COMPLETE",
                "agent_id": "a99999abc",
            },
            "approval_request": None,
        }

        self._install_module_stubs(monkeypatch, captured, parsed_contract)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-ghi789")
        monkeypatch.setattr(
            adapter,
            "_get_gaia_agent_names",
            lambda: [subagent_stop_payload["agent_type"]],
        )

        adapter.adapt_subagent_stop(self._build_event(subagent_stop_payload))

        assert captured.get("cleanup_preserve_nonces") in (None, set()), (
            "A non-APPROVAL_REQUEST contract must not preserve any nonce. "
            "Passing an empty set or None is acceptable; passing a "
            "populated set would leak pendings across cleanup cycles."
        )

    def test_approval_request_without_approval_id_does_not_preserve(
        self, adapter, subagent_stop_payload, monkeypatch
    ):
        """A malformed APPROVAL_REQUEST contract (missing approval_id) must
        not propagate a falsy value as a preserved nonce. Better to clean
        than to keep junk that nobody can reference.
        """
        captured = {}
        parsed_contract = {
            "agent_status": {
                "plan_status": "APPROVAL_REQUEST",
                "agent_id": "amalformed",
            },
            # approval_request present but no approval_id field
            "approval_request": {"operation": "Bash"},
        }

        self._install_module_stubs(monkeypatch, captured, parsed_contract)
        monkeypatch.setenv("CLAUDE_SESSION_ID", "sess-ghi789")
        monkeypatch.setattr(
            adapter,
            "_get_gaia_agent_names",
            lambda: [subagent_stop_payload["agent_type"]],
        )

        adapter.adapt_subagent_stop(self._build_event(subagent_stop_payload))

        assert captured.get("cleanup_preserve_nonces") in (None, set()), (
            "Missing approval_id must NOT result in preserving '' or "
            "another falsy placeholder -- the cleanup-skip predicate uses "
            "set membership and a junk nonce would silently shield "
            "unrelated pendings."
        )


# ============================================================================
# T004: format_validation_response tests
# ============================================================================


class TestFormatValidationResponse:
    """Test format_validation_response output shape."""

    def test_allow_response(self, adapter):
        """Allowed command produces permissionDecision: allow."""
        result = ValidationResult(allowed=True, reason="Safe command", tier="T0")
        resp = adapter.format_validation_response(result)

        assert isinstance(resp, HookResponse)
        assert resp.exit_code == 0
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "PreToolUse"
        assert hook_out["permissionDecision"] == "allow"
        assert hook_out["permissionDecisionReason"] == "Safe command"
        assert "updatedInput" not in hook_out

    def test_deny_with_nonce(self, adapter):
        """Denied mutative command includes nonce in response, exit 0."""
        result = ValidationResult(
            allowed=False,
            reason="Mutative operation requires approval",
            tier="T3",
            nonce="abc123def456",
        )
        resp = adapter.format_validation_response(result)

        assert resp.exit_code == 0  # Corrective deny, not permanent block
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["permissionDecision"] == "deny"
        assert "Mutative" in hook_out["permissionDecisionReason"]

    def test_permanent_block(self, adapter):
        """Permanently blocked command produces exit code 2."""
        result = ValidationResult(
            allowed=False,
            reason="Command permanently blocked by policy",
            tier="BLOCKED",
            nonce=None,
        )
        resp = adapter.format_validation_response(result)

        assert resp.exit_code == 2
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["permissionDecision"] == "deny"

    def test_allow_with_updated_input(self, adapter):
        """Allowed with modified input includes updatedInput."""
        result = ValidationResult(
            allowed=True,
            reason="Footer stripped",
            tier="T0",
            modified_input={"command": "git status"},
        )
        resp = adapter.format_validation_response(result)

        assert resp.exit_code == 0
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["permissionDecision"] == "allow"
        assert hook_out["updatedInput"] == {"command": "git status"}

    def test_deny_without_nonce_not_blocked_tier(self, adapter):
        """Deny without nonce but not BLOCKED tier -> exit 0 (corrective)."""
        result = ValidationResult(
            allowed=False,
            reason="GitOps policy violation",
            tier="T3",
            nonce=None,
        )
        resp = adapter.format_validation_response(result)
        assert resp.exit_code == 0


# ============================================================================
# T004: format_ask_response tests
# ============================================================================


class TestFormatAskResponse:
    """Test format_ask_response for interactive permission."""

    def test_ask_response(self, adapter):
        """Ask response produces permissionDecision: ask."""
        resp = adapter.format_ask_response("Should I proceed with this operation?")

        assert isinstance(resp, HookResponse)
        assert resp.exit_code == 0
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["permissionDecision"] == "ask"
        assert hook_out["permissionDecisionReason"] == "Should I proceed with this operation?"


# ============================================================================
# T004: format_completion_response tests
# ============================================================================


class TestFormatCompletionResponse:
    """Test format_completion_response for SubagentStop."""

    def test_valid_completion(self, adapter):
        """Valid contract produces minimal response."""
        result = CompletionResult(
            contract_valid=True,
            episode_id="ep-abc-123",
            context_updated=False,
        )
        resp = adapter.format_completion_response(result)

        assert isinstance(resp, HookResponse)
        assert resp.exit_code == 0
        assert resp.output["contract_valid"] is True
        assert resp.output["anomalies_detected"] == 0
        assert resp.output["episode_id"] == "ep-abc-123"
        assert "repair_needed" not in resp.output

    def test_needs_repair(self, adapter):
        """Invalid contract with repair needed includes anomalies."""
        result = CompletionResult(
            contract_valid=False,
            anomalies=[
                {"type": "missing_status", "severity": "critical"},
                {"type": "missing_evidence", "severity": "warning"},
            ],
            repair_needed=True,
        )
        resp = adapter.format_completion_response(result)

        assert resp.exit_code == 0
        assert resp.output["contract_valid"] is False
        assert resp.output["anomalies_detected"] == 2
        assert resp.output["repair_needed"] is True
        assert len(resp.output["anomalies"]) == 2

    def test_context_updated(self, adapter):
        """Context updated flag appears when set."""
        result = CompletionResult(context_updated=True)
        resp = adapter.format_completion_response(result)
        assert resp.output["context_updated"] is True

    def test_default_completion(self, adapter):
        """Default CompletionResult produces clean response."""
        result = CompletionResult()
        resp = adapter.format_completion_response(result)
        assert resp.output["contract_valid"] is True
        assert resp.output["anomalies_detected"] == 0
        assert "episode_id" not in resp.output
        assert "repair_needed" not in resp.output
        assert "context_updated" not in resp.output


# ============================================================================
# T004: format_context_response tests
# ============================================================================


class TestFormatContextResponse:
    """Test format_context_response for SubagentStart."""

    def test_with_context(self, adapter):
        """Context injection produces hookSpecificOutput with additionalContext."""
        result = ContextResult(
            context_injected=True,
            additional_context="# Project Context\nRegion: us-east4\nCluster: dev",
            sections_provided=["project_identity", "environment"],
        )
        resp = adapter.format_context_response(result)

        assert resp.exit_code == 0
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "SubagentStart"
        assert "additionalContext" in hook_out
        assert "Project Context" in hook_out["additionalContext"]
        assert resp.output["sections_provided"] == ["project_identity", "environment"]

    def test_no_context(self, adapter):
        """No context injection produces hookSpecificOutput without additionalContext."""
        result = ContextResult()
        resp = adapter.format_context_response(result)
        assert resp.exit_code == 0
        hook_out = resp.output["hookSpecificOutput"]
        assert hook_out["hookEventName"] == "SubagentStart"
        assert "additionalContext" not in hook_out

    def test_injected_but_no_content(self, adapter):
        """context_injected=True but no additional_context omits the field."""
        result = ContextResult(context_injected=True, additional_context=None)
        resp = adapter.format_context_response(result)
        hook_out = resp.output["hookSpecificOutput"]
        assert "additionalContext" not in hook_out


# ============================================================================
# T004: detect_channel tests
# ============================================================================


class TestDetectChannel:
    """Test distribution channel detection."""

    def test_npm_default(self, adapter):
        """Default channel (no env var) is NPM."""
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        assert adapter.detect_channel() == DistributionChannel.NPM

    def test_plugin_with_env_var(self):
        """CLAUDE_PLUGIN_ROOT env var triggers PLUGIN channel."""
        os.environ["CLAUDE_PLUGIN_ROOT"] = "/opt/plugins/gaia-ops"
        try:
            a = ClaudeCodeAdapter()
            assert a.detect_channel() == DistributionChannel.PLUGIN
        finally:
            del os.environ["CLAUDE_PLUGIN_ROOT"]

    def test_plugin_root_path(self):
        """_get_plugin_root returns Path from env var."""
        os.environ["CLAUDE_PLUGIN_ROOT"] = "/opt/plugins/gaia-ops"
        try:
            a = ClaudeCodeAdapter()
            root = a._get_plugin_root()
            assert root == Path("/opt/plugins/gaia-ops")
        finally:
            del os.environ["CLAUDE_PLUGIN_ROOT"]

    def test_no_plugin_root(self, adapter):
        """_get_plugin_root returns None when env var is not set."""
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
        assert adapter._get_plugin_root() is None

    def test_empty_plugin_root(self, adapter):
        """Empty CLAUDE_PLUGIN_ROOT is treated as not set."""
        os.environ["CLAUDE_PLUGIN_ROOT"] = ""
        try:
            assert adapter.detect_channel() == DistributionChannel.NPM
        finally:
            del os.environ["CLAUDE_PLUGIN_ROOT"]


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_parse_event_none_input(self, adapter):
        """None input raises ValueError (not TypeError)."""
        with pytest.raises(ValueError, match="Empty stdin data"):
            adapter.parse_event(None)

    def test_parse_pre_tool_use_empty_tool_input(self, adapter):
        """Empty tool_input produces empty command."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {},
            "session_id": "s1",
        }
        req = adapter.parse_pre_tool_use(payload)
        assert req.command == ""

    def test_parse_post_tool_use_non_integer_exit_code(self, adapter):
        """Non-integer exit_code falls through to default 0."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {"output": "file.txt", "exit_code": "not_a_number"},
            "session_id": "s1",
        }
        result = adapter.parse_post_tool_use(payload)
        # exit_code is whatever was in the JSON; ToolResult stores it as-is
        assert result.exit_code == "not_a_number"

    def test_format_validation_response_empty_reason(self, adapter):
        """Empty reason string is still included."""
        result = ValidationResult(allowed=True, reason="")
        resp = adapter.format_validation_response(result)
        assert resp.output["hookSpecificOutput"]["permissionDecisionReason"] == ""

    def test_roundtrip_parse_and_format(self, adapter, pre_tool_use_bash_payload):
        """Parse event -> extract request -> validate -> format response."""
        # Parse
        stdin_data = json.dumps(pre_tool_use_bash_payload)
        event = adapter.parse_event(stdin_data)

        # Extract
        req = adapter.parse_pre_tool_use(event.payload)
        assert req.tool_name == "Bash"
        assert req.command == "git status"

        # Validate (mock business logic)
        validation = ValidationResult(allowed=True, reason="Safe read-only command", tier="T0")

        # Format
        resp = adapter.format_validation_response(validation)
        assert resp.exit_code == 0
        assert resp.output["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_all_p0_event_types_parseable(self, adapter):
        """All P0 event types can be parsed."""
        for event_name in ["PreToolUse", "PostToolUse", "SubagentStop"]:
            stdin_data = json.dumps({
                "hook_event_name": event_name,
                "session_id": "s1",
            })
            event = adapter.parse_event(stdin_data)
            assert event.event_type.value == event_name


# ============================================================================
# Workspace Memory injection for subagent dispatch
# ============================================================================
#
# Task #4 of the Project Context refactor: subagents must receive the same
# ## Workspace Memory block the orchestrator gets at SessionStart. Injection
# happens inside _adapt_task via _append_workspace_memory, which reuses
# session_manifest.build_workspace_memory_block as the single source of truth.
# ============================================================================


class TestAppendWorkspaceMemory:
    """Tests for _append_workspace_memory helper -- the module-level
    function called by _adapt_task to inject curated workspace memory
    into the subagent's additionalContext.
    """

    def test_appends_block_when_memory_present(self, monkeypatch):
        """When build_workspace_memory_block returns content, it is appended
        to the original context with a blank-line separator and the
        ## Workspace Memory header survives intact.
        """
        from adapters import claude_code as cc

        sentinel = (
            "## Workspace Memory (me)\n\n"
            "Atoms:\n- atom_x: curated knowledge"
        )

        import modules.session.session_manifest as sm
        monkeypatch.setattr(
            sm, "build_workspace_memory_block", lambda *a, **kw: sentinel
        )

        base_context = "# Project Context\n\nfoo: bar"
        result = cc._append_workspace_memory(base_context)

        assert "## Workspace Memory" in result, (
            "Header must survive intact so subagents anchor on the same "
            "marker the orchestrator does"
        )
        assert "atom_x: curated knowledge" in result
        assert result.startswith(base_context), (
            "Original context must be preserved at the start"
        )
        assert "\n\n## Workspace Memory" in result, (
            "Blocks must be separated by exactly one blank line to keep "
            "markdown structure clean"
        )

    def test_empty_block_leaves_context_unchanged(self, monkeypatch):
        """When the workspace has no curated memory (CLI emits empty
        string), the original context is returned verbatim -- no header
        with placeholder, no dangling newlines."""
        from adapters import claude_code as cc
        import modules.session.session_manifest as sm
        monkeypatch.setattr(
            sm, "build_workspace_memory_block", lambda *a, **kw: ""
        )

        base_context = "# Project Context\n\nfoo: bar"
        result = cc._append_workspace_memory(base_context)

        assert result == base_context, (
            "Empty memory must not pollute the context with separators or "
            "headers -- absence is the correct signal"
        )
        assert "## Workspace Memory" not in result

    def test_cli_failure_is_silent_and_safe(self, monkeypatch):
        """When build_workspace_memory_block raises (subprocess timeout,
        DB error, anything), the dispatch must continue and return the
        original context. A subagent must never fail to launch because
        memory injection misbehaved.
        """
        from adapters import claude_code as cc
        import modules.session.session_manifest as sm

        def _boom(*a, **kw):
            raise RuntimeError("simulated CLI failure")

        monkeypatch.setattr(sm, "build_workspace_memory_block", _boom)

        base_context = "# Project Context\n\nfoo: bar"
        # Must not raise
        result = cc._append_workspace_memory(base_context)
        assert result == base_context, (
            "On CLI failure the helper must degrade gracefully -- the "
            "fail-safe contract is the whole point of the wrapper"
        )

    def test_empty_input_with_memory_returns_only_memory(self, monkeypatch):
        """When the caller passes an empty context but memory exists,
        the memory block is returned alone (no leading blank line).
        Guards against the dispatch path where build_project_context
        returned None and fallback extraction found nothing -- memory
        should still surface.
        """
        from adapters import claude_code as cc
        import modules.session.session_manifest as sm

        sentinel = "## Workspace Memory (me)\n\nAtoms:\n- a: b"
        monkeypatch.setattr(
            sm, "build_workspace_memory_block", lambda *a, **kw: sentinel
        )

        result = cc._append_workspace_memory("")
        assert result == sentinel, (
            "Empty input must not leave a leading \\n\\n separator -- the "
            "block stands on its own"
        )
