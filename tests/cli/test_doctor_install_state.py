"""`gaia doctor --channels` reads every Gaia channel of a throwaway machine and names where they disagree."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BIN_DIR = REPO_ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import cli._install_state as state  # noqa: E402
import cli.doctor as doctor_mod  # noqa: E402

MKT = "gaia-marketplace"
GITHUB = {"source": "github", "repo": "metraton/gaia"}
GIT_URL = {"source": "git", "url": "https://github.com/metraton/gaia.git"}


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _package(root: Path, version: str) -> Path:
    _write(root / "package.json", {"name": "@jaguilar87/gaia", "version": version})
    _write(root / "opencode" / "plugin.ts", "export const GaiaOpenCodePlugin = async () => ({})\n")
    return root


def _tagged_clone(path: Path, tag: str) -> str:
    """A git repository at *path* whose one commit carries *tag*; returns the commit."""
    path.mkdir(parents=True)
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "release"], check=True)
    subprocess.run([*git, "tag", tag], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


class Box:
    """One machine: home, Claude Code's plugin records, and a workspace."""

    def __init__(self, tmp: Path):
        self.home = tmp / "home"
        self.ws = tmp / "ws"
        self.plugins = self.home / ".claude" / "plugins"
        (self.ws / ".claude").mkdir(parents=True)
        self.settings = {"user": {}, "local": {}}
        self.known: dict = {}
        self.installed: dict = {}

    def declare(self, source: dict, scope: str = "user", name: str = MKT) -> None:
        self.settings[scope].setdefault("extraKnownMarketplaces", {})[name] = {"source": source}

    def register(self, source: dict, name: str = MKT, location: "Path | None" = None) -> None:
        where = location or self.plugins / "marketplaces" / name
        self.known[name] = {"source": source, "installLocation": str(where)}

    def install_plugin(self, version: str, name: str = MKT, commit: str = "c" * 40, enabled: bool = True) -> Path:
        root = _package(self.plugins / "cache" / name / "gaia" / version, version)
        self.installed.setdefault(f"gaia@{name}", []).append({
            "scope": "local", "projectPath": str(self.ws), "installPath": str(root),
            "version": version, "gitCommitSha": commit})
        self.settings["local"].setdefault("enabledPlugins", {})[f"gaia@{name}"] = enabled
        return root

    def npm_local(self, version: str, integrity: str = "sha512-same") -> Path:
        root = _package(self.ws / "node_modules" / "@jaguilar87" / "gaia", version)
        _write(self.ws / "package.json", {"dependencies": {"@jaguilar87/gaia": version}})
        lock = {"packages": {"node_modules/@jaguilar87/gaia": {
            "version": version, "integrity": integrity,
            "resolved": f"https://registry.npmjs.org/@jaguilar87/gaia/-/gaia-{version}.tgz"}}}
        _write(self.ws / "package-lock.json", lock)
        _write(self.ws / "node_modules" / ".package-lock.json", lock)
        return root

    def opencode(self, *entries) -> None:
        _write(self.ws / "opencode.json", {"plugin": [str(entry) for entry in entries]})

    def npm_entry(self) -> Path:
        return self.ws / "node_modules" / "@jaguilar87" / "gaia" / "opencode" / "plugin.ts"

    def machine(self) -> "state.Machine":
        _write(self.home / ".claude" / "settings.json", self.settings["user"])
        _write(self.ws / ".claude" / "settings.local.json", self.settings["local"])
        _write(self.plugins / "known_marketplaces.json", self.known)
        _write(self.plugins / "installed_plugins.json", {"version": 2, "plugins": self.installed})
        return state.Machine(
            workspace=self.ws, user_settings=self.home / ".claude" / "settings.json",
            plugins_dir=self.plugins, opencode_global=self.home / ".config" / "opencode",
            npm_prefix=None, path_gaia=None, plugin_root=None, windows=False)


def _errors(report: dict) -> "list[str]":
    return [f["detail"] for f in report["findings"] if f["severity"] == "error"]


def _commands(plan: dict) -> "list[str]":
    return [step["command"] for step in plan["steps"] if step.get("command")]


def _snapshot(root: Path) -> dict:
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


