"""gaia doctor judges the install by the channel that is actually active.

Every case builds a throwaway workspace: a plugin root (the directory
CLAUDE_PLUGIN_ROOT names), an npm copy under node_modules, the workspace's
settings files and the user's settings file, and a gaia.db reached through
GAIA_DB or GAIA_DATA_DIR.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import cli.doctor as doctor_mod  # noqa: E402

SHIPPED = {
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use.py"}]},
            {"matcher": "Read|Edit", "hooks": [
                {"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/pre_tool_use.py"}]},
        ],
        "SessionStart": [
            {"hooks": [
                {"type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py"}]},
        ],
    }
}


def _package(root: Path) -> Path:
    """A Gaia package tree: hooks.json plus the entrypoints it names."""
    hooks = root / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "hooks.json").write_text(json.dumps(SHIPPED))
    for name in ("pre_tool_use.py", "session_start.py"):
        (hooks / name).write_text("# hook\n")
    (root / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": "5.5.0"}))
    return root


def _npm_hooks(workspace: Path) -> dict:
    """What the npm channel writes: every shipped hook through .claude/hooks."""
    prefix = f"{(workspace / '.claude' / 'hooks').as_posix()}/"
    rendered = json.loads(json.dumps(SHIPPED["hooks"]).replace("${CLAUDE_PLUGIN_ROOT}/hooks/", prefix))
    return rendered


def _settings(workspace: Path, name: str, data: dict) -> None:
    claude = workspace / ".claude"
    claude.mkdir(exist_ok=True)
    (claude / name).write_text(json.dumps(data))


def _db(path: Path, version: int, minimum) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT, "
        "description TEXT, min_code_version INTEGER)"
    )
    con.execute("INSERT INTO schema_version VALUES (?, 'now', 'seal', ?)", (version, minimum))
    con.commit()
    con.close()
    return path


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("GAIA_DB", raising=False)
    monkeypatch.setattr(doctor_mod, "_USER_SETTINGS_PATH", tmp_path / "user-settings.json", raising=False)
    monkeypatch.setattr(doctor_mod, "_KNOWN_MARKETPLACES_PATH", tmp_path / "known_marketplaces.json", raising=False)


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)
    return ws


PERMISSIONS = {"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm -rf /:*)"]}}


def test_plugin_without_workspace_hooks_is_green(tmp_path, workspace, monkeypatch):
    plugin = _package(tmp_path / "plugin")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    _settings(workspace, "settings.local.json", PERMISSIONS)

    registrations = doctor_mod.check_hook_registrations(workspace)
    settings = doctor_mod.check_settings(workspace)

    assert registrations["severity"] == "pass", registrations
    assert settings["severity"] == "pass", settings
    assert "No hooks configured" not in settings["detail"]


def test_hook_in_plugin_and_workspace_settings_is_a_duplicate(tmp_path, workspace, monkeypatch):
    plugin = _package(tmp_path / "plugin")
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(plugin))
    _settings(workspace, "settings.local.json", {**PERMISSIONS, "hooks": _npm_hooks(workspace)})

    r = doctor_mod.check_hook_registrations(workspace)

    assert r["severity"] == "error"
    assert "PreToolUse" in r["detail"] and "2" in r["detail"]
    assert "gaia scan" not in r.get("fix", "")


def test_plugin_enabled_only_for_the_user_plus_npm_is_a_duplicate(tmp_path, workspace):
    """The plugin enabled in ~/.claude/settings.json and an npm copy wired in
    the workspace run every hook twice even though no CLAUDE_PLUGIN_ROOT is set."""
    _package(workspace / "node_modules" / "@jaguilar87" / "gaia")
    doctor_mod._USER_SETTINGS_PATH.write_text(
        json.dumps({"enabledPlugins": {"gaia@gaia-marketplace": True}})
    )
    _settings(workspace, "settings.local.json", {**PERMISSIONS, "hooks": _npm_hooks(workspace)})

    channel = doctor_mod.check_install_channel(workspace)
    r = doctor_mod.check_hook_registrations(workspace)

    assert "plugin" in channel["detail"] and "npm" in channel["detail"]
    assert r["severity"] == "error"
    assert "user settings" in r["detail"]


def test_registered_command_whose_file_is_gone_is_an_error(tmp_path, workspace):
    """The orphan an `npm uninstall` leaves: settings still name a hook file
    that no longer exists, and the fix is a gaia command that removes it."""
    _settings(workspace, "settings.local.json", {**PERMISSIONS, "hooks": _npm_hooks(workspace)})

    r = doctor_mod.check_hook_commands(workspace)

    assert r["severity"] == "error"
    assert "pre_tool_use.py" in r["detail"]
    assert r["fix"].startswith("`gaia ")
    assert "gaia scan" not in r["fix"]


def test_registered_command_with_missing_interpreter_is_an_error(tmp_path, workspace):
    _package(workspace / "node_modules" / "@jaguilar87" / "gaia")
    hooks_dir = workspace / ".claude" / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "session_start.py").write_text("# hook\n")
    command = f"no-such-python-xyz {hooks_dir.as_posix()}/session_start.py"
    _settings(workspace, "settings.local.json", {
        **PERMISSIONS,
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": command}]}]},
    })

    r = doctor_mod.check_hook_commands(workspace)

    assert r["severity"] == "error"
    assert "no-such-python-xyz" in r["detail"]
    assert "gaia install" in r["fix"]


def test_database_ahead_inside_the_window_warns(tmp_path, monkeypatch):
    expected = doctor_mod.EXPECTED_SCHEMA_VERSION
    db = _db(tmp_path / "gaia.db", expected + 1, expected)
    monkeypatch.setenv("GAIA_DB", str(db))

    r = doctor_mod.check_schema_version()

    assert r["severity"] == "warning", r
    assert f"v{expected + 1}" in r["detail"]


def test_database_ahead_outside_the_window_errors(tmp_path, monkeypatch):
    expected = doctor_mod.EXPECTED_SCHEMA_VERSION
    db = _db(tmp_path / "gaia.db", expected + 1, expected + 1)
    monkeypatch.setenv("GAIA_DB", str(db))

    r = doctor_mod.check_schema_version()

    assert r["severity"] == "error", r
    assert "`gaia update`" in r["fix"]


def test_database_behind_names_gaia_migrate(tmp_path, monkeypatch):
    expected = doctor_mod.EXPECTED_SCHEMA_VERSION
    monkeypatch.setenv("GAIA_DB", str(_db(tmp_path / "gaia.db", expected - 1, None)))

    r = doctor_mod.check_schema_version()

    assert r["severity"] == "warning"
    assert "gaia migrate" in r["fix"]


def test_schema_check_reads_the_database_under_gaia_data_dir(tmp_path, monkeypatch):
    expected = doctor_mod.EXPECTED_SCHEMA_VERSION
    data_dir = tmp_path / "other-data"
    data_dir.mkdir()
    db = _db(data_dir / "gaia.db", expected + 1, expected)
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))

    r = doctor_mod.check_schema_version()

    assert str(db.resolve()) in r["detail"], r


def _two_installs(tmp_path: Path, monkeypatch) -> "tuple[Path, Path]":
    """A tmp HOME whose installed_plugins.json lists the published marketplace
    install first (A) and the dev install second (B), both with a real tree."""
    home = tmp_path / "home"
    published = _package(home / ".claude" / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.5.0")
    dev = _package(tmp_path / "dev-plugin")
    for tree in (published, dev):
        for name in ("agents", "skills"):
            (tree / name).mkdir()
    record = home / ".claude" / "plugins" / "installed_plugins.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps({"version": 2, "plugins": {
        "gaia@gaia-marketplace": [{"scope": "user", "installPath": str(published)}],
        "gaia@gaia-dev": [{"scope": "user", "installPath": str(dev)}],
    }}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(doctor_mod, "_INSTALLED_PLUGINS_PATH", record)
    return published, dev


def test_checks_name_the_enabled_install_not_the_first_gaia_entry(tmp_path, workspace, monkeypatch):
    published, dev = _two_installs(tmp_path, monkeypatch)
    _settings(workspace, "settings.local.json", {
        "enabledPlugins": {"gaia@gaia-marketplace": False, "gaia@gaia-dev": True},
    })

    assert doctor_mod._plugin_tree(workspace) == dev
    for r in (
        doctor_mod.check_symlinks(workspace),
        doctor_mod.check_plugin_mode(workspace),
        doctor_mod.check_workspace_initialized(workspace),
    ):
        assert str(dev) in r["detail"], r
        assert str(published) not in r["detail"], r


def test_a_disabled_install_in_the_workspace_overrides_the_user_settings(tmp_path, workspace, monkeypatch):
    """settings.local.json turns the marketplace copy off; the user's file, read
    last, still lists it on. The workspace's word stands."""
    published, dev = _two_installs(tmp_path, monkeypatch)
    doctor_mod._USER_SETTINGS_PATH.write_text(
        json.dumps({"enabledPlugins": {"gaia@gaia-marketplace": True, "gaia@gaia-dev": True}})
    )
    _settings(workspace, "settings.local.json", {"enabledPlugins": {"gaia@gaia-marketplace": False}})

    assert doctor_mod._plugin_tree(workspace) == dev


