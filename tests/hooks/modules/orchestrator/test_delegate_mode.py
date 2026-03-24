"""Tests for orchestrator delegate mode enforcement."""

import os
import unittest
from unittest.mock import patch

import sys
from pathlib import Path

# Add hooks directory to path for module resolution
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.orchestrator.delegate_mode import (
    ORCHESTRATOR_ALLOWED_TOOLS,
    DelegateModeResult,
    _read_settings_delegate_mode,
    check_delegate_mode,
    is_delegate_mode_enabled,
    is_orchestrator_context,
)

# Patch target for the disk fallback so tests don't read the real settings.json
_DISK_FALLBACK = "modules.orchestrator.delegate_mode._read_settings_delegate_mode"


class TestIsDelegateModeEnabled(unittest.TestCase):
    """Tests for is_delegate_mode_enabled()."""

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_enabled_true(self):
        self.assertTrue(is_delegate_mode_enabled())

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "1"}, clear=False)
    def test_enabled_1(self):
        self.assertTrue(is_delegate_mode_enabled())

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "yes"}, clear=False)
    def test_enabled_yes(self):
        self.assertTrue(is_delegate_mode_enabled())

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "on"}, clear=False)
    def test_enabled_on(self):
        self.assertTrue(is_delegate_mode_enabled())

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "TRUE"}, clear=False)
    def test_enabled_case_insensitive(self):
        self.assertTrue(is_delegate_mode_enabled())

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "false"}, clear=False)
    def test_disabled_false(self):
        self.assertFalse(is_delegate_mode_enabled())

    @patch(_DISK_FALLBACK, return_value=None)
    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": ""}, clear=False)
    def test_disabled_empty(self, _mock_disk):
        self.assertFalse(is_delegate_mode_enabled())

    @patch(_DISK_FALLBACK, return_value=None)
    @patch.dict(os.environ, {}, clear=False)
    def test_disabled_missing(self, _mock_disk):
        # Remove the key if it exists
        os.environ.pop("ORCHESTRATOR_DELEGATE_MODE", None)
        self.assertFalse(is_delegate_mode_enabled())

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "0"}, clear=False)
    def test_disabled_zero(self):
        self.assertFalse(is_delegate_mode_enabled())

    # ── Settings.json fallback ──

    @patch(_DISK_FALLBACK, return_value="true")
    @patch.dict(os.environ, {}, clear=False)
    def test_fallback_to_settings_json(self, _mock_disk):
        """When env var is absent, reads from settings.json."""
        os.environ.pop("ORCHESTRATOR_DELEGATE_MODE", None)
        self.assertTrue(is_delegate_mode_enabled())

    @patch(_DISK_FALLBACK, return_value="false")
    @patch.dict(os.environ, {}, clear=False)
    def test_fallback_settings_disabled(self, _mock_disk):
        """settings.json says false, env var absent -> disabled."""
        os.environ.pop("ORCHESTRATOR_DELEGATE_MODE", None)
        self.assertFalse(is_delegate_mode_enabled())

    @patch(_DISK_FALLBACK, return_value="true")
    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "false"}, clear=False)
    def test_env_var_overrides_settings(self, _mock_disk):
        """Env var takes precedence over settings.json."""
        self.assertFalse(is_delegate_mode_enabled())


