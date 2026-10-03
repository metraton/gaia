"""
`gaia uninstall` on an npm-shaped workspace: it takes back or reports everything
Gaia left, keeps everything the user owns, and its dry run says exactly what
the real run does.

The real install and uninstall commands run against a temporary HOME,
GAIA_DATA_DIR and workspace; only the DB bootstrap and seeders are stubbed.
"""

import argparse
import hashlib
import io
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _manifest, dev, install, uninstall  # noqa: E402

_GAIA = "@jaguilar87/gaia"
_USER_SETTINGS = {"env": {"USER_VAR": "keep"}, "permissions": {"allow": ["UserTool(x)"]}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "gaia-data"
    data_dir.mkdir()
    db = data_dir / "gaia.db"
    db.write_bytes(b"SQLite format 3\x00 fixture")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GAIA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GAIA_DB", str(db))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("INIT_CWD", raising=False)
    with patch.object(install, "_run_bootstrap", return_value={"rc": 0, "detail": ""}), \
         patch.object(install, "_seed_contract_permissions", return_value={"action": "noop", "details": ""}), \
         patch.object(install, "_seed_surface_routing", return_value={"action": "noop", "details": ""}), \
         patch.object(install, "_workspace_status", return_value={"action": "noop", "details": ""}), \
         patch.object(install, "_warn_launcher_shadowed", return_value=None), \
         patch.object(install, "_warn_launcher_dir_absent", return_value=None):
        yield argparse.Namespace(
            home=home, workspace=workspace, db=db, snapshots=data_dir / "snapshots", tmp=tmp_path,
        )


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")


def _npm_workspace(workspace: Path) -> None:
    """What the package manager leaves before `gaia install` runs: a user dep beside Gaia's."""
    deps = {_GAIA: "file:gaia.tgz", "left-pad": "^1.0.0"}
    _write_json(workspace / "package.json", {"name": "consumer", "version": "1.0.0", "dependencies": deps})
    _write_json(workspace / "package-lock.json", {
        "name": "consumer", "lockfileVersion": 3,
        "packages": {
            "": {"name": "consumer", "dependencies": deps},
            f"node_modules/{_GAIA}": {"version": "5.5.0", "resolved": "file:gaia.tgz"},
            "node_modules/left-pad": {"version": "1.0.0"},
        },
    })
    (workspace / "pnpm-lock.yaml").write_text(f"importers:\n  .:\n    dependencies:\n      '{_GAIA}':\n")
    modules = workspace / "node_modules"
    package = modules / _GAIA
    _write_json(package / "package.json", {"name": _GAIA, "version": "5.5.0"})
    (package / "bin").mkdir()
    (package / "bin" / "gaia").write_text("#!/usr/bin/env python3\n")
    (modules / "left-pad").mkdir()
    (modules / "left-pad" / "index.js").write_text("module.exports = 1\n")
    (modules / ".bin").mkdir()
    (modules / ".bin" / "gaia").symlink_to(f"../{_GAIA}/bin/gaia")
    (modules / ".bin" / "left-pad").symlink_to("../left-pad/index.js")
    _write_json(workspace / ".claude" / "settings.local.json", _USER_SETTINGS)


def _install(env, **overrides) -> None:
    args = argparse.Namespace(
        postinstall=False, quiet=True, verbose=False, db_path=None, workspace=str(env.workspace),
        skip_workspace=False, path=True, no_path=False, strict_wiring=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    assert install.install_channels(args, ("npm", "opencode")) == 0


def _uninstall(env, *, dry_run: bool = False, preuninstall: bool = False) -> dict:
    args = argparse.Namespace(
        preuninstall=preuninstall, workspace=str(env.workspace), dry_run=dry_run, quiet=False,
        json=True, db_path=str(env.db), no_backup=False, snapshot_dir=str(env.snapshots),
    )
    out = io.StringIO()
    with redirect_stdout(out):
        assert uninstall.cmd_uninstall(args) == 0
    return json.loads(out.getvalue())["manifest"]


def _tree(root: Path) -> dict:
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                found[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                found[rel] = ("dir",)
            else:
                found[rel] = ("file", path.read_bytes())
    return found


def _dev_pack_cache(workspace: Path) -> Path:
    attempt = dev.default_pack_dest(workspace) / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "jaguilar87-gaia-5.5.0.tgz").write_bytes(b"tarball")
    return attempt.parent


def _launcher(env) -> Path:
    return env.home / ".local" / "bin" / "gaia"


def _wire(env) -> Path:
    """An installed npm workspace with the leftovers a real history produces; returns the dev-pack cache."""
    _npm_workspace(env.workspace)
    _install(env)
    return _dev_pack_cache(env.workspace)


def test_uninstall_takes_back_gaia_and_keeps_the_user(env):
    cache = _wire(env)
    claude_hooks = env.workspace / ".claude" / "hooks"
    elsewhere = env.tmp / "released-worktree-hooks"
    claude_hooks.unlink()
    claude_hooks.symlink_to(elsewhere)
    launcher = _launcher(env)
    launcher.unlink()
    launcher.symlink_to(env.tmp / "gone" / "bin" / "gaia")
    db_before = hashlib.sha256(env.db.read_bytes()).hexdigest()
    (env.workspace / ".claude" / ".hooks_state").mkdir()
    (env.workspace / ".claude" / ".hooks_state" / "pending.json").write_text("{}")
    violations = env.workspace / ".claude" / "logs" / "commit-violations.jsonl"
    violations.parent.mkdir()
    violations.write_text("{}\n")

    result = _uninstall(env)

    workspace = env.workspace
    assert not os.path.lexists(workspace / ".claude" / ".hooks_state")
    assert violations.is_file()
    assert not os.path.lexists(claude_hooks)
    assert not os.path.lexists(workspace / "opencode.json")
    assert not os.path.lexists(workspace / ".opencode")
    assert not os.path.lexists(launcher)
    assert not cache.exists()
    assert not os.path.lexists(workspace / "node_modules" / _GAIA)
    assert not os.path.lexists(workspace / "node_modules" / ".bin" / "gaia")
    assert (workspace / "node_modules" / "left-pad" / "index.js").is_file()
    assert os.path.lexists(workspace / "node_modules" / ".bin" / "left-pad")
    package = json.loads((workspace / "package.json").read_text())
    assert package["dependencies"] == {"left-pad": "^1.0.0"}
    assert package["name"] == "consumer"
    lock = json.loads((workspace / "package-lock.json").read_text())
    assert set(lock["packages"]) == {"", "node_modules/left-pad"}
    assert lock["packages"][""]["dependencies"] == {"left-pad": "^1.0.0"}
    assert json.loads((workspace / ".claude" / "settings.local.json").read_text()) == _USER_SETTINGS
    assert hashlib.sha256(env.db.read_bytes()).hexdigest() == db_before

    # What it could not fix on its own is named, with the reason, not silently left.
    assert {k["path"]: bool(k["reason"]) for k in result["kept"]} == {
        str(workspace / "pnpm-lock.yaml"): True,
        str(workspace / ".claude" / "logs"): True,
    }
    assert _GAIA in (workspace / "pnpm-lock.yaml").read_text()


def test_dry_run_reports_what_the_real_run_does(env):
    _wire(env)
    workspace_before, home_before = _tree(env.workspace), _tree(env.home)
    cache_before = _tree(env.tmp / "gaia-data" / "cache")

    dry = _uninstall(env, dry_run=True)

    assert _tree(env.workspace) == workspace_before
    assert _tree(env.home) == home_before
    assert _tree(env.tmp / "gaia-data" / "cache") == cache_before
    real = _uninstall(env)
    for key in ("reverted", "artifacts", "kept"):
        assert dry[key] and sorted(map(json.dumps, dry[key])) == sorted(map(json.dumps, real[key])), key


def test_launcher_now_pointing_at_another_live_install_is_kept_and_reported(env):
    _wire(env)
    other = env.tmp / "other-install" / "bin" / "gaia"
    other.parent.mkdir(parents=True)
    other.write_text("#!/usr/bin/env python3\n")
    launcher = _launcher(env)
    launcher.unlink()
    launcher.symlink_to(other)

    result = _uninstall(env)

    assert launcher.is_symlink() and os.readlink(launcher) == str(other)
    assert str(launcher) in {k["path"] for k in result["kept"]}


def test_uninstall_run_by_npm_leaves_the_package_to_npm(env):
    _wire(env)
    package_json = (env.workspace / "package.json").read_bytes()

    _uninstall(env, preuninstall=True)

    assert (env.workspace / "package.json").read_bytes() == package_json
    assert (env.workspace / "node_modules" / _GAIA / "bin" / "gaia").is_file()
    assert not os.path.lexists(env.workspace / ".claude" / "hooks")