def test_an_aligned_machine_reads_one_version_proves_its_commit_and_changes_nothing(tmp_path):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.2"}
    clone = box.plugins / "marketplaces" / MKT
    commit = _tagged_clone(clone, "v5.8.0-rc.2")
    box.declare(ref)
    box.register(ref, location=clone)
    box.install_plugin("5.8.0-rc.2", commit=commit)
    box.npm_local("5.8.0-rc.2")
    box.opencode(box.npm_entry())
    machine = box.machine()
    before = _snapshot(tmp_path)

    report = state.read(machine)
    plan = state.plan(report, "5.8.0-rc.2")

    assert (report["status"], report["exit_code"], report["versions"]) == ("aligned", 0, ["5.8.0-rc.2"])
    assert _errors(report) == []
    plugin = next(c for c in report["channels"] if c["channel"] == "plugin")
    assert plugin["provenance"]["verdict"] == "match"
    assert plan["steps"] == []
    assert _snapshot(tmp_path) == before


def test_an_installed_commit_that_is_not_the_tag_commit_fails_provenance(tmp_path):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.2"}
    clone = box.plugins / "marketplaces" / MKT
    commit = _tagged_clone(clone, "v5.8.0-rc.2")
    box.declare(ref)
    box.register(ref, location=clone)
    box.install_plugin("5.8.0-rc.2", commit="0" * 40)

    report = state.read(box.machine())

    assert report["exit_code"] == 2
    assert any("0" * 12 in e and commit[:12] in e and "v5.8.0-rc.2" in e for e in _errors(report))


def test_a_declared_git_url_against_a_registered_github_ref_is_a_split_named_on_both_sides(tmp_path):
    box = Box(tmp_path)
    box.declare({**GIT_URL, "ref": "v5.8.0-rc.2"})
    box.register({**GITHUB, "ref": "v5.8.0-rc.1"})
    box.install_plugin("5.8.0-rc.1")

    report = state.read(box.machine())
    plan = state.plan(report, "5.8.0-rc.2")

    assert report["exit_code"] == 2
    assert any("git https://github.com/metraton/gaia.git ref v5.8.0-rc.2" in e
               and "github metraton/gaia ref v5.8.0-rc.1" in e for e in _errors(report))
    commands = _commands(plan)
    assert "claude plugin marketplace add https://github.com/metraton/gaia.git#v5.8.0-rc.2 --scope user" in commands
    assert not any("metraton/gaia#" in c for c in commands)
    update = next(s for s in plan["steps"] if s.get("command") == "claude plugin update gaia@gaia-marketplace --scope local")
    assert update["cwd"] == str(box.ws)
    assert not any(s["kind"] == "edit" for s in plan["steps"])


def test_the_plugin_and_opencode_on_different_versions_is_a_split(tmp_path):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.1"}
    box.declare(ref)
    box.register(ref)
    box.install_plugin("5.8.0-rc.1")
    box.npm_local("5.7.2-rc.2")
    box.opencode(box.npm_entry())

    report = state.read(box.machine())
    plan = state.plan(report, "5.8.0-rc.1")

    assert report["exit_code"] == 2
    assert report["versions"] == ["5.7.2-rc.2", "5.8.0-rc.1"]
    assert any("5.8.0-rc.1" in e and "5.7.2-rc.2" in e and "opencode" in e for e in _errors(report))
    install = next(s for s in plan["steps"] if s.get("command") == "npm install @jaguilar87/gaia@5.8.0-rc.1 --save-exact")
    assert install["cwd"] == str(box.ws)
    assert not any("claude plugin" in c for c in _commands(plan))


def test_an_opencode_entry_in_a_swept_plugin_cache_folder_is_stale(tmp_path):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.1"}
    box.declare(ref)
    box.register(ref)
    box.install_plugin("5.8.0-rc.1")
    box.npm_local("5.8.0-rc.1")
    stale = _package(box.plugins / "cache" / MKT / "gaia" / "5.7.0", "5.7.0")
    _write(stale / ".orphaned_at", "1762473600000")
    box.opencode(stale / "opencode" / "plugin.ts", box.npm_entry())

    report = state.read(box.machine())
    plan = state.plan(report, "5.8.0-rc.1")

    assert report["exit_code"] == 2
    assert any(str(stale) in e and "orphaned" in e for e in _errors(report))
    edit = next(s for s in plan["steps"] if s["kind"] == "edit")
    assert edit["file"] == str(box.ws / "opencode.json") and str(stale / "opencode" / "plugin.ts") in edit["detail"]


