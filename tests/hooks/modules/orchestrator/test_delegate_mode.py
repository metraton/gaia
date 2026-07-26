"""Tests for orchestrator delegate mode enforcement."""

import unittest

import sys
from pathlib import Path

# Add hooks directory to path for module resolution
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.orchestrator.delegate_mode import (
    ORCHESTRATOR_AGENT_TYPES,
    ORCHESTRATOR_ALLOWED_TOOLS,
    DelegateModeResult,
    SessionRole,
    check_delegate_mode,
    classify_session_role,
    is_orchestrator_context,
)


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
            "agent_id": "a12345f0f1e2d3c4b",
            "agent_type": "platform-architect",
        }
        self.assertFalse(is_orchestrator_context(payload))


class TestClassifySessionRole(unittest.TestCase):
    """The (agent_id, agent_type) taxonomy behind is_orchestrator_context().

    The harness documents that agent_id is "absent for the main thread, even in
    --agent sessions", so its absence alone cannot mean "orchestrator". These
    cases pin the whole cross product, including the two that must never
    collapse into each other: a named specialist's main thread and the
    orchestrator's own (also named) main thread.
    """

    @staticmethod
    def _payload(**identity) -> dict:
        return {
            "session_id": "abc123",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            **identity,
        }

    def test_dispatched_subagent(self):
        role = classify_session_role(
            self._payload(agent_id="a12345f0f1e2d3c4b", agent_type="developer")
        )
        self.assertIs(role, SessionRole.SUBAGENT)

    def test_unnamed_main_thread_is_orchestrator(self):
        """No agent_id and no agent_type: a plain main session."""
        self.assertIs(classify_session_role(self._payload()), SessionRole.ORCHESTRATOR)

    def test_empty_identity_strings_are_orchestrator(self):
        """Empty strings are absence, not identity."""
        role = classify_session_role(self._payload(agent_id="", agent_type=""))
        self.assertIs(role, SessionRole.ORCHESTRATOR)

    def test_named_orchestrator_main_thread_is_orchestrator(self):
        """`agent: gaia-orchestrator` in settings makes the orchestrator's own
        main thread carry an agent_type. It is still the orchestrator."""
        role = classify_session_role(self._payload(agent_type="gaia-orchestrator"))
        self.assertIs(role, SessionRole.ORCHESTRATOR)

    def test_named_orchestrator_bare_spelling(self):
        role = classify_session_role(self._payload(agent_type="orchestrator"))
        self.assertIs(role, SessionRole.ORCHESTRATOR)

    def test_named_orchestrator_case_and_padding_insensitive(self):
        role = classify_session_role(self._payload(agent_type="  Gaia-Orchestrator "))
        self.assertIs(role, SessionRole.ORCHESTRATOR)

    def test_named_specialist_main_thread(self):
        """`claude --agent developer`: main thread, no agent_id, not the
        orchestrator. This is the case the old `not agent_id` proxy got wrong."""
        role = classify_session_role(self._payload(agent_type="developer"))
        self.assertIs(role, SessionRole.NAMED_SPECIALIST)

    def test_named_operator_is_a_specialist_not_a_curator(self):
        """gaia-operator is a curator for DB writes but a specialist here: the
        orchestrator identity set must not be widened into the curator set."""
        role = classify_session_role(self._payload(agent_type="gaia-operator"))
        self.assertIs(role, SessionRole.NAMED_SPECIALIST)

    def test_unknown_agent_type_is_not_the_orchestrator(self):
        """The orchestrator is known by name, so an unrecognized name is not it."""
        role = classify_session_role(self._payload(agent_type="general-purpose"))
        self.assertIs(role, SessionRole.NAMED_SPECIALIST)

    def test_orchestrator_identities_exclude_specialists(self):
        for name in ("developer", "gaia-operator", "gaia-system", "general-purpose"):
            self.assertNotIn(name, ORCHESTRATOR_AGENT_TYPES)


