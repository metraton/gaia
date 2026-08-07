#!/usr/bin/env python3
"""
E2E tests for the Plugin distribution channel.

Validates the full lifecycle when CLAUDE_PLUGIN_ROOT is set:
  1. Channel detection returns PLUGIN
  2. hooks.json paths resolve to real scripts on disk
  3. PreToolUse Bash: safe command allowed (exit 0)
  4. PreToolUse Bash: destructive command blocked (exit 2)
  5. PreToolUse Agent: no preloaded context (the kernel is claimed at
     SubagentStart; the cache bridge never carries a snapshot)
  6. Hook state written after successful pre_tool_use invocation

Existing tests only verify allow/block decisions in isolation. These tests
exercise the full adapter invocation -> state written -> state readable
pipeline under the PLUGIN channel (tests/fixtures/pretool_adapter drives
adapt_pre_tool_use, the same method the stdin entry point calls).
"""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ============================================================================
# PATH SETUP
# ============================================================================
REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = REPO_ROOT / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.claude_code import ClaudeCodeAdapter
from adapters.types import HostDistribution
from modules.core.paths import clear_path_cache
from modules.core.state import get_hook_state, STATE_FILE_NAME

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tests.fixtures.pretool_adapter import (  # noqa: E402
    compat_shape,
    run_pre_tool_use,
    run_subagent_bash,
)


