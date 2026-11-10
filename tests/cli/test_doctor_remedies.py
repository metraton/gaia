"""A doctor remedy never names a dist-tag, and every reader of the plugin cache honours CLAUDE_CODE_PLUGIN_CACHE_DIR."""

import json
import re
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "bin"))

import cli.doctor as doctor_mod  # noqa: E402
from cli import release  # noqa: E402

FLOATING = re.compile(r"@jaguilar87/gaia@(?:latest|rc|next|beta)\b|\bor 'latest'")


def _database_ahead(path: Path, minimum_ahead: bool) -> Path:
    expected = doctor_mod.EXPECTED_SCHEMA_VERSION
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT, "
        "description TEXT, min_code_version INTEGER)"
    )
    con.execute("INSERT INTO schema_version VALUES (?, 'now', 'seal', ?)",
                (expected + 1, expected + 1 if minimum_ahead else expected))
    con.commit()
    con.close()
    return path


def test_no_remedy_in_the_doctor_code_names_a_dist_tag():
    source = Path(doctor_mod.__file__).read_text(encoding="utf-8")
    assert FLOATING.findall(source) == []


@pytest.mark.parametrize("minimum_ahead", [True, False])
def test_a_database_ahead_points_at_the_exact_version_route(tmp_path, monkeypatch, minimum_ahead):
    monkeypatch.setenv("GAIA_DB", str(_database_ahead(tmp_path / "gaia.db", minimum_ahead)))

    result = doctor_mod.check_schema_version()

    assert "gaia doctor --target" in result["fix"]
    assert "@latest" not in result["fix"]


def _gaia_plugin(plugins: Path, version: str) -> Path:
    root = plugins / "cache" / "gaia-marketplace" / "gaia" / version
    root.mkdir(parents=True)
    (root / "package.json").write_text(json.dumps({"name": "@jaguilar87/gaia", "version": version}))
    return root


def test_the_live_smoke_reads_the_plugin_cache_where_the_variable_moves_it(tmp_path, monkeypatch):
    moved = tmp_path / "moved-plugins"
    root = _gaia_plugin(moved, "5.8.0-rc.2")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(moved))
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(tmp_path / "no-npm"))

    channels = release.live_channels("5.8.0-rc.2", None)

    assert [(c.name, c.root) for c in channels] == [("marketplace", root.resolve())]


def test_the_doctor_reads_installed_plugins_where_the_variable_moves_them(tmp_path, monkeypatch):
    moved = tmp_path / "moved-plugins"
    root = _gaia_plugin(moved, "5.8.0-rc.2")
    record = {"plugins": {"gaia@gaia-marketplace": [{"scope": "user", "installPath": str(root)}]}}
    (moved / "installed_plugins.json").write_text(json.dumps(record))
    monkeypatch.setattr(doctor_mod, "_INSTALLED_PLUGINS_PATH", tmp_path / "default" / "installed_plugins.json")
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(moved))

    assert list(doctor_mod._installed_gaia_installs()) == ["gaia@gaia-marketplace"]