class TestCheckDelegateMode(unittest.TestCase):
    """Tests for the main check_delegate_mode() entry point.

    Delegate mode is always active when GAIA is installed.
    """

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
            "agent_id": "a12345f0f1e2d3c4b",
            "agent_type": "platform-architect",
        }

    # -- Orchestrator context: blocked tools --

    def test_blocks_bash_for_orchestrator(self):
        result = check_delegate_mode("Bash", self._orchestrator_payload("Bash"))
        self.assertTrue(result.blocked)
        self.assertIn("DELEGATION REQUIRED", result.reason)

    def test_allows_read_for_orchestrator(self):
        """Read is the one direct evidence tool granted to the orchestrator."""
        result = check_delegate_mode("Read", self._orchestrator_payload("Read"))
        self.assertFalse(result.blocked)

    def test_blocks_edit_for_orchestrator(self):
        result = check_delegate_mode("Edit", self._orchestrator_payload("Edit"))
        self.assertTrue(result.blocked)

    def test_blocks_write_for_orchestrator(self):
        result = check_delegate_mode("Write", self._orchestrator_payload("Write"))
        self.assertTrue(result.blocked)

    def test_blocks_glob_for_orchestrator(self):
        result = check_delegate_mode("Glob", self._orchestrator_payload("Glob"))
        self.assertTrue(result.blocked)

    def test_blocks_grep_for_orchestrator(self):
        result = check_delegate_mode("Grep", self._orchestrator_payload("Grep"))
        self.assertTrue(result.blocked)

    def test_blocks_notebookedit_for_orchestrator(self):
        result = check_delegate_mode("NotebookEdit", self._orchestrator_payload("NotebookEdit"))
        self.assertTrue(result.blocked)

    # -- Orchestrator context: allowed tools --

    def test_allows_agent_for_orchestrator(self):
        result = check_delegate_mode("Agent", self._orchestrator_payload("Agent"))
        self.assertFalse(result.blocked)

    def test_allows_task_for_orchestrator(self):
        result = check_delegate_mode("Task", self._orchestrator_payload("Task"))
        self.assertFalse(result.blocked)

    def test_allows_sendmessage_for_orchestrator(self):
        result = check_delegate_mode("SendMessage", self._orchestrator_payload("SendMessage"))
        self.assertFalse(result.blocked)

    def test_allows_skill_for_orchestrator(self):
        result = check_delegate_mode("Skill", self._orchestrator_payload("Skill"))
        self.assertFalse(result.blocked)

    def test_allows_taskcreate_for_orchestrator(self):
        result = check_delegate_mode("TaskCreate", self._orchestrator_payload("TaskCreate"))
        self.assertFalse(result.blocked)

    def test_allows_taskupdate_for_orchestrator(self):
        result = check_delegate_mode("TaskUpdate", self._orchestrator_payload("TaskUpdate"))
        self.assertFalse(result.blocked)

    def test_allows_tasklist_for_orchestrator(self):
        result = check_delegate_mode("TaskList", self._orchestrator_payload("TaskList"))
        self.assertFalse(result.blocked)

    def test_allows_taskget_for_orchestrator(self):
        result = check_delegate_mode("TaskGet", self._orchestrator_payload("TaskGet"))
        self.assertFalse(result.blocked)

    def test_allows_toolsearch_for_orchestrator(self):
        result = check_delegate_mode("ToolSearch", self._orchestrator_payload("ToolSearch"))
        self.assertFalse(result.blocked)

    def test_allows_websearch_for_orchestrator(self):
        result = check_delegate_mode("WebSearch", self._orchestrator_payload("WebSearch"))
        self.assertFalse(result.blocked)

    def test_allows_webfetch_for_orchestrator(self):
        result = check_delegate_mode("WebFetch", self._orchestrator_payload("WebFetch"))
        self.assertFalse(result.blocked)

    # -- Subagent context: never restricted --

    def test_subagent_bash_allowed(self):
        """Subagents are never restricted by delegate mode."""
        result = check_delegate_mode("Bash", self._subagent_payload("Bash"))
        self.assertFalse(result.blocked)

    def test_subagent_read_allowed(self):
        result = check_delegate_mode("Read", self._subagent_payload("Read"))
        self.assertFalse(result.blocked)

    def test_subagent_edit_allowed(self):
        result = check_delegate_mode("Edit", self._subagent_payload("Edit"))
        self.assertFalse(result.blocked)

    # -- Case insensitivity --

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
        for tool in ("bash", "edit", "write", "glob", "grep",
                     "notebookedit"):
            self.assertNotIn(tool, ORCHESTRATOR_ALLOWED_TOOLS)

    def test_read_present(self):
        self.assertIn("read", ORCHESTRATOR_ALLOWED_TOOLS)


if __name__ == "__main__":
    unittest.main()
