#!/usr/bin/env python3
"""
E2E tests for the Plugin distribution channel.

Validates the full lifecycle when CLAUDE_PLUGIN_ROOT is set:
  1. Channel detection returns PLUGIN
  2. hooks.json paths resolve to real scripts on disk
  3. PreToolUse Bash: safe command allowed (exit 0)
  4. PreToolUse Bash: destructive command blocked (exit 2)
  5. PreToolUse Agent: context injection enriches prompt
  6. No double context injection (# Project Context appears exactly once)
  7. Hook state written after successful pre_tool_use invocation

Existing tests only verify allow/block decisions in isolation. These tests
exercise the full hook invocation -> context injection -> state written ->
state readable pipeline under the PLUGIN channel.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ============================================================================
# PATH SETUP
# ============================================================================
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import DistributionChannel
from modules.core.paths import clear_path_cache
from modules.core.state import get_hook_state, STATE_FILE_NAME


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def plugin_env(monkeypatch, tmp_path):
    """Set CLAUDE_PLUGIN_ROOT to repo root and ensure a .claude dir exists.

    Creates a temporary project directory with a .claude/ directory so that
    path resolution (find_claude_dir) works correctly under the PLUGIN channel.
    State files are written here and cleaned up automatically by tmp_path.
    """
    clear_path_cache()

    # Point CLAUDE_PLUGIN_ROOT at the real repo root (hooks, agents, etc.)
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(REPO_ROOT))

    # Create a minimal project directory with .claude/ for state storage
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    claude_dir = project_dir / ".claude"
    claude_dir.mkdir()

    # Copy config (needed by context_provider and task_validator)
    shutil.copytree(REPO_ROOT / "config", claude_dir / "config")

    # cd into the project dir so find_claude_dir() locates .claude/
    original_cwd = os.getcwd()
    os.chdir(project_dir)

    yield {
        "repo_root": REPO_ROOT,
        "project_dir": project_dir,
        "claude_dir": claude_dir,
        "hooks_dir": HOOKS_DIR,
    }

    os.chdir(original_cwd)
    clear_path_cache()


@pytest.fixture
def plugin_env_with_context(plugin_env):
    """Extends plugin_env with a project-context.json and tools directory.

    This fixture enables context injection tests by providing the files
    that context_provider.py needs to produce enriched prompts.
    """
    claude_dir = plugin_env["claude_dir"]

    # Copy agents (needed for agent frontmatter lookup)
    shutil.copytree(REPO_ROOT / "agents", claude_dir / "agents")

    # Copy tools (context_provider.py lives here)
    shutil.copytree(REPO_ROOT / "tools", claude_dir / "tools")

    # Create project-context with minimal data
    pc_dir = claude_dir / "project-context"
    pc_dir.mkdir()
    pc_data = {
        "metadata": {
            "project_name": "plugin-e2e-test",
            "cloud_provider": "gcp",
            "primary_region": "us-east4",
        },
        "sections": {
            "project_identity": {"name": "plugin-e2e-test", "type": "application"},
            "stack": {},
            "git": {"platform": "github"},
            "environment": {"runtimes": []},
            "infrastructure": {"cloud_providers": [{"name": "gcp", "region": "us-east4"}]},
            "cluster_details": {"kubernetes_version": "1.28.5"},
            "infrastructure_topology": {},
            "terraform_infrastructure": {},
            "gitops_configuration": {},
            "application_services": {},
        },
    }
    (pc_dir / "project-context.json").write_text(json.dumps(pc_data, indent=2))

    plugin_env["pc_path"] = pc_dir / "project-context.json"
    return plugin_env


# ============================================================================
# TEST 1: Channel detection
# ============================================================================

class TestPluginChannelDetection:
    """Verify that CLAUDE_PLUGIN_ROOT triggers PLUGIN channel detection."""

    def test_detect_channel_returns_plugin(self, plugin_env):
        """With CLAUDE_PLUGIN_ROOT set, adapter.detect_channel() returns PLUGIN."""
        adapter = ClaudeCodeAdapter()
        result = adapter.detect_channel()
        assert result == DistributionChannel.PLUGIN, (
            f"Expected PLUGIN channel, got {result}"
        )

    def test_get_plugin_root_returns_correct_path(self, plugin_env):
        """_get_plugin_root() must return the CLAUDE_PLUGIN_ROOT env var as a Path."""
        adapter = ClaudeCodeAdapter()
        result = adapter._get_plugin_root()
        assert result is not None, "Expected non-None plugin root"
        assert str(result) == str(plugin_env["repo_root"]), (
            f"Expected {plugin_env['repo_root']}, got {result}"
        )

    def test_npm_channel_when_env_unset(self, monkeypatch):
        """Without CLAUDE_PLUGIN_ROOT, channel defaults to NPM."""
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        adapter = ClaudeCodeAdapter()
        result = adapter.detect_channel()
        assert result == DistributionChannel.NPM


# ============================================================================
# TEST 2: hooks.json path resolution
# ============================================================================

class TestPluginHooksJsonPaths:
    """Verify hooks.json command paths resolve to real scripts on disk."""

    def test_hooks_json_paths_resolve(self, plugin_env):
        """Every ${CLAUDE_PLUGIN_ROOT}/... command in hooks.json must map to a real file."""
        hooks_json_path = HOOKS_DIR / "hooks.json"
        assert hooks_json_path.exists(), f"hooks.json not found at {hooks_json_path}"

        data = json.loads(hooks_json_path.read_text())
        repo_root = plugin_env["repo_root"]

        missing = []
        for event_name, entries in data["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    assert command.startswith("${CLAUDE_PLUGIN_ROOT}/"), (
                        f"Command in {event_name} does not use "
                        f"${{CLAUDE_PLUGIN_ROOT}} prefix: {command}"
                    )

                    # Resolve the path: replace ${CLAUDE_PLUGIN_ROOT} with repo root
                    resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", str(repo_root))
                    resolved_path = Path(resolved)
                    if not resolved_path.exists():
                        missing.append(f"{event_name}: {command} -> {resolved_path}")

        assert not missing, (
            f"hooks.json references scripts that don't exist on disk:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

    def test_all_hook_scripts_are_executable_python(self, plugin_env):
        """Each resolved hook script should be a Python file with a shebang or .py extension."""
        hooks_json_path = HOOKS_DIR / "hooks.json"
        data = json.loads(hooks_json_path.read_text())
        repo_root = plugin_env["repo_root"]

        for event_name, entries in data["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    command = hook["command"]
                    resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", str(repo_root))
                    resolved_path = Path(resolved)
                    assert resolved_path.suffix == ".py", (
                        f"Hook script in {event_name} is not a .py file: {resolved_path}"
                    )


# ============================================================================
# TEST 3: PreToolUse Bash - allowed
# ============================================================================

class TestPluginPreToolUseBashAllowed:
    """Safe Bash commands must be allowed under the PLUGIN channel."""

    def test_safe_command_allowed(self, plugin_env):
        """A safe command like 'ls -la' should be allowed (return None or dict)."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location("pre_tool_use_e2e_allow", str(pre_hook_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = mod.pre_tool_use_hook("Bash", {"command": "ls -la"})

        # None means allowed without modification
        assert result is None, (
            f"Expected None (allowed) for 'ls -la', got: {result}"
        )

    def test_read_only_kubectl_allowed(self, plugin_env):
        """kubectl get pods should be allowed as a T0 read-only command."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location("pre_tool_use_e2e_kubectl", str(pre_hook_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = mod.pre_tool_use_hook("Bash", {"command": "kubectl get pods"})
        assert result is None, (
            f"Expected None (allowed) for 'kubectl get pods', got: {result}"
        )


# ============================================================================
# TEST 4: PreToolUse Bash - blocked
# ============================================================================

class TestPluginPreToolUseBashBlocked:
    """Destructive Bash commands must be blocked under the PLUGIN channel."""

    def test_destructive_command_blocked(self, plugin_env):
        """'rm -rf /' must be blocked (return a non-None error string/dict)."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location("pre_tool_use_e2e_block", str(pre_hook_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = mod.pre_tool_use_hook("Bash", {"command": "rm -rf /"})

        assert result is not None, "Expected 'rm -rf /' to be blocked, got None (allowed)"
        # The result should be either a string (error message) or a dict with block decision
        if isinstance(result, str):
            assert len(result) > 0, "Block message should not be empty"
        elif isinstance(result, dict):
            decision = (
                result.get("hookSpecificOutput", {}).get("permissionDecision", "")
            )
            assert decision in ("deny", "block"), (
                f"Expected deny/block decision, got: {decision}"
            )

    def test_git_push_force_blocked(self, plugin_env):
        """'git push --force' must be blocked or require approval."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location("pre_tool_use_e2e_gpf", str(pre_hook_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = mod.pre_tool_use_hook("Bash", {"command": "git push --force origin main"})

        # git push --force should not be silently allowed
        assert result is not None, (
            "Expected 'git push --force' to be blocked or require approval, got None"
        )


# ============================================================================
# TEST 5: PreToolUse Agent - context injection
# ============================================================================

class TestPluginPreToolUseAgentContextInjection:
    """Agent tool invocations must receive context injection under PLUGIN channel."""

    def test_agent_context_injection_occurs(self, plugin_env_with_context):
        """PreToolUse for a project agent returns additionalContext with '# Project Context'."""
        import importlib.util

        env = plugin_env_with_context
        pre_hook_path = env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_ctx", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Mock subprocess call to context_provider.py since we control the output
        mock_context_payload = {
            "project_knowledge": {
                "cluster_details": {"kubernetes_version": "1.28.5"},
                "infrastructure": {"cloud_providers": [{"name": "gcp"}]},
            },
            "metadata": {
                "cloud_provider": "gcp",
                "contract_version": "3.0",
                "rules_count": 0,
            },
            "write_permissions": {
                "readable_sections": ["cluster_details", "infrastructure"],
                "writable_sections": ["cluster_details"],
            },
            "investigation_brief": {
                "agent_role": "primary",
                "primary_surface": "live_runtime",
            },
            "surface_routing": {
                "primary_surface": "live_runtime",
                "active_surfaces": ["live_runtime"],
                "dispatch_mode": "single_surface",
                "recommended_agents": ["cloud-troubleshooter"],
            },
            "rules": {},
        }

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = json.dumps(mock_context_payload)
        mock_subprocess_result.stderr = ""

        with patch("modules.context.context_injector.subprocess.run",
                    return_value=mock_subprocess_result):
            result = mod.pre_tool_use_hook(
                "Agent",
                {
                    "subagent_type": "cloud-troubleshooter",
                    "prompt": "Check pod health in namespace test",
                },
            )

        # Result should be a dict with additionalContext containing context
        assert isinstance(result, dict), (
            f"Expected dict with additionalContext, got: {type(result).__name__}: {result}"
        )

        additional_context = result["hookSpecificOutput"]["additionalContext"]
        assert "# Project Context" in additional_context, (
            "additionalContext must contain '# Project Context' section"
        )
        assert "cluster_details" in additional_context, (
            "additionalContext must contain injected project knowledge"
        )
        # Prompt should NOT be mutated -- additionalContext is separate
        assert "updatedInput" not in result["hookSpecificOutput"], (
            "Phase 2: should use additionalContext, not updatedInput"
        )


# ============================================================================
# TEST 6: No double context injection
# ============================================================================

class TestPluginNoDoubleContextInjection:
    """'# Project Context' must appear exactly once in additionalContext."""

    def test_project_context_appears_once(self, plugin_env_with_context):
        """Verify that '# Project Context' appears exactly once in additionalContext."""
        import importlib.util

        env = plugin_env_with_context
        pre_hook_path = env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_dedup", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_context_payload = {
            "project_knowledge": {
                "cluster_details": {"kubernetes_version": "1.28.5"},
            },
            "metadata": {"cloud_provider": "gcp"},
            "write_permissions": {
                "readable_sections": ["cluster_details"],
                "writable_sections": ["cluster_details"],
            },
            "investigation_brief": {},
            "surface_routing": {},
            "rules": {},
        }

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = json.dumps(mock_context_payload)
        mock_subprocess_result.stderr = ""

        with patch("modules.context.context_injector.subprocess.run",
                    return_value=mock_subprocess_result):
            result = mod.pre_tool_use_hook(
                "Agent",
                {
                    "subagent_type": "cloud-troubleshooter",
                    "prompt": "Investigate cluster health",
                },
            )

        assert isinstance(result, dict), f"Expected dict, got: {result}"
        additional_context = result["hookSpecificOutput"]["additionalContext"]

        count = additional_context.count("# Project Context")
        assert count == 1, (
            f"'# Project Context' should appear exactly once in additionalContext, "
            f"found {count} occurrences"
        )

    def test_no_double_injection_on_already_enriched_prompt(self, plugin_env_with_context):
        """If prompt already contains '# Project Context', dedup guard prevents injection.

        With additionalContext, the prompt is not mutated. The dedup guard checks
        the original prompt: if it already has '# Project Context', no context is
        injected (result is None).
        """
        import importlib.util

        env = plugin_env_with_context
        pre_hook_path = env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_dedup2", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Prompt that already has context injected
        pre_enriched_prompt = (
            "# Task\n\nInvestigate cluster health\n\n"
            "# Project Context\n\n{\"cluster_details\": {}}\n"
        )

        mock_context_payload = {
            "project_knowledge": {"cluster_details": {"status": "RUNNING"}},
            "metadata": {"cloud_provider": "gcp"},
            "write_permissions": {
                "readable_sections": ["cluster_details"],
                "writable_sections": ["cluster_details"],
            },
            "investigation_brief": {},
            "surface_routing": {},
            "rules": {},
        }

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = json.dumps(mock_context_payload)
        mock_subprocess_result.stderr = ""

        with patch("modules.context.context_injector.subprocess.run",
                    return_value=mock_subprocess_result):
            result = mod.pre_tool_use_hook(
                "Agent",
                {
                    "subagent_type": "cloud-troubleshooter",
                    "prompt": pre_enriched_prompt,
                },
            )

        # Dedup guard triggers: prompt already has '# Project Context', so
        # build_project_context returns None, no additionalContext is returned.
        assert result is None, (
            f"Expected None (dedup guard should prevent injection), got: {result}"
        )


# ============================================================================
# TEST 7: Hook state written after invocation
# ============================================================================

class TestPluginStateWrittenAfterHook:
    """After a successful PreToolUse, hook state must be persisted to disk."""

    def test_state_written_for_bash_command(self, plugin_env):
        """After allowing 'ls -la', a state file should exist with correct fields."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_state", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        result = mod.pre_tool_use_hook("Bash", {"command": "ls -la"})
        assert result is None, f"Expected 'ls -la' to be allowed, got: {result}"

        # Verify state file was written
        state_file = plugin_env["claude_dir"] / STATE_FILE_NAME
        assert state_file.exists(), (
            f"Hook state file not found at {state_file} after allowed command"
        )

        state_data = json.loads(state_file.read_text())
        assert state_data["tool_name"] == "Bash", (
            f"Expected tool_name='Bash', got '{state_data['tool_name']}'"
        )
        assert state_data["command"] == "ls -la", (
            f"Expected command='ls -la', got '{state_data['command']}'"
        )
        assert state_data["pre_hook_result"] == "allowed"
        assert state_data["tier"] != "unknown", (
            "Tier should be classified (not 'unknown') for a known command"
        )
        assert state_data["start_time"] != "", "start_time should be set"
        assert state_data["start_time_epoch"] > 0, "start_time_epoch should be > 0"

    def test_state_readable_via_get_hook_state(self, plugin_env):
        """State written by save_hook_state must be readable via get_hook_state."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_state2", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Run a safe command to trigger state write
        mod.pre_tool_use_hook("Bash", {"command": "echo hello"})

        # Read state back via the module API
        state = get_hook_state()
        assert state is not None, "get_hook_state() returned None after hook invocation"
        assert state.tool_name == "Bash"
        assert "echo hello" in state.command
        assert state.pre_hook_result == "allowed"

    def test_state_not_written_for_blocked_command(self, plugin_env):
        """Blocked commands should not write hook state (state reflects last allowed)."""
        import importlib.util

        pre_hook_path = plugin_env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_nostate", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Ensure no prior state
        state_file = plugin_env["claude_dir"] / STATE_FILE_NAME
        if state_file.exists():
            state_file.unlink()
        clear_path_cache()

        result = mod.pre_tool_use_hook("Bash", {"command": "rm -rf /"})
        assert result is not None, "Expected 'rm -rf /' to be blocked"

        # State file should NOT exist (blocked commands skip save_hook_state)
        assert not state_file.exists(), (
            "Hook state file should not exist after a blocked command"
        )

    def test_state_written_for_agent_task(self, plugin_env_with_context):
        """After allowing an Agent task, state should record the agent dispatch."""
        import importlib.util

        env = plugin_env_with_context
        pre_hook_path = env["hooks_dir"] / "pre_tool_use.py"
        spec = importlib.util.spec_from_file_location(
            "pre_tool_use_e2e_agent_state", str(pre_hook_path)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        mock_context_payload = {
            "project_knowledge": {"cluster_details": {}},
            "metadata": {"cloud_provider": "gcp"},
            "write_permissions": {
                "readable_sections": ["cluster_details"],
                "writable_sections": ["cluster_details"],
            },
            "investigation_brief": {},
            "surface_routing": {},
            "rules": {},
        }

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 0
        mock_subprocess_result.stdout = json.dumps(mock_context_payload)
        mock_subprocess_result.stderr = ""

        with patch("modules.context.context_injector.subprocess.run",
                    return_value=mock_subprocess_result):
            result = mod.pre_tool_use_hook(
                "Agent",
                {
                    "subagent_type": "cloud-troubleshooter",
                    "prompt": "Check pods",
                },
            )

        # Verify state was written for the agent task
        state = get_hook_state()
        assert state is not None, "Hook state should exist after agent task"
        assert "cloud-troubleshooter" in state.command, (
            f"State command should reference agent name, got: {state.command}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