def test_workspace_false_beats_user_true_for_the_channel_too(tmp_path, workspace, monkeypatch):
    """The workspace switches the only install off while the user's file has it on:
    the channel check and the tree pick must agree that no plugin is active."""
    _two_installs(tmp_path, monkeypatch)
    doctor_mod._USER_SETTINGS_PATH.write_text(json.dumps({"enabledPlugins": {"gaia@gaia-marketplace": True}}))
    _settings(workspace, "settings.local.json", {"enabledPlugins": {"gaia@gaia-marketplace": False}})

    channels = doctor_mod._active_channels(workspace)
    r = doctor_mod.check_install_channel(workspace)

    assert channels["plugin"] is False, channels
    assert doctor_mod._plugin_tree(workspace) is None
    assert r["severity"] == "warning" and "not enabled" in r["detail"], r


def _gaia_dev_marketplace(tmp_path: Path, workspace: Path, monkeypatch, kind: str) -> "tuple[Path, Path]":
    """gaia@gaia-dev enabled in the workspace, recorded with a versioned cache copy
    as installPath (stale, no identity) and a marketplace location (live) of *kind*."""
    home = tmp_path / "home"
    stale = _package(home / ".claude" / "plugins" / "cache" / "gaia-dev" / "gaia" / "5.5.0")
    live = _package(tmp_path / "dev-plugin")
    (live / "agents").mkdir()
    (live / "agents" / "gaia-orchestrator.md").write_text("# orchestrator\n")
    (live / "settings.json").write_text(json.dumps({"agent": "gaia-orchestrator"}))
    source = {"source": "directory", "path": str(live)} if kind == "directory" else {"source": kind, "repo": "o/r"}
    plugins = home / ".claude" / "plugins"
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        "gaia@gaia-dev": [{"scope": "local", "projectPath": str(workspace), "installPath": str(stale)}],
    }}))
    (plugins / "known_marketplaces.json").write_text(json.dumps({
        "gaia-dev": {"source": source, "installLocation": str(live)},
    }))
    monkeypatch.setattr(doctor_mod, "_INSTALLED_PLUGINS_PATH", plugins / "installed_plugins.json")
    monkeypatch.setattr(doctor_mod, "_KNOWN_MARKETPLACES_PATH", plugins / "known_marketplaces.json", raising=False)
    _settings(workspace, "settings.local.json", {"enabledPlugins": {"gaia@gaia-dev": True}})
    return stale, live


