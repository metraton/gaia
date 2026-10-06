"""gaia doctor on a plugin-only workspace, run with no flags from a subfolder.

A plugin-only workspace has no local npm package, no plugin-registry.json and no
local agents: its `.claude` holds only the hooks link and a settings.local.json
with permissions. What the plugin provides -- agents/, skills/, hooks/ and a
settings.json naming the orchestrator -- lives under CLAUDE_PLUGIN_ROOT. Doctor
must judge that workspace against the plugin, find it from the workspace root
recorded in gaia.db, and still fail when the plugin itself is broken.
"""

import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import copy_bootstrapped_db

REPO_ROOT = Path(__file__).resolve().parents[2]
GAIA_BIN = REPO_ROOT / "bin" / "gaia"
if str(REPO_ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "bin"))

import cli.doctor as doctor_mod  # noqa: E402

PLUGIN_JUDGED = ("Symlinks", "Identity", "Agent routing", "Workspace initialized", "Plugin registered")
ROUTED = {"application_services": "developer", "live_runtime": "cloud-troubleshooter"}
_HOST_ENV = ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "GAIA_WORKSPACE_PATH", "GAIA_DATA_DIR", "GAIA_DB")


@pytest.fixture
def plugin_workspace(tmp_path, bootstrapped_db_template):
    """A healthy plugin-only workspace, its plugin root, and the env doctor runs under."""
    plugin = tmp_path / "plugin"
    shutil.copytree(REPO_ROOT / "agents", plugin / "agents")
    (plugin / "skills").symlink_to(REPO_ROOT / "skills")
    (plugin / "hooks").symlink_to(REPO_ROOT / "hooks")
    (plugin / "settings.json").write_text('{"agent": "gaia-orchestrator"}')

    workspace = tmp_path / "ws"
    claude = workspace / ".claude"
    claude.mkdir(parents=True)
    (claude / "hooks").symlink_to(plugin / "hooks")
    (claude / "settings.local.json").write_text(
        '{"permissions": {"allow": ["Bash(ls:*)"], "deny": ["Bash(rm -rf /:*)"]}}'
    )
    (workspace / "sub" / "deeper").mkdir(parents=True)

    database = copy_bootstrapped_db(bootstrapped_db_template, tmp_path / "data" / "gaia.db")
    con = sqlite3.connect(database)
    try:
        con.execute("DELETE FROM workspaces")
        con.execute("INSERT INTO workspaces (name, root_path) VALUES ('ws', ?)", (str(workspace),))
        con.execute("DELETE FROM surface_routing")
        con.executemany(
            "INSERT INTO surface_routing (surface, primary_agent) VALUES (?, ?)", ROUTED.items()
        )
        con.commit()
    finally:
        con.close()

    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV}
    # A PATH without the developer's own `gaia`, which Global CLI alignment would compare against.
    path = os.pathsep.join(dict.fromkeys([str(Path(sys.executable).parent), "/usr/bin", "/bin"]))
    env.update(HOME=str(home), PATH=path, GAIA_DB=str(database), CLAUDE_PLUGIN_ROOT=str(plugin))
    return {"plugin": plugin, "workspace": workspace, "env": env, "tmp": tmp_path}


def _doctor(cwd: Path, env: dict) -> "tuple[int, dict, str]":
    """Run `gaia doctor` with no flags; return the exit code, severity per check, and output."""
    proc = subprocess.run(
        [sys.executable, str(GAIA_BIN), "doctor"],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=180,
    )
    icons = {icon: severity for severity, icon in doctor_mod._SEVERITY_ICONS.items()}
    severities = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"\s+\[(.+?)\] (.+?)\s{2,}", line + "  ")
        if match and match.group(1) in icons:
            severities[match.group(2).strip()] = icons[match.group(1)]
    return proc.returncode, severities, proc.stdout + proc.stderr


def test_a_healthy_plugin_workspace_is_green_from_a_subfolder(plugin_workspace):
    code, checks, output = _doctor(plugin_workspace["workspace"] / "sub" / "deeper", plugin_workspace["env"])
    assert {name: checks.get(name) for name in PLUGIN_JUDGED} == dict.fromkeys(PLUGIN_JUDGED, "pass"), output
    assert code == 0, output


def test_b_plugin_without_orchestrator_fails_identity(plugin_workspace):
    (plugin_workspace["plugin"] / "agents" / "gaia-orchestrator.md").unlink()
    code, checks, output = _doctor(plugin_workspace["workspace"] / "sub", plugin_workspace["env"])
    assert checks.get("Identity") == "error", output
    assert code == 2, output


def test_c_plugin_missing_a_routed_agent_fails_routing(plugin_workspace):
    (plugin_workspace["plugin"] / "agents" / "developer.md").unlink()
    code, checks, output = _doctor(plugin_workspace["workspace"] / "sub", plugin_workspace["env"])
    assert checks.get("Agent routing") == "error", output
    assert "developer" in output
    assert code == 2, output


def test_d_without_the_plugin_the_npm_requirements_hold(plugin_workspace):
    env = {k: v for k, v in plugin_workspace["env"].items() if k != "CLAUDE_PLUGIN_ROOT"}
    code, checks, output = _doctor(plugin_workspace["workspace"] / "sub", env)
    assert checks.get("Symlinks") == "error", output
    assert code == 2, output


def test_the_host_project_dir_names_the_workspace_over_the_cwd(plugin_workspace):
    outside = plugin_workspace["tmp"] / "elsewhere"
    outside.mkdir()
    env = dict(plugin_workspace["env"], CLAUDE_PROJECT_DIR=str(plugin_workspace["workspace"] / "sub"))
    code, checks, output = _doctor(outside, env)
    assert f"Workspace: {plugin_workspace['workspace'].resolve()}" in output
    assert code == 0, output


def test_e_a_folder_outside_every_recorded_root_exits_with_the_hint(plugin_workspace):
    outside = plugin_workspace["tmp"] / "elsewhere"
    outside.mkdir()
    code, checks, output = _doctor(outside, plugin_workspace["env"])
    assert code == 2, output
    assert not checks, output
    assert "--workspace" in output
