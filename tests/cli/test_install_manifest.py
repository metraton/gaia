"""
Install manifest: `gaia install` records every file and key it writes, and
`gaia uninstall` reverts exactly that -- nothing the user created, nothing
outside the workspace unless opted in, never the database.

Every test runs the real install and uninstall commands against a temporary
workspace and a temporary HOME; only the DB bootstrap and the seeders are
stubbed, so the database is a file whose hash the tests can hold.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _manifest, cleanup, install, uninstall, update  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temporary HOME, a workspace, and a DB file outside HOME."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".profile").write_text("# user profile\n")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "gaia-data"
    data_dir.mkdir()
    db = data_dir / "gaia.db"
    db.write_bytes(b"SQLite format 3\x00 fixture")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)
    return argparse.Namespace(home=home, workspace=workspace, db=db, snapshots=data_dir / "snapshots")


@pytest.fixture
def stubbed_db():
    """Stub the DB bootstrap and seeders; yields the seeder mocks."""
    with patch.object(install, "_run_bootstrap", return_value={"rc": 0, "detail": ""}), \
         patch.object(install, "_seed_contract_permissions", return_value={"action": "noop", "details": ""}) as perms, \
         patch.object(install, "_seed_surface_routing", return_value={"action": "noop", "details": ""}) as routing:
        yield argparse.Namespace(perms=perms, routing=routing)


def _install_args(workspace: Path, **overrides) -> argparse.Namespace:
    ns = argparse.Namespace(
        postinstall=False, quiet=True, verbose=False, db_path=None,
        workspace=str(workspace), host="claude_code", skip_workspace=False,
        path=False, no_path=False, strict_wiring=False,
    )
    for key, value in overrides.items():
        setattr(ns, key, value)
    return ns


def _run_install(workspace: Path, **overrides) -> int:
    return install.cmd_install(_install_args(workspace, **overrides))


def _run_uninstall(env) -> dict:
    args = argparse.Namespace(
        preuninstall=False, workspace=str(env.workspace), dry_run=False, quiet=True,
        json=False, db_path=str(env.db), no_backup=False, snapshot_dir=str(env.snapshots),
    )
    assert uninstall.cmd_uninstall(args) == 0
    return _manifest.load(env.workspace) or {}


def _tree(root: Path) -> dict:
    """Every entry under *root*: symlink target, file bytes, or directory -- links not followed."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                out[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                out[rel] = ("dir",)
            else:
                out[rel] = ("file", path.read_bytes())
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _user_settings(workspace: Path, hooks: dict | None = None) -> None:
    claude = workspace / ".claude"
    claude.mkdir(exist_ok=True)
    local = {"env": {"USER_VAR": "keep"}, "permissions": {"allow": ["UserTool(x)"]}}
    if hooks is not None:
        local["hooks"] = hooks
    (claude / "settings.local.json").write_text(json.dumps(local, indent=2) + "\n")


def test_a_install_without_opt_in_writes_nothing_outside_and_records_every_write(env, stubbed_db):
    home_before = _tree(env.home)

    assert _run_install(env.workspace) == 0

    assert _tree(env.home) == home_before
    manifest = _manifest.load(env.workspace)
    entries = {e["path"]: e for e in manifest["entries"]}
    assert manifest["channel"] == "npm"
    assert not any(Path(p).is_absolute() for p in entries)
    for rel in (".claude", ".claude/settings.json", ".claude/settings.local.json", ".claude/agents", ".claude/hooks"):
        assert rel in entries, rel
    keys = {tuple(op["path"]) for op in entries[".claude/settings.local.json"]["json_ops"]}
    assert {("agent",), ("attribution",), ("hooks",), ("permissions",)} <= keys
    written = set(_tree(env.workspace)) - {".claude/gaia-manifest.json"}
    top_level = {p for p in written if p.count("/") <= 1 and not p.startswith(".claude/agents/")}
    assert top_level <= set(entries), top_level - set(entries)


def test_b_launcher_opt_in_is_recorded_and_reverted(env, stubbed_db):
    home_before = _tree(env.home)

    # The PATH-shadow advisory asks npm for its prefix, and npm logs into
    # ~/.npm on its own; that probe is not a Gaia write, so it is held out.
    with patch.object(install, "_warn_launcher_shadowed", return_value=None), \
         patch.object(install, "_warn_launcher_dir_absent", return_value=None):
        assert _run_install(env.workspace, path=True) == 0

    launcher = env.home / ".local" / "bin" / "gaia"
    assert launcher.is_symlink()
    paths = {e["path"] for e in _manifest.load(env.workspace)["entries"]}
    assert str(launcher) in paths
    _run_uninstall(env)
    assert _tree(env.home) == home_before


def test_c_user_claude_md_and_settings_json_survive_byte_for_byte(env, stubbed_db):
    claude_md = env.workspace / "CLAUDE.md"
    claude_md.write_bytes(b"# my own identity\r\nno trailing newline")
    (env.workspace / ".claude").mkdir()
    settings = env.workspace / ".claude" / "settings.json"
    settings.write_bytes(b'{"model":  "opus"}')

    assert _run_install(env.workspace) == 0
    _run_uninstall(env)

    assert claude_md.read_bytes() == b"# my own identity\r\nno trailing newline"
    assert settings.read_bytes() == b'{"model":  "opus"}'


