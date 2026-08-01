"""Tests for orchestrator delegate mode enforcement."""

import sys
import unittest
from pathlib import Path

# Add hooks directory to path for module resolution
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "hooks"))

from modules.orchestrator.delegate_mode import (
    CODEX_ALLOWED_TOOLS,
    CODEX_DELEGATION_TOOLS,
    CODEX_HOST,
    HOST_MARKER_KEY,
    ORCHESTRATOR_AGENT_TYPES,
    ORCHESTRATOR_ALLOWED_TOOLS,
    SessionRole,
    check_delegate_mode,
    classify_session_role,
    is_orchestrator_context,
    normalize_tool_name,
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

    def _named_specialist_payload(self, tool_name: str) -> dict:
        """Build a payload simulating a `--agent <specialist>` main thread."""
        return {
            "session_id": "abc123",
            "tool_name": tool_name,
            "tool_input": {},
            "agent_type": "developer",
        }

    # -- Named-specialist main thread: blocked, with its own reason --

    def test_blocks_bash_for_named_specialist(self):
        """Out of design: agents run only via a genuine orchestrator dispatch.

        A --agent main thread never enters the context-injection path a real
        dispatch builds, so it is denied like the orchestrator -- but under a
        distinct, truthful reason.
        """
        result = check_delegate_mode("Bash", self._named_specialist_payload("Bash"))
        self.assertTrue(result.blocked)
        self.assertIn("NOT RUNNABLE STANDALONE", result.reason)

    def test_named_specialist_reason_is_not_the_orchestrator_message(self):
        """A named specialist must never be told to delegate to a specialist."""
        result = check_delegate_mode("Bash", self._named_specialist_payload("Bash"))
        self.assertNotIn("DELEGATION REQUIRED", result.reason)

    def test_allows_read_for_named_specialist(self):
        """Read stays available, mirroring the orchestrator's own allowance."""
        result = check_delegate_mode("Read", self._named_specialist_payload("Read"))
        self.assertFalse(result.blocked)

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


class TestCodexDelegateMode(unittest.TestCase):
    """Codex gets its own closed tool vocabulary; Claude remains unchanged."""

    @staticmethod
    def _payload(tool_name: str) -> dict:
        return {
            "session_id": "codex-thread",
            "tool_name": tool_name,
            "tool_input": {},
            HOST_MARKER_KEY: CODEX_HOST,
        }

    def test_exec_command_alias_bash_remains_delegated(self):
        result = check_delegate_mode("Bash", self._payload("Bash"))
        self.assertTrue(result.blocked)
        self.assertIn("collaboration.spawn_agent", result.reason)

    def test_apply_patch_and_edit_alias_remain_delegated(self):
        for name in ("apply_patch", "Edit"):
            with self.subTest(name=name):
                self.assertTrue(
                    check_delegate_mode(name, self._payload(name)).blocked
                )

    def test_delegation_tool_spellings_are_never_blocked(self):
        for name in (
            "spawn_agent",
            "collaboration.spawn_agent",
            "collaboration_spawn_agent",
            "collaboration-spawn-agent",
            "collaborationspawn_agent",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    check_delegate_mode(name, self._payload(name)).blocked
                )

    def test_collaboration_coordination_tools_are_allowed(self):
        for name in (
            "collaboration.list_agents",
            "collaboration.send_message",
            "collaboration.followup_task",
            "collaboration.wait_agent",
            "collaboration.interrupt_agent",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    check_delegate_mode(name, self._payload(name)).blocked
                )

    def test_codex_direct_read_and_state_tools_are_allowed(self):
        for name in (
            "get_goal",
            "update_plan",
            "view_image",
            "list_mcp_resources",
            "read_mcp_resource",
            "web.run",
            "functions.exec",
            "tool_search",
        ):
            with self.subTest(name=name):
                self.assertFalse(
                    check_delegate_mode(name, self._payload(name)).blocked
                )

    def test_read_only_mcp_leaf_is_allowed(self):
        name = "mcp__codex_apps__github__search_repositories"
        self.assertFalse(check_delegate_mode(name, self._payload(name)).blocked)

    def test_mutative_or_unknown_mcp_leaf_is_blocked(self):
        for name in (
            "mcp__codex_apps__gmail__send_email",
            "mcp__server__get_or_create_resource",
            "mcp__server__synchronize",
        ):
            with self.subTest(name=name):
                self.assertTrue(
                    check_delegate_mode(name, self._payload(name)).blocked
                )

    def test_unknown_local_tool_remains_blocked(self):
        name = "custom_unknown_tool"
        self.assertTrue(check_delegate_mode(name, self._payload(name)).blocked)

    def test_denial_does_not_claim_routing_recommendation_exists(self):
        result = check_delegate_mode("Bash", self._payload("Bash"))
        self.assertNotIn("routing recommendation", result.reason.lower())
        self.assertNotIn("last message", result.reason.lower())

    def test_codex_delegation_invariant(self):
        self.assertTrue(CODEX_DELEGATION_TOOLS)
        self.assertLessEqual(CODEX_DELEGATION_TOOLS, CODEX_ALLOWED_TOOLS)

    def test_normalization_covers_points_underscores_and_hyphens(self):
        expected = "collaborationspawnagent"
        for name in (
            "collaboration.spawn_agent",
            "collaboration_spawn_agent",
            "collaboration-spawn-agent",
        ):
            self.assertEqual(normalize_tool_name(name), expected)


if __name__ == "__main__":
    unittest.main()
