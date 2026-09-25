#!/usr/bin/env python3
"""Workspace hook registration by run_first_time_setup, per install layout.

A plugin install registers its hooks through the plugin's own hooks.json, so
any Gaia entry in the workspace settings.local.json makes every hook fire
twice. These tests pin that a plugin launch leaves none there (and strips
what earlier versions merged, user entries untouched), that an npm copy still
merges, and that the workspace-registered copy of a plugin install writes
nothing.
"""

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.core import plugin_setup
from modules.core.paths import clear_path_cache

GAIA_ENTRYPOINTS = ("pre_tool_use.py", "session_start.py", "subagent_start.py")


def _gaia_handler(workspace: Path, entrypoint: str) -> dict:
    return {
        "type": "command",
        "command": f"python3 {workspace}/.claude/hooks/{entrypoint}",
    }


def _gaia_commands(settings: dict) -> list:
    entrypoints = plugin_setup._gaia_hook_entrypoints()
    return [
        h["command"]
        for entries in settings.get("hooks", {}).values()
        for entry in entries
        for h in entry.get("hooks", [])
        if plugin_setup._is_gaia_hook_command(h.get("command"), entrypoints)
    ]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    monkeypatch.chdir(ws)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    clear_path_cache()
    yield ws
    clear_path_cache()


@pytest.fixture
def plugin_launch(workspace, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin-cache" / "gaia" / "5.5.0"))
    return workspace


def _settings(workspace: Path) -> dict:
    return json.loads((workspace / ".claude" / "settings.local.json").read_text())


class TestPluginLaunch:
    def test_first_run_leaves_no_gaia_hook_in_workspace_settings(self, plugin_launch):
        plugin_setup.run_first_time_setup(mark_done=True)

        assert _gaia_commands(_settings(plugin_launch)) == []

    def test_two_runs_are_idempotent(self, plugin_launch):
        settings_path = plugin_launch / ".claude" / "settings.local.json"

        plugin_setup.run_first_time_setup(mark_done=True)
        after_first = settings_path.read_bytes()
        second_message = plugin_setup.run_first_time_setup(mark_done=True)

        assert settings_path.read_bytes() == after_first
        assert second_message is None
        assert _gaia_commands(_settings(plugin_launch)) == []

    def test_entries_merged_by_an_earlier_version_are_stripped(self, plugin_launch):
        ws = plugin_launch
        user_guard = {"type": "command", "command": f"python3 {ws}/.claude/hooks/my_guard.py"}
        user_echo = {"type": "command", "command": "echo user-hook"}
        (ws / ".claude" / "settings.local.json").write_text(json.dumps({
            "enabledPlugins": {"gaia@gaia-marketplace": True},
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [_gaia_handler(ws, "pre_tool_use.py"), user_echo]},
                    {"matcher": "Task", "hooks": [_gaia_handler(ws, "pre_tool_use.py")]},
                ],
                "SessionStart": [{"hooks": [_gaia_handler(ws, "session_start.py")]}],
                "SubagentStart": [{"hooks": [
                    {"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/subagent_start.py"},
                ]}],
                "Notification": [{"hooks": [user_guard]}],
            },
        }))

        plugin_setup.run_first_time_setup(mark_done=True)
        settings = _settings(ws)

        assert _gaia_commands(settings) == []
        assert settings["hooks"] == {
            "PreToolUse": [{"matcher": "Bash", "hooks": [user_echo]}],
            "Notification": [{"hooks": [user_guard]}],
        }
        assert settings["enabledPlugins"] == {"gaia@gaia-marketplace": True}

    def test_hooks_key_is_dropped_when_only_gaia_entries_were_there(self, plugin_launch):
        ws = plugin_launch
        (ws / ".claude" / "settings.local.json").write_text(json.dumps({
            "hooks": {"SessionStart": [{"hooks": [_gaia_handler(ws, name) for name in GAIA_ENTRYPOINTS]}]},
        }))

        assert plugin_setup.remove_merged_gaia_hooks() is True
        assert "hooks" not in _settings(ws)
        assert plugin_setup.remove_merged_gaia_hooks() is False


class TestNonPluginLaunch:
    def test_npm_copy_still_merges_hooks_into_workspace_settings(self, workspace, monkeypatch):
        monkeypatch.setattr(plugin_setup, "_installed_under_node_modules", lambda: True)

        plugin_setup.run_first_time_setup(mark_done=True)
        commands = _gaia_commands(_settings(workspace))

        assert f"python3 {workspace}/.claude/hooks/pre_tool_use.py" in commands
        assert "${CLAUDE_PLUGIN_ROOT}" not in json.dumps(_settings(workspace)["hooks"])

    def test_workspace_registered_copy_of_a_plugin_install_writes_no_hooks(self, workspace, monkeypatch):
        """The copy an earlier plugin version registered in workspace settings runs
        from the plugin cache with no CLAUDE_PLUGIN_ROOT; re-merging from it would
        undo the plugin launch's cleanup on every event."""
        monkeypatch.setattr(plugin_setup, "_installed_under_node_modules", lambda: False)

        plugin_setup.run_first_time_setup(mark_done=True)

        assert "hooks" not in _settings(workspace)