def test_d_install_then_uninstall_restores_workspace_home_and_keeps_db(env, stubbed_db):
    _user_settings(env.workspace)
    workspace_before, home_before, db_before = _tree(env.workspace), _tree(env.home), _sha(env.db)

    assert _run_install(env.workspace) == 0
    assert _tree(env.workspace) != workspace_before
    _run_uninstall(env)

    assert _tree(env.workspace) == workspace_before
    assert _tree(env.home) == home_before
    assert _sha(env.db) == db_before


def test_d_fresh_workspace_is_left_empty(env, stubbed_db):
    assert _run_install(env.workspace) == 0
    _run_uninstall(env)
    assert _tree(env.workspace) == {}


def test_e_plugin_channel_writes_hidden_attribution(env, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
    from modules.core import plugin_setup

    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(Path(__file__).resolve().parents[2]))
    monkeypatch.chdir(env.workspace)

    assert plugin_setup.setup_project_permissions() is True

    local = json.loads((env.workspace / ".claude" / "settings.local.json").read_text())
    assert local["attribution"] == {"commit": "", "pr": "", "sessionUrl": False}
    assert plugin_setup.setup_project_permissions() is False


def test_f_install_without_manifest_is_adopted_and_uninstalls_to_pre_gaia_state(env, stubbed_db):
    _user_settings(env.workspace)
    (env.workspace / ".claude" / "settings.json").write_text("{}\n")
    (env.workspace / "CLAUDE.md").write_text("mine\n")
    pre_gaia = _tree(env.workspace)
    assert _run_install(env.workspace) == 0
    _manifest.manifest_path(env.workspace).unlink()
    assert _manifest.is_unmanifested_install(env.workspace)

    assert _run_install(env.workspace) == 0

    manifest = _manifest.load(env.workspace)
    assert manifest is not None
    assert ".claude/agents" in {e["path"] for e in manifest["entries"]}
    _run_uninstall(env)
    assert _tree(env.workspace) == pre_gaia


def test_f_reinstall_keeps_the_original_baseline(env, stubbed_db):
    _user_settings(env.workspace)
    pre_gaia = _tree(env.workspace)

    assert _run_install(env.workspace) == 0
    assert _run_install(env.workspace) == 0
    _run_uninstall(env)

    assert _tree(env.workspace) == pre_gaia


def test_g_update_is_install_and_fails_when_the_migration_fails(env, stubbed_db):
    args = _install_args(env.workspace, dry_run=False, json=False)
    assert update.cmd_update(args) == 0
    stubbed_db.perms.assert_called_once()
    stubbed_db.routing.assert_called_once()
    assert _manifest.load(env.workspace) is not None

    with patch.object(install, "_run_bootstrap", return_value={"rc": 3, "detail": "boom"}):
        assert update.cmd_update(_install_args(env.workspace, dry_run=False, json=False)) == 3


def test_h_user_hook_script_and_its_entry_survive_install_and_uninstall(env, stubbed_db):
    hooks_dir = env.workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    script = hooks_dir / "mi_hook_propio.py"
    script.write_bytes(b"print('mine')\n")
    user_entry = {"matcher": "Bash", "hooks": [{"type": "command", "command": f"python3 {script}"}]}
    _user_settings(env.workspace, hooks={"PreToolUse": [user_entry]})

    assert _run_install(env.workspace) == 0
    installed = json.loads((env.workspace / ".claude" / "settings.local.json").read_text())
    assert len(installed["hooks"]["PreToolUse"]) > 1
    _run_uninstall(env)

    assert script.read_bytes() == b"print('mine')\n"
    local = json.loads((env.workspace / ".claude" / "settings.local.json").read_text())
    assert local["hooks"] == {"PreToolUse": [user_entry]}
    assert "agent" not in local


def test_h_legacy_cleanup_keeps_a_user_hook_script_entry(env):
    hooks_dir = env.workspace / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True)
    script = hooks_dir / "mi_hook_propio.py"
    script.write_text("print('mine')\n")
    user_entry = {"hooks": [{"type": "command", "command": f"python3 {script}"}]}
    gaia_entry = {"hooks": [{"type": "command", "command": f"python3 {hooks_dir}/pre_tool_use.py"}]}
    hooks = {"PreToolUse": [gaia_entry, user_entry]}

    cleanup._strip_gaia_hooks(hooks, env.workspace)

    assert hooks == {"PreToolUse": [user_entry]}


def test_i_cleanup_has_no_ownership_predicate_of_its_own(env):
    assert not hasattr(cleanup, "_is_gaia_hook_command")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
    from modules.core import plugin_setup

    with patch.object(plugin_setup, "is_gaia_hook_command", return_value=False) as predicate:
        cleanup._strip_gaia_hooks({"Stop": [{"hooks": [{"command": "x"}]}]}, env.workspace)
    predicate.assert_called_once()
    assert predicate.call_args.args[:2] == ("x", env.workspace)


def test_json_ops_revert_keeps_keys_the_user_added_later():
    before = {"a": 1, "list": ["u"]}
    after = {"a": 2, "list": ["u", "g"], "new": {"k": 1}}
    ops = _manifest.json_ops(before, after)
    later = {**after, "user": True, "new": {"k": 1, "mine": 2}}

    assert _manifest.revert_json(after, ops) == before
    assert _manifest.revert_json(later, ops) == {"a": 1, "list": ["u"], "user": True, "new": {"mine": 2}}