def _is_allowed(result) -> bool:
    """True when the compat-shaped result is an allow (None or allow dict).

    The real subagent lane may allow WITH updatedInput (the dispatch identity
    env stamp), so an allow is not always None.
    """
    if result is None:
        return True
    if isinstance(result, dict):
        return (
            result.get("hookSpecificOutput", {}).get("permissionDecision")
            == "allow"
        )
    return False


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

    # Isolate the substrate: T3 denials persist pending approvals and Task
    # dispatches birth handoff rows; neither may land on the real ~/.gaia.
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia_data"))

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
        """With CLAUDE_PLUGIN_ROOT set, detect_distribution() declares the plugin channel."""
        adapter = ClaudeCodeAdapter()
        result = adapter.detect_distribution()
        assert result.channel == "plugin", (
            f"Expected plugin channel, got {result}"
        )
        assert result.root == plugin_env["repo_root"], (
            f"Expected root {plugin_env['repo_root']}, got {result.root}"
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
        """Without CLAUDE_PLUGIN_ROOT, the distribution defaults to the npm channel."""
        monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
        adapter = ClaudeCodeAdapter()
        result = adapter.detect_distribution()
        assert result == HostDistribution(channel="npm")


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
                    # Commands are invoked as `python3 ${CLAUDE_PLUGIN_ROOT}/...`
                    # to avoid depending on the script's exec bit (tarball install
                    # mode is not always preserved). Strip the invoker prefix to
                    # validate the path token.
                    assert "${CLAUDE_PLUGIN_ROOT}/" in command, (
                        f"Command in {event_name} does not reference "
                        f"${{CLAUDE_PLUGIN_ROOT}}: {command}"
                    )
                    path_part = command.split()[-1]

                    # Resolve the path: replace ${CLAUDE_PLUGIN_ROOT} with repo root
                    resolved = path_part.replace("${CLAUDE_PLUGIN_ROOT}", str(repo_root))
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
                    # Strip the `python3 ` invoker prefix to reach the path token.
                    path_part = command.split()[-1]
                    resolved = path_part.replace("${CLAUDE_PLUGIN_ROOT}", str(repo_root))
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
        """A safe command like 'ls -la' should be allowed."""
        result = compat_shape(run_subagent_bash("ls -la"))
        assert _is_allowed(result), (
            f"Expected allow for 'ls -la', got: {result}"
        )

    def test_read_only_kubectl_allowed(self, plugin_env):
        """kubectl get pods should be allowed as a T0 read-only command."""
        result = compat_shape(run_subagent_bash("kubectl get pods"))
        assert _is_allowed(result), (
            f"Expected allow for 'kubectl get pods', got: {result}"
        )


# ============================================================================
# TEST 4: PreToolUse Bash - blocked
# ============================================================================

class TestPluginPreToolUseBashBlocked:
    """Destructive Bash commands must be blocked under the PLUGIN channel."""

    def test_destructive_command_blocked(self, plugin_env):
        """'rm -rf /' must be blocked (error string or deny/block decision)."""
        result = compat_shape(run_subagent_bash("rm -rf /"))

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
        result = compat_shape(run_subagent_bash("git push --force origin main"))

        # git push --force should not be silently allowed
        assert not _is_allowed(result), (
            "Expected 'git push --force' to be blocked or require approval"
        )


# ============================================================================
# TEST 5: PreToolUse Agent - no preloaded context
# ============================================================================

class TestPluginAgentDispatchCarriesNoPreloadedContext:
    """Agent dispatch returns no payload; the cache bridge never carries a
    preloaded project-context snapshot (the kernel is claimed at
    SubagentStart and project context is pulled on demand via the CLI)."""

    def test_agent_dispatch_returns_none_and_caches_no_snapshot(
        self, plugin_env_with_context,
    ):
        session_marker = "sess-plugin-e2e-agent"
        result = compat_shape(run_pre_tool_use(
            "Agent",
            {
                "subagent_type": "cloud-troubleshooter",
                "prompt": "Check pod health in namespace test",
            },
            session_id=session_marker,
        ))

        # PreToolUse must NOT return additionalContext (it would land on the
        # orchestrator, not the subagent).
        assert result is None, (
            f"PreToolUse:Agent should return no payload, got: {result}"
        )

        cache_dir = Path("/tmp/gaia-context-cache")
        for f in cache_dir.glob(f"{session_marker}-*.json"):
            cached = json.loads(f.read_text())
            assert "# Project Context" not in cached.get("context", ""), (
                "The cache bridge must not carry a preloaded project-context snapshot"
            )
            f.unlink(missing_ok=True)



# ============================================================================
# TEST 7: Hook state written after invocation
# ============================================================================

class TestPluginStateWrittenAfterHook:
    """After a successful PreToolUse, hook state must be persisted to disk."""

    def test_state_written_for_bash_command(self, plugin_env):
        """After allowing 'ls -la', a state file should exist with correct fields."""
        result = compat_shape(run_subagent_bash("ls -la"))
        assert _is_allowed(result), f"Expected 'ls -la' to be allowed, got: {result}"

        # Verify state file was written
        state_file = plugin_env["claude_dir"] / STATE_FILE_NAME
        assert state_file.exists(), (
            f"Hook state file not found at {state_file} after allowed command"
        )

        state_data = json.loads(state_file.read_text())
        assert state_data["tool_name"] == "Bash", (
            f"Expected tool_name='Bash', got '{state_data['tool_name']}'"
        )
        # The subagent lane records the EFFECTIVE command (the dispatch
        # identity env stamp may prefix it).
        assert state_data["command"].endswith("ls -la"), (
            f"Expected command ending in 'ls -la', got '{state_data['command']}'"
        )
        assert state_data["pre_hook_result"] == "allowed"
        assert state_data["tier"] != "unknown", (
            "Tier should be classified (not 'unknown') for a known command"
        )
        assert state_data["start_time"] != "", "start_time should be set"
        assert state_data["start_time_epoch"] > 0, "start_time_epoch should be > 0"

    def test_state_readable_via_get_hook_state(self, plugin_env):
        """State written by save_hook_state must be readable via get_hook_state."""
        run_subagent_bash("echo hello")

        # Read state back via the module API
        state = get_hook_state()
        assert state is not None, "get_hook_state() returned None after hook invocation"
        assert state.tool_name == "Bash"
        assert "echo hello" in state.command
        assert state.pre_hook_result == "allowed"

    def test_state_not_written_for_blocked_command(self, plugin_env):
        """Blocked commands should not write hook state (state reflects last allowed)."""
        # Ensure no prior state
        state_file = plugin_env["claude_dir"] / STATE_FILE_NAME
        if state_file.exists():
            state_file.unlink()
        clear_path_cache()

        result = compat_shape(run_subagent_bash("rm -rf /"))
        assert result is not None, "Expected 'rm -rf /' to be blocked"

        # State file should NOT exist (blocked commands skip save_hook_state)
        assert not state_file.exists(), (
            "Hook state file should not exist after a blocked command"
        )

    def test_state_written_for_agent_task(self, plugin_env_with_context):
        """After allowing an Agent task, state should record the agent dispatch."""
        result = compat_shape(run_pre_tool_use(
            "Agent",
            {
                "subagent_type": "cloud-troubleshooter",
                "prompt": "Check pods",
            },
        ))
        assert result is None, f"Expected Agent dispatch to be allowed, got: {result}"

        # Verify state was written for the agent task
        state = get_hook_state()
        assert state is not None, "Hook state should exist after agent task"
        assert "cloud-troubleshooter" in state.command, (
            f"State command should reference agent name, got: {state.command}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
