"""The compaction mod ships in the plugin build, only steers, and starts no process.

The mod is a Claude Code hooks module (hooks/mods/compaction/). It reaches the
host through hooks/hooks.json's `modules` key, generated from the manifest's
`host_mods` key; `$.process.run` would skip the permission prompt and Gaia's
PreToolUse, so the second class pins that the mod's source spawns nothing.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MOD_DIR = ROOT / "hooks" / "mods" / "compaction"
MOD_SOURCES = sorted(p for p in MOD_DIR.glob("*.ts") if not p.name.endswith(".test.ts"))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_plugin():
    return _load("_gaia_build_plugin", "scripts/build-plugin.py")


@pytest.fixture(scope="module")
def manifest(build_plugin):
    return build_plugin.load_manifest("gaia")


class TestBuildOutputShipsTheMod:
    def test_generated_hooks_json_declares_the_mod(self, build_plugin, manifest):
        generated = build_plugin.generate_hooks_json(manifest)
        assert generated["modules"] == ["./mods/compaction/register.ts"]

    def test_committed_hooks_json_declares_the_mod(self):
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        assert committed["modules"] == ["./mods/compaction/register.ts"]

    def test_declared_module_resolves_from_hooks_json(self):
        committed = json.loads((ROOT / "hooks" / "hooks.json").read_text())
        for declared in committed["modules"]:
            assert (ROOT / "hooks" / declared).is_file(), declared

    def test_mod_sources_are_in_the_build_file_list(self, build_plugin, manifest):
        listed = set(build_plugin.resolve_file_list(manifest))
        assert set(MOD_SOURCES) <= listed

    def test_modules_key_is_independent_of_the_python_modules_key(self, build_plugin, manifest):
        without_mods = {k: v for k, v in manifest.items() if k != "host_mods"}
        assert "modules" not in build_plugin.generate_hooks_json(without_mods)
        assert manifest["modules"] == "all"

    def test_drift_guard_fails_when_the_committed_modules_key_is_dropped(
        self, build_plugin, manifest, tmp_path, monkeypatch, capsys
    ):
        guard = _load("_gaia_check_hooks_drift", "scripts/check_hooks_drift.py")
        stale = build_plugin.generate_hooks_json(manifest)
        del stale["modules"]
        fixtures = {
            "MANIFEST": ("gaia.manifest.json", manifest),
            "HOOKS_JSON": ("hooks.json", stale),
            "PLUGIN_JSON": ("plugin.json", {"name": "gaia"}),
        }
        for attr, (filename, content) in fixtures.items():
            path = tmp_path / filename
            path.write_text(json.dumps(content))
            monkeypatch.setattr(guard, attr, path)

        assert guard.main() == 1
        assert "'modules'" in capsys.readouterr().err

    def test_drift_guard_passes_on_the_working_tree(self, capsys):
        guard = _load("_gaia_check_hooks_drift", "scripts/check_hooks_drift.py")
        assert guard.main() == 0, capsys.readouterr().err


class TestModReachesNothingOutsideItsHook:
    REACH = re.compile(
        r"\$\.(process|tool|agent|network|fs|settings|env|clock)\b|\bimport\s*\(|\beval\s*\(|new Function|\bfetch\s*\("
    )

    def test_mod_sources_exist(self):
        assert MOD_DIR / "register.ts" in MOD_SOURCES

    def test_mod_starts_no_process_and_reaches_nothing_else(self):
        for path in MOD_SOURCES:
            assert not self.REACH.search(path.read_text()), path.name


class TestTheRefreshDeliversTheSnapshotOnce:
    """The SessionStart(compact) refresh carries exactly one snapshot; register.test.ts pins that the mod adds none."""

    def test_the_compact_start_context_carries_one_snapshot(self, monkeypatch):
        import gaia.session_snapshot as snapshot
        from modules.session.session_lifecycle import start_context

        monkeypatch.setattr(snapshot, "build_snapshot", lambda session_id: {"session_id": session_id})
        monkeypatch.setattr(snapshot, "render_snapshot", lambda snap: f"## Session Snapshot\n{snap['session_id']}")

        refresh = start_context("compact", ["## Workspace\nnotice"], "session-1")

        assert refresh.count("## Session Snapshot") == 1
        assert "## Session Snapshot\nsession-1" in refresh
