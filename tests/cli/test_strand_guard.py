"""A chain older copies cannot write is not applied while one would be stranded, and `doctor --target` upgrades them first."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for entry in (REPO_ROOT / "bin", REPO_ROOT / "scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import bootstrap_database as bootstrap  # noqa: E402
import cli._install_state as state  # noqa: E402
import cli.install as install  # noqa: E402

MKT = "gaia-marketplace"


def _write(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def _package(root: Path, version: str, schema: int) -> Path:
    _write(root / "package.json", {"name": "@jaguilar87/gaia", "version": version})
    _write(root / "bin" / "cli" / "doctor.py", f"EXPECTED_SCHEMA_VERSION = {schema}\n")
    _write(root / "opencode" / "plugin.ts", "export const GaiaOpenCodePlugin = async () => ({})\n")
    return root


def _behind(db_path: str) -> dict:
    return {"database": db_path, "state": "sealed", "ledger": 67, "expected": 69, "min_code_version": 60,
            "min_code_version_after": 69, "direction": "behind", "chain": "v67..v69", "migrations": [],
            "consent_command": None, "backups": []}


def _machine(tmp: Path, *, plugin: "str | None" = None, npm_local: "str | None" = None) -> state.Machine:
    home, ws = tmp / "home", tmp / "ws"
    plugins = home / ".claude" / "plugins"
    (ws / ".claude").mkdir(parents=True, exist_ok=True)
    _package(home / "npm-global" / "lib" / "node_modules" / "@jaguilar87" / "gaia", "5.6.3", 67)
    user, local, known, installed = {}, {}, {}, {}
    if plugin:
        source = {"source": "github", "repo": "metraton/gaia", "ref": f"v{plugin}"}
        user["extraKnownMarketplaces"] = {MKT: {"source": source}}
        known[MKT] = {"source": source, "installLocation": str(plugins / "marketplaces" / MKT)}
        root = _package(plugins / "cache" / MKT / "gaia" / plugin, plugin, 67)
        installed[f"gaia@{MKT}"] = [{"scope": "local", "projectPath": str(ws), "installPath": str(root),
                                     "version": plugin, "gitCommitSha": "c" * 40}]
        local["enabledPlugins"] = {f"gaia@{MKT}": True}
    if npm_local:
        _package(ws / "node_modules" / "@jaguilar87" / "gaia", npm_local, 67)
        _write(ws / "package.json", {"dependencies": {"@jaguilar87/gaia": npm_local}})
    _write(home / ".claude" / "settings.json", user)
    _write(ws / ".claude" / "settings.local.json", local)
    _write(plugins / "known_marketplaces.json", known)
    _write(plugins / "installed_plugins.json", {"version": 2, "plugins": installed})
    return state.Machine(workspace=ws, user_settings=home / ".claude" / "settings.json", plugins_dir=plugins,
                         opencode_global=home / ".config" / "opencode", npm_prefix=home / "npm-global",
                         path_gaia=None, plugin_root=None, windows=False, db_path=home / ".gaia" / "gaia.db")


def test_the_plan_report_carries_the_minimum_code_version_after_a_breaking_chain(tmp_path, monkeypatch):
    migrations = tmp_path / "migrations"
    _write(migrations / "v67_to_v68.sql", "-- gaia-compat: backward\nCREATE TABLE added_a (x INTEGER);\n")
    _write(migrations / "v68_to_v69.sql", "-- gaia-compat: breaking\nCREATE TABLE added_b (x INTEGER);\n")
    db = tmp_path / "gaia.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT, description TEXT, "
                "min_code_version INTEGER)")
    con.execute("INSERT INTO schema_version VALUES (67, '2026-11-01T00:00:00Z', 'fixture', 60)")
    con.commit()
    con.close()
    monkeypatch.setattr(bootstrap, "GAIA_DB", db)
    monkeypatch.setattr(bootstrap, "MIG_DIR", migrations)

    breaking = bootstrap.plan_report(69)
    backward = bootstrap.plan_report(68)

    assert (breaking["direction"], breaking["min_code_version"], breaking["min_code_version_after"]) == ("behind", 60, 69)
    assert (backward["min_code_version"], backward["min_code_version_after"]) == (60, 60)


def _args(machine: state.Machine, db: Path, **overrides) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    install.add_install_arguments(parser)
    args = parser.parse_args(["--workspace", str(machine.workspace), "--db-path", str(db), "--channel", "npm"])
    return argparse.Namespace(**{**vars(args), **overrides})


def _isolate(monkeypatch, machine: state.Machine) -> list:
    monkeypatch.setattr(state.Machine, "current", classmethod(lambda cls, *_args: machine))
    monkeypatch.setattr(install.migrate, "plan_report", _behind)
    monkeypatch.setattr("gaia.hook_shell.missing_hook_shell", lambda *_a: None)
    monkeypatch.setattr("gaia.hook_shell.claude_code_version_finding", lambda *_a: None)
    ran: list = []
    monkeypatch.setattr(install, "_run_bootstrap", lambda **kw: ran.append(kw) or {"rc": 0, "detail": ""})
    for seeder in ("_seed_contract_permissions", "_seed_surface_routing"):
        monkeypatch.setattr(install, seeder, lambda **_kw: {"action": "noop", "details": ""})
    return ran


def _tree(root: Path) -> dict:
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file() or p.is_symlink()}


def test_a_5_6_global_copy_stops_the_update_before_the_database_or_links_change(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path)
    db = _write(tmp_path / "data" / "gaia.db", "a v67 store")
    ran = _isolate(monkeypatch, machine)
    before = (_tree(machine.workspace), db.read_bytes(), db.stat().st_mtime_ns)
    version = install._install_helpers._read_plugin_version(install._PACKAGE_ROOT)

    code = install.install_channels(_args(machine, db), ("npm",), command="gaia update")

    refusal = capsys.readouterr().err
    assert (code, ran) == (1, [])
    assert (_tree(machine.workspace), db.read_bytes(), db.stat().st_mtime_ns) == before
    global_root = machine.npm_prefix / "lib" / "node_modules" / "@jaguilar87" / "gaia"
    assert f"npm-global 5.6.3 ({global_root}) writes schema v67" in refusal
    assert f"gaia doctor --target {version}" in refusal
    assert f"npm install -g @jaguilar87/gaia@{version}" in refusal
    assert "--accept-stranding" in refusal


def test_accept_stranding_is_off_by_default_and_lets_the_update_migrate(tmp_path, monkeypatch, capsys):
    machine = _machine(tmp_path)
    db = _write(tmp_path / "data" / "gaia.db", "a v67 store")
    ran = _isolate(monkeypatch, machine)

    assert _args(machine, db).accept_stranding is False
    code = install.install_channels(_args(machine, db, accept_stranding=True, skip_workspace=True), ("npm",),
                                    command="gaia update")

    assert (code, len(ran)) == (0, 1)
    assert "refused" not in capsys.readouterr().err


def test_doctor_target_upgrades_every_older_copy_before_the_step_that_migrates(tmp_path):
    machine = _machine(tmp_path, plugin="5.7.0", npm_local="5.7.0")
    report = state.read(machine, store_plan=_behind)

    stranded = state.stranded_copies(report)
    target_plan = state.plan(report, "5.8.0")

    assert stranded == state.stranded_copies(report)
    assert {(c["label"], c["needs"], c["channel"]) for c in stranded} == {
        ("npm-global", 69, "npm-global"), ("npm-local", 69, "npm-local"),
        (f"plugin gaia@{MKT}", 69, "plugin")}
    steps = target_plan["steps"]
    assert all({"kind", "migrates", "stranded"} <= set(step) for step in steps)
    first = next(i for i, step in enumerate(steps) if step["migrates"])
    assert " update --workspace " in steps[first]["command"] and steps[first]["stranded"] == []
    commands = [step["command"] for step in steps]
    upgrades = [commands.index(command) for command in (
        "npm install -g @jaguilar87/gaia@5.8.0", "npm install @jaguilar87/gaia@5.8.0 --save-exact",
        f"claude plugin update gaia@{MKT} --scope local")]
    assert max(upgrades) < first