def test_a_directory_marketplace_is_read_where_the_host_loads_it(tmp_path, workspace, monkeypatch):
    _, live = _gaia_dev_marketplace(tmp_path, workspace, monkeypatch, "directory")

    assert doctor_mod._plugin_tree(workspace) == live
    identity = doctor_mod.check_identity(workspace)
    assert identity["severity"] == "pass", identity


def test_a_git_marketplace_is_read_from_its_install_path(tmp_path, workspace, monkeypatch):
    install_path, _ = _gaia_dev_marketplace(tmp_path, workspace, monkeypatch, "github")

    assert doctor_mod._plugin_tree(workspace) == install_path


def test_installed_but_none_enabled_says_so(tmp_path, workspace, monkeypatch):
    _two_installs(tmp_path, monkeypatch)
    _settings(workspace, "settings.local.json", PERMISSIONS)

    assert doctor_mod._plugin_tree(workspace) is None
    r = doctor_mod.check_install_channel(workspace)

    assert r["severity"] == "warning", r
    assert "gaia@gaia-marketplace" in r["detail"] and "gaia@gaia-dev" in r["detail"]
    assert "not enabled" in r["detail"]


def test_empty_provenance_is_never_pass(workspace):
    provenance = doctor_mod.check_install_provenance(workspace)
    commands = doctor_mod.check_hook_commands(workspace)

    assert provenance["severity"] != "pass"
    assert commands["severity"] != "pass"
