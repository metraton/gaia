"""An install behind the shared database names the update command of its own channel."""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.session import session_manifest  # noqa: E402
from modules.session.session_manifest import install_behind_line, install_update_command  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("GAIA_DATA_DIR", str(tmp_path / "gaia-data"))
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)


def _runs_from(monkeypatch, root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(session_manifest, "_own_package_root", lambda: root)


def test_the_marketplace_plugin_updates_through_claude(monkeypatch, tmp_path):
    root = tmp_path / ".claude" / "plugins" / "cache" / "gaia-marketplace" / "gaia" / "5.6.0"
    monkeypatch.setenv("CLAUDE_PLUGIN_ROOT", str(root))

    assert install_update_command(80) == "claude plugin update gaia@gaia-marketplace"


def test_a_workspace_package_updates_with_npm_into_that_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "my ws"
    _runs_from(monkeypatch, workspace / "node_modules" / "@jaguilar87" / "gaia")
    (workspace / "package.json").write_text("{}", encoding="utf-8")

    command = install_update_command(80)

    assert command == (
        f"npm install --prefix '{workspace}' @jaguilar87/gaia@latest, "
        f"then gaia update --workspace '{workspace}'"
    )


def test_a_global_package_updates_with_npm_global(monkeypatch, tmp_path):
    _runs_from(monkeypatch, tmp_path / "prefix" / "lib" / "node_modules" / "@jaguilar87" / "gaia")

    assert install_update_command(80) == "npm install -g @jaguilar87/gaia@latest"


def test_an_unknown_channel_still_produces_a_line(monkeypatch, tmp_path):
    _runs_from(monkeypatch, tmp_path / "somewhere" / "gaia")

    line = install_behind_line(80, 73, refused=True)

    assert install_update_command(80) is None
    assert line.startswith("This Gaia (schema v73) is behind gaia.db (schema v80)")
    assert "channel is unknown" in line
    assert "\n" not in line
