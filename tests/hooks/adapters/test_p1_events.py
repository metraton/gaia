#!/usr/bin/env python3
"""
Tests for P1 Event Adapters (SessionStart).

Validates:
1. adapt_session_start with startup -> should_scan=True
2. adapt_session_start with resume -> should_scan=False, should_refresh=True
3. adapt_session_start with clear/compact -> both False
4. SessionStart hook script runs without error (subprocess test)
5. hooks.json has SessionStart entry
6. format_bootstrap_response and format_context_response integration

Note: UserPromptSubmit was migrated from static echo to a real Python hook
in Phase C (dynamic identity injection). The hook injects mode-specific
identity context via additionalContext.
"""

import sys
import json
import os
import subprocess
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import (
    BootstrapResult,
    HookEventType,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def adapter():
    """Fresh ClaudeCodeAdapter instance with clean env."""
    old_val = os.environ.pop("CLAUDE_PLUGIN_ROOT", None)
    a = ClaudeCodeAdapter()
    yield a
    if old_val is not None:
        os.environ["CLAUDE_PLUGIN_ROOT"] = old_val
    else:
        os.environ.pop("CLAUDE_PLUGIN_ROOT", None)


@pytest.fixture
def session_start_payload():
    """Realistic SessionStart event from Claude Code."""
    return {
        "hook_event_name": "SessionStart",
        "session_id": "sess-start-001",
        "session_type": "startup",
    }


# ============================================================================
# T037: adapt_session_start tests
# ============================================================================


class TestAdaptSessionStart:
    """Test adapt_session_start with different session types."""

    def test_startup_should_scan_and_refresh(self, adapter):
        """Startup session triggers both scan and refresh."""
        result = adapter.adapt_session_start({"session_type": "startup"})

        assert isinstance(result, BootstrapResult)
        assert result.should_scan is True
        assert result.should_refresh is True
        assert result.session_type == "startup"

    def test_resume_should_refresh_not_scan(self, adapter):
        """Resume session triggers refresh but not scan."""
        result = adapter.adapt_session_start({"session_type": "resume"})

        assert isinstance(result, BootstrapResult)
        assert result.should_scan is False
        assert result.should_refresh is True
        assert result.session_type == "resume"

    def test_clear_no_scan_no_refresh(self, adapter):
        """Clear session triggers neither scan nor refresh."""
        result = adapter.adapt_session_start({"session_type": "clear"})

        assert isinstance(result, BootstrapResult)
        assert result.should_scan is False
        assert result.should_refresh is False
        assert result.session_type == "clear"

    def test_compact_no_scan_no_refresh(self, adapter):
        """Compact session triggers neither scan nor refresh."""
        result = adapter.adapt_session_start({"session_type": "compact"})

        assert isinstance(result, BootstrapResult)
        assert result.should_scan is False
        assert result.should_refresh is False
        assert result.session_type == "compact"

    def test_missing_session_type_defaults_startup(self, adapter):
        """Missing session_type defaults to startup."""
        result = adapter.adapt_session_start({})

        assert result.should_scan is True
        assert result.should_refresh is True
        assert result.session_type == "startup"

    def test_parse_event_session_start(self, adapter):
        """SessionStart event type is parseable."""
        stdin_data = json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": "s1",
            "session_type": "startup",
        })
        event = adapter.parse_event(stdin_data)
        assert event.event_type == HookEventType.SESSION_START
        assert event.session_id == "s1"


# ============================================================================
# P1: format_bootstrap_response tests
# ============================================================================


