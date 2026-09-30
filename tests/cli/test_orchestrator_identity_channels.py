"""The host starts as gaia-orchestrator from what each install channel actually ships.

The plugin channels carry the identity in the plugin root's settings.json; the
package channels get it from the workspace settings `gaia install` writes.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BIN_DIR = _REPO_ROOT / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _dev_plugin, _pack_helpers  # noqa: E402
from tests.conftest import require_tool  # noqa: E402


@pytest.fixture(scope="module")
def tarball(tmp_path_factory):
    """The package packed from a copy of the working tree.

    prepack deletes __pycache__ and regenerates manifests in the tree it packs,
    so packing the repository itself breaks any parallel test that reads it.
    """
    git = require_tool("git")
    require_tool("npm")
    listed = subprocess.run(
        [git, "-C", str(_REPO_ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True, timeout=60)
    source = tmp_path_factory.mktemp("source")
    for name in filter(None, listed.stdout.split("\0")):
        original = _REPO_ROOT / name
        if os.path.lexists(original):
            (source / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, source / name, follow_symlinks=False)
    result = _pack_helpers.pack_tarball(source, dest_dir=tmp_path_factory.mktemp("pack"), timeout=120)
    assert result["action"] == "created", result.get("details")
    return Path(result["tarball"])


def test_dev_plugin_tree_starts_the_host_as_the_orchestrator(tarball, tmp_path):
    directory = tmp_path / "plugin"

    result = _dev_plugin.extract_plugin(tarball, directory)

    assert result["action"] == "created", result
    settings = json.loads((directory / "settings.json").read_text())
    assert settings.get("agent") == "gaia-orchestrator"


def test_bun_installed_package_wires_the_orchestrator_identity(tarball, tmp_path):
    bun = require_tool("bun")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "package.json").write_text(json.dumps({"name": "consumer", "private": True}))
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "GAIA_DATA_DIR": str(tmp_path / "data"),
        "GAIA_DB": str(tmp_path / "data" / "gaia.db"),
        "BUN_INSTALL_CACHE_DIR": str(tmp_path / "bun-cache"),
    }

    added = subprocess.run([bun, "add", str(tarball)], cwd=workspace, env=env,
                           capture_output=True, text=True, timeout=180)
    assert added.returncode == 0, added.stderr
    installed_gaia = workspace / "node_modules" / "@jaguilar87" / "gaia" / "bin" / "gaia"
    wired = subprocess.run(
        [sys.executable, str(installed_gaia), "install", "--workspace", str(workspace),
         "--host", "claude_code", "--no-path", "--quiet"],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=180)

    assert wired.returncode == 0, wired.stderr or wired.stdout
    local = json.loads((workspace / ".claude" / "settings.local.json").read_text())
    assert local.get("agent") == "gaia-orchestrator"
    assert not (tmp_path / "home" / ".claude" / "settings.json").exists()
