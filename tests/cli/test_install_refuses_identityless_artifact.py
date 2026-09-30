"""An install refuses to serve an artifact that cannot start the host as gaia-orchestrator."""

import io
import json
import sys
import tarfile
from pathlib import Path

import pytest

_BIN_DIR = Path(__file__).resolve().parents[2] / "bin"
if str(_BIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BIN_DIR))

from cli import _dev_plugin, _install_helpers, install  # noqa: E402


def _tarball(tmp_path: Path, files: dict) -> Path:
    path = tmp_path / "gaia.tgz"
    with tarfile.open(path, "w:gz") as archive:
        for name, text in files.items():
            data = text.encode()
            member = tarfile.TarInfo(f"package/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return path


_PLUGIN_FILES = {
    ".claude-plugin/plugin.json": json.dumps({"name": "gaia", "version": "5.5.0"}),
    "agents/gaia-orchestrator.md": "---\nname: gaia-orchestrator\n---\n",
    "settings.json": json.dumps({"agent": "gaia-orchestrator"}),
}


def test_plugin_channel_serves_a_tree_carrying_the_identity(tmp_path):
    result = _dev_plugin.extract_plugin(_tarball(tmp_path, _PLUGIN_FILES), tmp_path / "plugin")

    assert result["action"] == "created", result


@pytest.mark.parametrize("dropped", ["settings.json", "agents/gaia-orchestrator.md"])
def test_plugin_channel_refuses_a_tree_without_the_identity(tmp_path, dropped):
    files = {name: text for name, text in _PLUGIN_FILES.items() if name != dropped}
    directory = tmp_path / "plugin"

    result = _dev_plugin.extract_plugin(_tarball(tmp_path, files), directory)

    assert result["action"] == "error"
    assert "gaia-orchestrator" in result["details"]
    assert not directory.exists()


def test_package_install_refuses_a_package_without_the_orchestrator(tmp_path, monkeypatch, capsys):
    package = tmp_path / "package"
    (package / "agents").mkdir(parents=True)
    monkeypatch.setattr(_install_helpers, "_PACKAGE_ROOT", package)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    configured = install._configure_host(
        "claude_code", workspace=workspace, postinstall=False, quiet=True, verbose=False,
    )

    assert configured is False
    assert "gaia-orchestrator" in capsys.readouterr().err
    assert not (workspace / ".claude").exists()
