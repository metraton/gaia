"""The PATH launcher keeps running the current Gaia across a plugin upgrade, in a temporary HOME holding a fake plugin cache."""

import argparse
import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _root in (_REPO / "bin", _REPO / "hooks"):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

from cli import _manifest, doctor, install  # noqa: E402
from modules.core import plugin_setup  # noqa: E402

MARKETPLACE = "gaia-marketplace"
KEY = f"gaia@{MARKETPLACE}"


def _package(root: Path, version: str) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "gaia").write_text("#!/usr/bin/env python3\n")
    (root / "package.json").write_text(json.dumps(
        {"name": "@jaguilar87/gaia", "version": version, "bin": {"gaia": "bin/gaia"}}))
    return root


def _record(home: Path, installs: dict, marketplaces: dict) -> None:
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    (plugins / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {
        key: [{"scope": "user", "installPath": str(path)}] for key, path in installs.items()}}))
    (plugins / "known_marketplaces.json").write_text(json.dumps(marketplaces))


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A temporary HOME with Gaia A installed from the marketplace and a workspace."""
    home = tmp_path / "home"
    cache = home / ".claude" / "plugins" / "cache" / MARKETPLACE / "gaia"
    a, b = _package(cache / "5.7.1", "5.7.1"), _package(cache / "5.7.2", "5.7.2")
    _record(home, {KEY: a}, {MARKETPLACE: {"source": {"source": "github", "repo": "metraton/gaia"}}})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)
    monkeypatch.chdir(workspace)
    return argparse.Namespace(home=home, a=a, b=b, workspace=workspace,
                              link=home / ".local" / "bin" / "gaia")


def _platform(windows: bool):
    return patch.object(install.sys, "platform", "win32" if windows else "linux")


def _session_start(root: Path, monkeypatch) -> None:
    """The plugin's SessionStart setup, launched from *root*, with the workspace steps held out."""
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))
    with patch.object(plugin_setup, "recorded_in_manifest", contextlib.nullcontext), \
         patch.object(plugin_setup, "ensure_plugin_registry"), \
         patch.object(plugin_setup, "setup_project_permissions", return_value=False), \
         patch.object(plugin_setup, "_sync_workspace_hooks", return_value=False):
        plugin_setup.run_first_time_setup()


def _runs(env, windows: bool) -> Path:
    if not windows:
        return env.link.resolve().parent.parent
    texts = [env.link.with_suffix(s).read_text(encoding="utf-8-sig") for s in (".cmd", ".ps1")]
    roots = {r for r in (env.a, env.b) if all(str(r / "bin" / "gaia") in t for t in texts)}
    assert len(roots) == 1, texts
    return roots.pop()


@pytest.mark.parametrize("windows", [False, True], ids=["posix", "windows"])
def test_upgrade_from_a_to_b_leaves_the_launcher_running_b(home, monkeypatch, windows):
    with _platform(windows):
        assert install._install_path_launcher(workspace=home.workspace,
                                              gaia_bin=home.a / "bin" / "gaia")["action"] == "created"
        assert _runs(home, windows) == home.a

        _record(home.home, {KEY: home.b}, {MARKETPLACE: {"source": {"source": "github"}}})
        (home.a / ".orphaned_at").write_text("1762700000000")
        _session_start(home.b, monkeypatch)

        assert _runs(home, windows) == home.b


@pytest.mark.parametrize("windows", [False, True], ids=["posix", "windows"])
def test_a_dev_plugin_session_never_retargets_the_launcher(home, monkeypatch, windows):
    dev = _package(home.home / ".claude" / "plugins" / "cache" / "gaia-dev" / "gaia" / "5.7.2", "5.7.2")
    served = _package(home.home / ".gaia" / "cache" / "dev-plugin" / "ws", "5.7.2")
    with _platform(windows):
        install._install_path_launcher(workspace=home.workspace, gaia_bin=home.a / "bin" / "gaia")
        _record(home.home, {KEY: home.b, "gaia@gaia-dev": dev},
                {MARKETPLACE: {"source": {"source": "github"}},
                 "gaia-dev": {"source": {"source": "directory", "path": str(served)},
                              "installLocation": str(served)}})
        for root in (dev, served):
            _session_start(root, monkeypatch)

        assert _runs(home, windows) == home.a


def test_plugin_only_install_path_writes_the_launcher(home):
    (home.workspace / ".claude").mkdir()
    (home.workspace / ".claude" / "settings.json").write_text(json.dumps({"enabledPlugins": {KEY: True}}))
    args = argparse.Namespace(postinstall=False, quiet=True, verbose=False, db_path=None,
                              workspace=str(home.workspace), channel=None, host=None,
                              skip_workspace=False, path=True, no_path=False, strict_wiring=False)
    with patch.object(install, "_run_bootstrap", return_value={"rc": 0, "detail": ""}), \
         patch.object(install, "_seed_contract_permissions", return_value={"action": "noop", "details": ""}), \
         patch.object(install, "_seed_surface_routing", return_value={"action": "noop", "details": ""}), \
         patch.object(install, "_warn_launcher_shadowed", return_value=None), \
         patch.object(install, "_warn_launcher_dir_absent", return_value=None):
        assert install.detect_channels(home.workspace) == ()
        assert install.cmd_install(args) == 0

    assert home.link.resolve() == install._gaia_entrypoint().resolve()
    assert str(home.link) in {e["path"] for e in _manifest.load(home.workspace)["entries"]}


def test_doctor_reports_a_launcher_left_on_an_orphaned_version(home):
    install._install_path_launcher(workspace=home.workspace, gaia_bin=home.a / "bin" / "gaia")
    _record(home.home, {KEY: home.b}, {MARKETPLACE: {"source": {"source": "github"}}})

    with patch.object(doctor.shutil, "which", return_value=str(home.link)), \
         patch.object(doctor, "_package_root", return_value=home.a):
        result = doctor.check_path_gaia()

    assert result["severity"] == "warning", result
    assert str(home.b) in result["detail"] + result.get("fix", "")