@pytest.mark.parametrize("dev_enabled", [True, False])
def test_a_dev_marketplace_beside_the_real_one(tmp_path, dev_enabled):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.1"}
    box.declare(ref)
    box.register(ref)
    box.install_plugin("5.8.0-rc.1")
    dev = _package(tmp_path / "dev-plugin", "5.7.2-rc.2")
    box.declare({"source": "directory", "path": str(dev)}, scope="local", name="gaia-dev")
    box.register({"source": "directory", "path": str(dev)}, name="gaia-dev", location=dev)
    box.install_plugin("5.7.2-rc.2", name="gaia-dev", enabled=dev_enabled)

    report = state.read(box.machine())
    plan = state.plan(report, "5.8.0-rc.1")

    disable = "claude plugin disable gaia@gaia-dev --scope local"
    if dev_enabled:
        assert report["exit_code"] == 2
        assert any("gaia@gaia-marketplace" in e and "gaia@gaia-dev" in e for e in _errors(report))
        assert disable in _commands(plan)
    else:
        assert (report["status"], report["exit_code"]) == ("aligned", 0)
        assert any(f["severity"] == "info" and "gaia@gaia-dev" in f["detail"] for f in report["findings"])
        assert disable not in _commands(plan)


def test_windows_paths_and_sources_are_read_by_their_form():
    entry = r"C:\Users\j\.claude\plugins\cache\gaia-marketplace\gaia\5.7.0\opencode\plugin.ts"
    assert state.cache_entry(entry) == ("gaia-marketplace", "5.7.0")
    assert state.same_path(r"C:\Users\J\WS", "c:/users/j/ws/", windows=True)
    assert not state.same_path("/home/J/ws", "/home/j/ws", windows=False)
    assert state.spelled_source({**GIT_URL, "ref": "v1"}, "v2") == "https://github.com/metraton/gaia.git#v2"
    assert state.spelled_source(GITHUB, "v2") == "metraton/gaia#v2"


def _doctor(box: Box, monkeypatch, capsys, channels: bool = True, target: "str | None" = None) -> "tuple[int, dict]":
    monkeypatch.setattr(doctor_mod, "_USER_SETTINGS_PATH", box.home / ".claude" / "settings.json")
    monkeypatch.setattr(doctor_mod, "_INSTALLED_PLUGINS_PATH", box.plugins / "installed_plugins.json")
    monkeypatch.setattr(doctor_mod, "_KNOWN_MARKETPLACES_PATH", box.plugins / "known_marketplaces.json")
    for var in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_CODE_PLUGIN_CACHE_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(box.home))
    monkeypatch.setenv("PATH", str(box.home / "empty-bin"))
    monkeypatch.setenv("NPM_CONFIG_PREFIX", str(box.home / "npm-global"))
    args = Namespace(workspace=str(box.ws), json=True, fix=False, channels=channels, target=target)
    code = doctor_mod.cmd_doctor(args)
    return code, json.loads(capsys.readouterr().out)


def test_doctor_channels_exits_with_the_install_verdict_alone(tmp_path, monkeypatch, capsys):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.1"}
    box.declare(ref)
    box.register(ref)
    box.install_plugin("5.8.0-rc.1")
    box.machine()
    code, out = _doctor(box, monkeypatch, capsys)
    assert (code, out["status"]) == (0, "aligned")

    box.npm_local("5.7.2-rc.2")
    box.opencode(box.npm_entry())
    box.machine()
    code, out = _doctor(box, monkeypatch, capsys)
    assert (code, out["status"]) == (2, "split")


def test_doctor_target_prints_the_plan_and_runs_nothing(tmp_path, monkeypatch, capsys):
    box = Box(tmp_path)
    ref = {**GITHUB, "ref": "v5.8.0-rc.1"}
    box.declare(ref)
    box.register(ref)
    box.install_plugin("5.8.0-rc.1")
    box.machine()
    before = _snapshot(tmp_path)

    code, out = _doctor(box, monkeypatch, capsys, channels=False, target="5.8.0-rc.2")
    plan = out["plan"]

    assert (code, plan["target"]) == (0, "5.8.0-rc.2")
    assert "claude plugin marketplace add metraton/gaia#v5.8.0-rc.2 --scope user" in _commands(plan)
    edit = next(s for s in plan["steps"] if s["kind"] == "edit")
    assert edit["file"] == str(box.home / ".claude" / "settings.json") and "v5.8.0-rc.2" in edit["detail"]
    assert _snapshot(tmp_path) == before
