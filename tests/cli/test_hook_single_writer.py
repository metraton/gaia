#!/usr/bin/env python3
"""One writer registers Gaia's hooks in a workspace, once per channel.

`gaia install`, `gaia update` and the SessionStart/UserPromptSubmit setup all
reach the same merge. These tests pin its contract through each entry point:
the plugin channel leaves no Gaia hook in workspace settings; the npm channel
merges by (event, matcher, command), so a new matcher that shares a command is
added and a retired matcher or event is pruned; user entries survive every
pass; paths are baked in posix form with no regex escaping; and alternating
the two channels in one workspace converges instead of flapping.
"""

import json
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _path in (_REPO_ROOT / "bin", _REPO_ROOT / "hooks"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from cli import _install_helpers as helpers  # noqa: E402
from modules.core import plugin_setup  # noqa: E402
from modules.core.paths import clear_path_cache  # noqa: E402

GAIA_PRE = "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use.py"
USER_GUARD = {"type": "command", "command": "/opt/user/guard.sh"}


def _entry(matcher: str | None, command: str) -> dict:
    entry: dict = {"hooks": [{"type": "command", "command": command}]}
    if matcher is not None:
        entry = {"matcher": matcher, **entry}
    return entry


def _package(tmp_path: Path, shipped: dict) -> Path:
    pkg = tmp_path / "pkg"
    (pkg / "hooks").mkdir(parents=True)
    (pkg / "hooks" / "hooks.json").write_text(json.dumps({"hooks": shipped}))
    return pkg


def _settings(workspace: Path) -> dict:
    return json.loads((workspace / ".claude" / "settings.local.json").read_text())


def _write_settings(workspace: Path, settings: dict) -> None:
    (workspace / ".claude" / "settings.local.json").write_text(json.dumps(settings))


def _gaia_triples(workspace: Path, settings: dict) -> list:
    entrypoints = plugin_setup._gaia_hook_entrypoints()
    return [
        (event, entry.get("matcher"), h["command"])
        for event, entries in settings.get("hooks", {}).items()
        for entry in entries
        for h in entry.get("hooks", [])
        if plugin_setup.is_gaia_hook_command(h.get("command"), workspace, entrypoints)
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


def _hooks_abs(workspace: Path) -> str:
    return ((workspace / ".claude").resolve() / "hooks").as_posix()


def test_a_new_matcher_sharing_a_command_is_added_and_user_hook_kept(workspace, tmp_path):
    hooks_abs = _hooks_abs(workspace)
    _write_settings(workspace, {"hooks": {"PreToolUse": [
        _entry("Bash", f"python3 {hooks_abs}/pre_tool_use.py"),
        {"matcher": "Bash", "hooks": [USER_GUARD]},
    ]}})
    pkg = _package(tmp_path, {"PreToolUse": [_entry("Bash", GAIA_PRE), _entry("Skill", GAIA_PRE)]})

    res = helpers.merge_local_hooks(workspace, plugin_root=pkg)

    settings = _settings(workspace)
    assert res["action"] == "updated"
    assert sorted(_gaia_triples(workspace, settings)) == [
        ("PreToolUse", "Bash", f"python3 {hooks_abs}/pre_tool_use.py"),
        ("PreToolUse", "Skill", f"python3 {hooks_abs}/pre_tool_use.py"),
    ]
    assert {"matcher": "Bash", "hooks": [USER_GUARD]} in settings["hooks"]["PreToolUse"]


def test_b_retired_matcher_and_event_are_pruned(workspace, tmp_path):
    hooks_abs = _hooks_abs(workspace)
    _write_settings(workspace, {"hooks": {
        "PreToolUse": [
            _entry("Bash", f"python3 {hooks_abs}/pre_tool_use.py"),
            _entry("Task", f"python3 {hooks_abs}/pre_tool_use.py"),
        ],
        "RetiredGaiaEvent": [_entry(None, f"python3 {hooks_abs}/retired_handler.py")],
    }})
    pkg = _package(tmp_path, {"PreToolUse": [_entry("Bash", GAIA_PRE)]})

    helpers.merge_local_hooks(workspace, plugin_root=pkg)

    settings = _settings(workspace)
    assert _gaia_triples(workspace, settings) == [
        ("PreToolUse", "Bash", f"python3 {hooks_abs}/pre_tool_use.py"),
    ]
    assert "RetiredGaiaEvent" not in settings["hooks"]


def test_c_plugin_channel_leaves_zero_gaia_hooks_and_user_intact(workspace, tmp_path, monkeypatch):
    hooks_abs = _hooks_abs(workspace)
    _write_settings(workspace, {"hooks": {"PreToolUse": [
        _entry("Bash", f"python3 {hooks_abs}/pre_tool_use.py"),
        {"matcher": "Bash", "hooks": [USER_GUARD]},
    ]}})
    pkg = _package(tmp_path, {"PreToolUse": [_entry("Bash", GAIA_PRE)]})
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin-cache" / "gaia" / "5.5.0"))

    helpers.merge_local_hooks(workspace, plugin_root=pkg)
    after_install = _settings(workspace)
    plugin_setup._sync_workspace_hooks()
    after_session = _settings(workspace)

    for settings in (after_install, after_session):
        assert _gaia_triples(workspace, settings) == []
        assert settings["hooks"] == {"PreToolUse": [{"matcher": "Bash", "hooks": [USER_GUARD]}]}


def test_d_workspace_path_with_backslashes_and_spaces(tmp_path, monkeypatch):
    ws = tmp_path / "back\\slash dir" / "my ws"
    (ws / ".claude").mkdir(parents=True)
    monkeypatch.chdir(ws)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(plugin_setup, "_installed_under_node_modules", lambda: True)
    clear_path_cache()

    assert plugin_setup._sync_workspace_hooks() is True

    commands = [c for _, _, c in _gaia_triples(ws, _settings(ws))]
    hooks_abs = _hooks_abs(ws)
    assert f'sh "{hooks_abs}/launch.sh" "{hooks_abs}/pre_tool_use.py"' in commands
    assert "${CLAUDE_PLUGIN_ROOT}" not in json.dumps(_settings(ws))
    windows_dir = PureWindowsPath(r"C:\Users\Jo Doe\ws\.claude\hooks")
    rendered = plugin_setup.render_gaia_hooks({"PreToolUse": [_entry("Bash", GAIA_PRE)]}, windows_dir)
    assert rendered["PreToolUse"][0]["hooks"][0]["command"] == (
        "python3 C:/Users/Jo Doe/ws/.claude/hooks/pre_tool_use.py"
    )


def test_e_plugin_then_npm_then_npm_converges(workspace, tmp_path, monkeypatch):
    _write_settings(workspace, {"hooks": {"Notification": [{"hooks": [USER_GUARD]}]}})
    monkeypatch.setattr(plugin_setup, "_installed_under_node_modules", lambda: True)

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(tmp_path / "plugin-cache" / "gaia" / "5.5.0"))
    plugin_setup._sync_workspace_hooks()
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT")
    assert plugin_setup._sync_workspace_hooks() is True
    after_second = (workspace / ".claude" / "settings.local.json").read_bytes()

    assert plugin_setup._sync_workspace_hooks() is False
    assert (workspace / ".claude" / "settings.local.json").read_bytes() == after_second
    settings = _settings(workspace)
    assert settings["hooks"]["Notification"] == [{"hooks": [USER_GUARD]}]
    assert _gaia_triples(workspace, settings)


def test_f_no_live_definition_of_the_total_replace_writer():
    result = subprocess.run(
        ["git", "grep", "-n", "def setup_project_hooks", "--", "hooks", "bin"],
        cwd=_REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1, result.stdout


def test_h_migrates_a_settings_file_the_old_writers_left(workspace):
    """A settings.local.json holding the old additive merge's duplicates, the
    old total-replace writer's shape and a user hook on a Gaia event converges
    to each shipped triple exactly once with the user hook kept."""
    shipped = json.loads((_REPO_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    hooks_abs = _hooks_abs(workspace)
    old = plugin_setup.render_gaia_hooks(shipped, PureWindowsPath(hooks_abs))
    old["PreToolUse"] = old["PreToolUse"] + old["PreToolUse"][:2] + [
        {"matcher": "Bash", "hooks": [USER_GUARD]},
    ]
    _write_settings(workspace, {"permissions": {"allow": ["Read"]}, "hooks": old})

    res = helpers.merge_local_hooks(workspace)
    settings = _settings(workspace)

    triples = _gaia_triples(workspace, settings)
    expected = [
        (event, entry.get("matcher"), h["command"].replace("${CLAUDE_PLUGIN_ROOT}/hooks", hooks_abs))
        for event, entries in shipped.items() for entry in entries for h in entry["hooks"]
    ]
    assert res["action"] == "updated"
    assert sorted(triples) == sorted(expected)
    assert {"matcher": "Bash", "hooks": [USER_GUARD]} in settings["hooks"]["PreToolUse"]
    assert settings["permissions"] == {"allow": ["Read"]}
    assert helpers.merge_local_hooks(workspace)["action"] == "noop"


def test_g_npm_copy_defers_to_a_plugin_enabled_in_the_workspace(workspace, monkeypatch):
    """Both channels installed: the plugin owns registration, so the npm copy's
    session writes nothing Gaia's and the two stop undoing each other."""
    _write_settings(workspace, {"enabledPlugins": {"gaia@gaia-marketplace": True}})
    monkeypatch.setattr(plugin_setup, "_installed_under_node_modules", lambda: True)

    plugin_setup._sync_workspace_hooks()

    assert _gaia_triples(workspace, _settings(workspace)) == []
