"""The Claude Code plugin channel and the OpenCode channel coexist in one workspace from one build."""

import argparse
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_ROOT / "bin"), str(_ROOT / "hooks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cli import _manifest  # noqa: E402
from cli import doctor as doctor_mod  # noqa: E402
from cli import install as install_mod  # noqa: E402
from cli import uninstall as uninstall_mod  # noqa: E402

_PLUGIN_SETTINGS = {"enabledPlugins": {"gaia@gaia-dev": True}}
_SESSION_ALLOW = "Bash(gaia *)"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch, _isolate_gaia_data_dir):
    """Keep the user's settings and database out; the global install steps succeed without running."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(install_mod, "_run_bootstrap", lambda **_: {"rc": 0})
    skipped = {"action": "skipped", "details": "test"}
    for step in ("_seed_contract_permissions", "_seed_surface_routing"):
        monkeypatch.setattr(install_mod, step, lambda **_: skipped)
    monkeypatch.setattr(install_mod, "_workspace_status", lambda *_: skipped)


def _plugin_session(workspace: Path, tmp_path: Path) -> None:
    """What a plugin session writes into the workspace, recorded the way plugin_setup records it."""
    plugin_hooks = tmp_path / "plugin" / "hooks"
    plugin_hooks.mkdir(parents=True)
    tracked = _manifest.track(workspace, whole_claude_dir=False)
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({**_PLUGIN_SETTINGS, "permissions": {"allow": [_SESSION_ALLOW]}}))
    (workspace / ".claude" / "hooks").symlink_to(plugin_hooks)
    assert _manifest.record_tracked(tracked, channel="plugin", version="0") is not None


def _package_copy(workspace: Path) -> Path:
    """The package copy a package manager leaves under node_modules for the OpenCode channel."""
    package = workspace / "node_modules" / "@jaguilar87" / "gaia"
    package.mkdir(parents=True)
    (package / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": "0"}))
    return package


@pytest.fixture
def coexisting(tmp_path, capsys):
    """A plugin workspace into which `gaia install --channel opencode` then ran."""
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    (workspace / ".claude" / "settings.local.json").write_text(json.dumps(_PLUGIN_SETTINGS))
    _plugin_session(workspace, tmp_path)
    _package_copy(workspace)
    args = argparse.Namespace(workspace=str(workspace), channel="opencode", host=None, quiet=True)
    assert install_mod.cmd_install(args) == 0
    capsys.readouterr()
    return workspace


def _uninstall(workspace: Path, channel: str | None, capsys) -> tuple[int, dict]:
    args = argparse.Namespace(workspace=str(workspace), channel=channel, json=True, no_backup=True,
                              dry_run=False, quiet=False, preuninstall=False, db_path=None, snapshot_dir=None)
    rc = uninstall_mod.cmd_uninstall(args)
    captured = capsys.readouterr()
    return rc, json.loads(captured.out) if rc == 0 else {"stderr": captured.err}


def _local_settings(workspace: Path) -> dict:
    return json.loads((workspace / ".claude" / "settings.local.json").read_text())


def test_coexist_records_both_channels_from_one_build(coexisting):
    manifest = _manifest.load(coexisting)
    assert manifest["channel"] == "plugin"
    assert manifest["package_channels"] == ["opencode"]
    owners = {entry["path"]: _manifest.entry_channel(manifest, entry["path"]) for entry in manifest["entries"]}
    assert owners["opencode.json"] == owners[".opencode/skills"] == "opencode"
    assert owners[".claude/hooks"] == "plugin"
    assert owners[".claude/settings.local.json"] == "plugin"
    plugin = json.loads((coexisting / "opencode.json").read_text())["plugin"]
    assert plugin == [str(_ROOT / "opencode" / "plugin.ts")]


def test_coexist_doctor_shows_both_channels_without_error(coexisting):
    channel = doctor_mod.check_install_channel(coexisting)
    assert channel["severity"] == "pass", channel
    assert "plugin" in channel["detail"] and "opencode" in channel["detail"]
    assert "npm" not in channel["detail"]
    for check in (doctor_mod.check_hook_registrations, doctor_mod.check_hook_commands):
        assert check(coexisting)["severity"] != "error", check(coexisting)


def test_coexist_counts_each_registered_hook_once(coexisting):
    assert "hooks" not in _local_settings(coexisting)
    channels = doctor_mod._active_channels(coexisting)
    shipped = doctor_mod._shipped_hooks(channels)
    expected = {(event, matcher) for event, matcher, _ in doctor_mod._hook_handlers(shipped)}
    counted: dict = {}
    for _, event, matcher, _ in doctor_mod._gaia_registrations(coexisting, channels, shipped):
        counted[(event, matcher)] = counted.get((event, matcher), 0) + 1
    assert expected and set(counted) == expected
    assert set(counted.values()) == {1}


def test_coexist_uninstalling_opencode_keeps_the_plugin_channel(coexisting, capsys):
    rc, result = _uninstall(coexisting, "opencode", capsys)
    assert rc == 0, result
    assert not (coexisting / "opencode.json").exists()
    assert not (coexisting / ".opencode").exists()
    assert not (coexisting / "node_modules" / "@jaguilar87" / "gaia").exists()
    assert _local_settings(coexisting)["permissions"]["allow"] == [_SESSION_ALLOW]
    assert (coexisting / ".claude" / "hooks").is_symlink()
    manifest = _manifest.load(coexisting)
    assert manifest["channel"] == "plugin" and manifest["package_channels"] == []
    assert doctor_mod.check_install_channel(coexisting)["severity"] == "pass"

    rc, _ = _uninstall(coexisting, "plugin", capsys)
    assert rc == 0
    assert _manifest.load(coexisting) is None
    assert _local_settings(coexisting) == _PLUGIN_SETTINGS
    assert not (coexisting / ".claude" / "hooks").exists()


def test_coexist_uninstalling_the_plugin_keeps_the_opencode_channel(coexisting, capsys):
    rc, result = _uninstall(coexisting, "plugin", capsys)
    assert rc == 0, result
    assert _local_settings(coexisting) == _PLUGIN_SETTINGS
    assert not (coexisting / ".claude" / "hooks").exists()
    assert json.loads((coexisting / "opencode.json").read_text())["plugin"]
    assert any((coexisting / ".opencode" / "skills").iterdir())
    assert (coexisting / "node_modules" / "@jaguilar87" / "gaia" / "package.json").is_file()
    manifest = _manifest.load(coexisting)
    assert manifest["channel"] == "opencode" and manifest["package_channels"] == ["opencode"]
    assert {_manifest.entry_channel(manifest, entry["path"]) for entry in manifest["entries"]} == {"opencode"}

    rc, _ = _uninstall(coexisting, "opencode", capsys)
    assert rc == 0
    assert _manifest.load(coexisting) is None
    assert not (coexisting / "opencode.json").exists()


def test_coexist_uninstall_of_a_channel_not_recorded_fails_naming_the_recorded_ones(coexisting, capsys):
    before = (coexisting / ".claude" / "gaia-manifest.json").read_bytes()
    rc, result = _uninstall(coexisting, "npm", capsys)
    assert rc == 1
    assert "opencode" in result["stderr"] and "plugin" in result["stderr"]
    assert (coexisting / ".claude" / "gaia-manifest.json").read_bytes() == before


def test_coexist_fix_hints_scope_the_uninstall_to_the_channel_that_goes(coexisting, capsys):
    assert _manifest.uninstall_command(coexisting, "opencode") == f"gaia uninstall --channel opencode --workspace {coexisting}"
    _manifest.record(coexisting, {}, channel="plugin", version="0", package_channels=["npm"])
    hint = doctor_mod._hook_fix(coexisting, {"plugin": True, "npm": coexisting / "node_modules"})
    assert f"`gaia uninstall --channel npm --workspace {coexisting}` then `npm uninstall" in hint

    rc, _ = _uninstall(coexisting, "npm", capsys)
    assert rc == 0
    assert not (coexisting / ".claude" / "hooks").exists()
    assert (coexisting / "opencode.json").is_file()
    assert _manifest.recorded_channels(_manifest.load(coexisting)) == ["opencode", "plugin"]


@pytest.mark.parametrize("channel", ["opencode", None])
def test_coexist_opencode_only_folder_uninstalls_without_a_manifest(tmp_path, capsys, channel):
    workspace = tmp_path / "oc"
    workspace.mkdir()
    users = {"plugin": ["./mine.ts"], "agent": {"mine": {"description": "the user's own"}}}
    (workspace / "opencode.json").write_text(json.dumps(users))
    args = argparse.Namespace(workspace=str(workspace), channel="opencode", host=None, quiet=True)
    assert install_mod.cmd_install(args) == 0
    capsys.readouterr()
    assert not (workspace / ".claude").exists()
    assert any((workspace / ".opencode" / "skills").iterdir())

    rc, result = _uninstall(workspace, channel, capsys)
    assert rc == 0, result
    assert json.loads((workspace / "opencode.json").read_text()) == users
    assert not (workspace / ".opencode").exists()
    assert not (workspace / ".claude").exists()


def test_coexist_uninstall_without_a_channel_still_takes_back_everything(coexisting, capsys):
    rc, _ = _uninstall(coexisting, None, capsys)
    assert rc == 0
    assert _manifest.load(coexisting) is None
    assert not (coexisting / "opencode.json").exists()
    assert _local_settings(coexisting) == _PLUGIN_SETTINGS