class TestFormatBootstrapResponse:
    """Test format_bootstrap_response for SessionStart."""

    def test_startup_response(self, adapter):
        """Startup bootstrap produces expected fields."""
        result = BootstrapResult(
            should_scan=True,
            should_refresh=True,
            session_type="startup",
        )
        resp = adapter.format_bootstrap_response(result)

        assert resp.exit_code == 0
        assert resp.output["session_type"] == "startup"
        assert resp.output["should_scan"] is True
        assert resp.output["should_refresh"] is True

    def test_resume_response(self, adapter):
        """Resume bootstrap produces correct flags."""
        result = BootstrapResult(
            should_scan=False,
            should_refresh=True,
            session_type="resume",
        )
        resp = adapter.format_bootstrap_response(result)

        assert resp.exit_code == 0
        assert resp.output["should_scan"] is False
        assert resp.output["should_refresh"] is True

    def test_with_scan_results(self, adapter):
        """Bootstrap with scan results includes extra fields."""
        result = BootstrapResult(
            should_scan=True,
            should_refresh=True,
            session_type="startup",
            project_scanned=True,
            context_path=Path("/tmp/project-context.json"),
            tools_detected=["terraform", "kubectl"],
        )
        resp = adapter.format_bootstrap_response(result)

        assert resp.output["project_scanned"] is True
        assert resp.output["context_path"] == "/tmp/project-context.json"
        assert resp.output["tools_detected"] == ["terraform", "kubectl"]


# ============================================================================
# T038: SessionStart hook script subprocess test
# ============================================================================


class TestSessionStartHookScript:
    """Test session_start.py hook script via subprocess."""

    def test_runs_without_error(self, tmp_path):
        """SessionStart hook script accepts stdin and exits 0."""
        hook_script = HOOKS_DIR / "session_start.py"
        if not hook_script.exists():
            pytest.skip("session_start.py not found")

        payload = json.dumps({
            "hook_event_name": "SessionStart",
            "session_id": "test-sess-001",
            "session_type": "startup",
        })

        # Run in tmp_path so .claude dir lookups don't interfere
        env = os.environ.copy()
        env.pop("CLAUDE_PLUGIN_ROOT", None)

        result = subprocess.run(
            [sys.executable, str(hook_script)],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=10,
            env=env,
        )

        assert result.returncode == 0, (
            f"session_start.py failed with exit code {result.returncode}\n"
            f"stderr: {result.stderr}\n"
            f"stdout: {result.stdout}"
        )

        # Output should be valid JSON
        output = json.loads(result.stdout)
        assert "session_type" in output
        assert output["session_type"] == "startup"


# ============================================================================
# T041: hooks.json has P1 entries
# ============================================================================


class TestHooksJsonP1Entries:
    """Verify hooks.json contains SessionStart (UserPromptSubmit removed -- now static echo in settings.json)."""

    @pytest.fixture
    def hooks_config(self):
        """Load hooks.json."""
        hooks_json_path = HOOKS_DIR / "hooks.json"
        assert hooks_json_path.exists(), "hooks.json not found"
        with open(hooks_json_path) as f:
            return json.load(f)

    def test_session_start_entry(self, hooks_config):
        """hooks.json has a SessionStart entry."""
        hooks = hooks_config.get("hooks", {})
        assert "SessionStart" in hooks, "SessionStart not found in hooks.json"

        session_start = hooks["SessionStart"]
        assert len(session_start) >= 1
        assert session_start[0]["matcher"] == "startup"
        assert "session_start.py" in session_start[0]["hooks"][0]["command"]

    def test_user_prompt_submit_in_hooks_json(self, hooks_config):
        """UserPromptSubmit must be in hooks.json (Phase C: dynamic identity injection)."""
        hooks = hooks_config.get("hooks", {})
        assert "UserPromptSubmit" in hooks, (
            "UserPromptSubmit should be in hooks.json -- "
            "Phase C migrated it from static echo to dynamic identity script"
        )

    def test_p0_events_still_present(self, hooks_config):
        """P0 events (PreToolUse, PostToolUse, SubagentStop) are unchanged."""
        hooks = hooks_config.get("hooks", {})
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks
        assert "SubagentStop" in hooks

    def test_hooks_json_is_valid(self, hooks_config):
        """hooks.json is valid JSON with expected structure."""
        assert "hooks" in hooks_config
        # All entries should have hooks arrays
        for event_name, entries in hooks_config["hooks"].items():
            assert isinstance(entries, list), f"{event_name} should be a list"
            for entry in entries:
                assert "hooks" in entry, f"{event_name} entry missing hooks array"
                for hook in entry["hooks"]:
                    assert "type" in hook
                    assert "command" in hook
                    assert hook["type"] == "command"
                    assert "${CLAUDE_PLUGIN_ROOT}" in hook["command"]