class TestIsOrchestratorContext(unittest.TestCase):
    """Tests for is_orchestrator_context()."""

    def test_main_session_no_agent_id(self):
        """Main session: agent_id absent from payload."""
        payload = {
            "session_id": "abc123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        self.assertTrue(is_orchestrator_context(payload))

    def test_main_session_empty_agent_id(self):
        """Main session: agent_id present but empty string."""
        payload = {
            "session_id": "abc123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent_id": "",
        }
        self.assertTrue(is_orchestrator_context(payload))

    def test_subagent_has_agent_id(self):
        """Subagent: agent_id is present and non-empty."""
        payload = {
            "session_id": "abc123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "agent_id": "a12345f",
            "agent_type": "terraform-architect",
        }
        self.assertFalse(is_orchestrator_context(payload))


class TestCheckDelegateMode(unittest.TestCase):
    """Tests for the main check_delegate_mode() entry point."""

    def _orchestrator_payload(self, tool_name: str) -> dict:
        """Build a payload simulating the main session (no agent_id)."""
        return {
            "session_id": "abc123",
            "tool_name": tool_name,
            "tool_input": {},
        }

    def _subagent_payload(self, tool_name: str) -> dict:
        """Build a payload simulating a subagent."""
        return {
            "session_id": "abc123",
            "tool_name": tool_name,
            "tool_input": {},
            "agent_id": "a12345f",
            "agent_type": "terraform-architect",
        }

    # ── Delegate mode disabled ──

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "false"}, clear=False)
    def test_disabled_allows_everything(self):
        """When delegate mode is off, all tools pass through."""
        result = check_delegate_mode("Bash", self._orchestrator_payload("Bash"))
        self.assertFalse(result.blocked)

    # ── Delegate mode enabled, orchestrator context ──

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_bash_for_orchestrator(self):
        result = check_delegate_mode("Bash", self._orchestrator_payload("Bash"))
        self.assertTrue(result.blocked)
        self.assertIn("DELEGATE MODE", result.reason)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_read_for_orchestrator(self):
        result = check_delegate_mode("Read", self._orchestrator_payload("Read"))
        self.assertTrue(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_edit_for_orchestrator(self):
        result = check_delegate_mode("Edit", self._orchestrator_payload("Edit"))
        self.assertTrue(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_write_for_orchestrator(self):
        result = check_delegate_mode("Write", self._orchestrator_payload("Write"))
        self.assertTrue(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_glob_for_orchestrator(self):
        result = check_delegate_mode("Glob", self._orchestrator_payload("Glob"))
        self.assertTrue(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_grep_for_orchestrator(self):
        result = check_delegate_mode("Grep", self._orchestrator_payload("Grep"))
        self.assertTrue(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_websearch_for_orchestrator(self):
        result = check_delegate_mode("WebSearch", self._orchestrator_payload("WebSearch"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_webfetch_for_orchestrator(self):
        result = check_delegate_mode("WebFetch", self._orchestrator_payload("WebFetch"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_blocks_notebookedit_for_orchestrator(self):
        result = check_delegate_mode("NotebookEdit", self._orchestrator_payload("NotebookEdit"))
        self.assertTrue(result.blocked)

    # ── Delegate mode enabled, allowed tools ──

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_agent_for_orchestrator(self):
        result = check_delegate_mode("Agent", self._orchestrator_payload("Agent"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_task_for_orchestrator(self):
        result = check_delegate_mode("Task", self._orchestrator_payload("Task"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_sendmessage_for_orchestrator(self):
        result = check_delegate_mode("SendMessage", self._orchestrator_payload("SendMessage"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_skill_for_orchestrator(self):
        result = check_delegate_mode("Skill", self._orchestrator_payload("Skill"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_taskcreate_for_orchestrator(self):
        result = check_delegate_mode("TaskCreate", self._orchestrator_payload("TaskCreate"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_taskupdate_for_orchestrator(self):
        result = check_delegate_mode("TaskUpdate", self._orchestrator_payload("TaskUpdate"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_tasklist_for_orchestrator(self):
        result = check_delegate_mode("TaskList", self._orchestrator_payload("TaskList"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_taskget_for_orchestrator(self):
        result = check_delegate_mode("TaskGet", self._orchestrator_payload("TaskGet"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_allows_toolsearch_for_orchestrator(self):
        result = check_delegate_mode("ToolSearch", self._orchestrator_payload("ToolSearch"))
        self.assertFalse(result.blocked)

    # ── Delegate mode enabled, subagent context ──

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_subagent_bash_allowed(self):
        """Subagents are never restricted by delegate mode."""
        result = check_delegate_mode("Bash", self._subagent_payload("Bash"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_subagent_read_allowed(self):
        result = check_delegate_mode("Read", self._subagent_payload("Read"))
        self.assertFalse(result.blocked)

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_subagent_edit_allowed(self):
        result = check_delegate_mode("Edit", self._subagent_payload("Edit"))
        self.assertFalse(result.blocked)

    # ── Case insensitivity ──

    @patch.dict(os.environ, {"ORCHESTRATOR_DELEGATE_MODE": "true"}, clear=False)
    def test_tool_name_case_insensitive(self):
        """Tool names are matched case-insensitively."""
        # "BASH" should still be blocked
        result = check_delegate_mode("BASH", self._orchestrator_payload("BASH"))
        self.assertTrue(result.blocked)

        # "agent" (lowercase) should be allowed
        result = check_delegate_mode("agent", self._orchestrator_payload("agent"))
        self.assertFalse(result.blocked)


class TestAllowedToolsCompleteness(unittest.TestCase):
    """Verify the allowed tools set covers the expected tools."""

    def test_dispatch_tools_present(self):
        self.assertIn("agent", ORCHESTRATOR_ALLOWED_TOOLS)
        self.assertIn("task", ORCHESTRATOR_ALLOWED_TOOLS)
        self.assertIn("sendmessage", ORCHESTRATOR_ALLOWED_TOOLS)

    def test_skill_tool_present(self):
        self.assertIn("skill", ORCHESTRATOR_ALLOWED_TOOLS)

    def test_task_management_tools_present(self):
        for tool in ("taskcreate", "taskupdate", "tasklist", "taskget"):
            self.assertIn(tool, ORCHESTRATOR_ALLOWED_TOOLS)

    def test_web_research_tools_present(self):
        """WebSearch and WebFetch are read-only T0 tools allowed for orchestrator."""
        self.assertIn("websearch", ORCHESTRATOR_ALLOWED_TOOLS)
        self.assertIn("webfetch", ORCHESTRATOR_ALLOWED_TOOLS)

    def test_investigation_tools_absent(self):
        """Ensure investigation tools are NOT in the allowed set."""
        for tool in ("bash", "read", "edit", "write", "glob", "grep",
                     "notebookedit"):
            self.assertNotIn(tool, ORCHESTRATOR_ALLOWED_TOOLS)


if __name__ == "__main__":
    unittest.main()
