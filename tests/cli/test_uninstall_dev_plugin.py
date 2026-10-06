"""`gaia uninstall` takes back what `gaia dev --channel plugin` set up in a workspace."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (_REPO_ROOT, _REPO_ROOT / "bin"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from cli import _dev_plugin, _manifest  # noqa: E402


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GAIA_DB", raising=False)
    root = tmp_path / "ws"
    (root / ".claude").mkdir(parents=True)
    return root


def _fake_claude(monkeypatch, workspace: Path, directory: Path) -> list[tuple[str, ...]]:
    """A claude CLI on which gaia dev already registered gaia-dev and installed gaia@gaia-dev."""
    calls: list[tuple[str, ...]] = []
    listings = {
        ("plugin", "marketplace", "list", "--json"):
            [{"name": "gaia-dev", "installLocation": str(directory)}],
        ("plugin", "list", "--json"):
            [{"id": "gaia@gaia-dev", "scope": "local", "projectPath": str(workspace)}],
    }

    def claude(cwd: Path, *args: str) -> subprocess.CompletedProcess:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps(listings.get(args, {})), "")

    monkeypatch.setattr(_dev_plugin.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(_dev_plugin, "_claude", claude)
    return calls


@pytest.mark.parametrize("before", [
    {"model": "opus", "enabledPlugins": {"gaia@gaia-marketplace": True}},
    {"model": "opus"},
])
@pytest.mark.parametrize("channel", [None, "plugin"])
def test_uninstall_takes_back_the_plugin_channel_setup(workspace, monkeypatch, before, channel):
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps(before))
    directory = _dev_plugin.plugin_dir(workspace)
    directory.mkdir(parents=True)
    (directory / "marketplace.json").write_text("{}")
    calls = _fake_claude(monkeypatch, workspace, directory)
    assert _dev_plugin.select_dev_plugin(workspace)["action"] == "updated"

    if channel:
        artifacts = _manifest._uninstall_channel(
            workspace, {"entries": [], "channel": "plugin", "package_channels": []},
            channel, [], dry_run=False, package_manager_owns_package=False,
        )["artifacts"]
    else:
        artifacts = _manifest.uninstall(
            workspace, dry_run=False, package_manager_owns_package=False, channel=None,
        )["artifacts"]

    assert json.loads(settings.read_text()) == before
    assert not directory.exists()
    assert not _dev_plugin.selection_record(workspace).exists()
    assert ("plugin", "uninstall", "gaia@gaia-dev", "--scope", "local") in calls
    assert ("plugin", "marketplace", "remove", "gaia-dev", "--scope", "local") in calls
    assert {"unregister", "edit"} <= {a["action"] for a in artifacts}


def test_a_published_entry_changed_after_gaia_dev_is_left_alone(workspace, monkeypatch):
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({"enabledPlugins": {"gaia@gaia-marketplace": True}}))
    directory = _dev_plugin.plugin_dir(workspace)
    directory.mkdir(parents=True)
    _fake_claude(monkeypatch, workspace, directory)
    _dev_plugin.select_dev_plugin(workspace)
    doc = json.loads(settings.read_text())
    doc["enabledPlugins"]["gaia@gaia-marketplace"] = "user-choice"
    settings.write_text(json.dumps(doc))

    _dev_plugin.deselect_dev_plugin(workspace)

    assert json.loads(settings.read_text()) == {"enabledPlugins": {"gaia@gaia-marketplace": "user-choice"}}


_ENABLE = "claude plugin enable gaia@gaia-marketplace --scope local"


def _uninstall(workspace: Path) -> dict:
    return _manifest.uninstall(workspace, dry_run=False, package_manager_owns_package=False, channel=None)


def test_a_selection_without_a_record_leaves_the_published_plugin_disabled_and_says_how_to_enable_it(
        workspace, monkeypatch):
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({"enabledPlugins": {"gaia@gaia-dev": True, "gaia@gaia-marketplace": False}}))
    directory = _dev_plugin.plugin_dir(workspace)
    directory.mkdir(parents=True)
    _fake_claude(monkeypatch, workspace, directory)
    assert _dev_plugin.select_dev_plugin(workspace)["action"] == "noop"
    assert not _dev_plugin.selection_record(workspace).exists()

    kept = _uninstall(workspace)["kept"]

    assert json.loads(settings.read_text()) == {"enabledPlugins": {"gaia@gaia-marketplace": False}}
    assert [k["path"] for k in kept if _ENABLE in k["reason"]] == [str(settings)]


def test_a_published_entry_changed_after_gaia_dev_is_listed_with_how_to_enable_it(workspace, monkeypatch):
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({}))
    directory = _dev_plugin.plugin_dir(workspace)
    directory.mkdir(parents=True)
    _fake_claude(monkeypatch, workspace, directory)
    _dev_plugin.select_dev_plugin(workspace)
    doc = json.loads(settings.read_text())
    del doc["enabledPlugins"]["gaia@gaia-marketplace"]
    settings.write_text(json.dumps(doc))

    kept = _uninstall(workspace)["kept"]

    assert json.loads(settings.read_text()) == {}
    assert [k["path"] for k in kept if _ENABLE in k["reason"]] == [str(settings)]


def test_without_the_claude_cli_the_build_stays_for_the_marketplace_that_still_points_at_it(
        workspace, monkeypatch):
    settings = workspace / ".claude" / "settings.local.json"
    settings.write_text(json.dumps({"enabledPlugins": {"gaia@gaia-marketplace": True}}))
    directory = _dev_plugin.plugin_dir(workspace)
    directory.mkdir(parents=True)
    _fake_claude(monkeypatch, workspace, directory)
    _dev_plugin.select_dev_plugin(workspace)
    monkeypatch.setattr(_dev_plugin.shutil, "which", lambda name: None)

    result = _uninstall(workspace)

    assert directory.is_dir()
    assert json.loads(settings.read_text()) == {"enabledPlugins": {"gaia@gaia-marketplace": True}}
    reasons = [k["reason"] for k in result["kept"] if k["path"] == str(directory)]
    assert any("claude plugin marketplace remove gaia-dev --scope local" in r for r in reasons)
    assert any("still points at it" in r for r in reasons)
    assert str(directory) not in {a["path"] for a in result["artifacts"] if a["action"] == "remove"}
