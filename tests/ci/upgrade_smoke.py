#!/usr/bin/env python3
"""Upgrade one published database base through the packed package, end to end.

Invoked once per (os, base) cell by the ``upgrade`` job in
``.github/workflows/ci.yml``. Everything runs from the npm-installed tarball,
never from the checkout; the checkout only supplies the committed base dump
(``tests/fixtures/published_bases``) and this script.

  1. load the v<base> dump into a fresh data dir
  2. ``npm install`` the tarball (packed on Linux, as a release is) into a
     temporary project
  3. ``gaia migrate apply`` (with the one chain consent it asks for, if any)
     and require the ledger to reach the package's EXPECTED_SCHEMA_VERSION
  4. npm channel: ``gaia install`` into a workspace, then run the registered
     PreToolUse command exactly as written, through a POSIX shell
  5. plugin channel: ``build-plugin.py`` regenerates the package's plugin
     manifests, then the plugin's SessionStart command runs as written
  6. ``gaia uninstall`` on both workspaces, which must leave no manifest and
     nothing left to revert

Every path the package sees contains a space. Exit 0 only when all six hold.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tests" / "fixtures" / "published_bases"))
from loader import load_base  # noqa: E402

PACKAGE_NAME = "@jaguilar87/gaia"
WINDOWS = sys.platform.startswith("win")


def _run(cmd: list[str], *, env: dict, cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env=env, cwd=cwd, input=stdin, capture_output=True, text=True, timeout=300)
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip())
    return proc


def _require(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", flush=True)
        raise SystemExit(1)


def _schema_version(db: Path) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        con.close()


def posix_shell() -> Path:
    """The shell Claude Code runs a hook command through.

    On Windows that is Git for Windows' bash, which Claude Code requires; it is
    located from ``git`` rather than PATH because the Windows PATH need not
    carry ``sh`` at all -- which is reported, since the registered command
    itself starts with ``sh``.
    """
    if not WINDOWS:
        return Path("/bin/sh")
    print(f"sh on the Windows PATH: {shutil.which('sh') or 'absent'}")
    git = shutil.which("git")
    _require(git is not None, "git is not on PATH, so Git Bash cannot be located")
    bash = Path(git).resolve().parents[1] / "bin" / "bash.exe"
    _require(bash.is_file(), f"Git Bash not found at {bash}")
    return bash


def _hook_command(hooks: dict, event: str) -> str:
    commands = [h["command"] for group in hooks.get(event, []) for h in group.get("hooks", [])]
    _require(bool(commands), f"no {event} command registered")
    command = commands[0]
    _require(command.startswith("sh ") and "launch.sh" in command, f"{event} command is not the launcher form: {command}")
    return command


def _run_hook(command: str, payload: dict, *, env: dict, cwd: Path) -> subprocess.CompletedProcess:
    print(f"registered command: {command}")
    return _run([str(posix_shell()), "-c", command], env=env, cwd=cwd, stdin=json.dumps(payload))


def _uninstall_leaves_nothing(gaia: list[str], workspace: Path, env: dict) -> None:
    done = _run([*gaia, "uninstall", "--workspace", str(workspace), "--json", "--no-backup"], env=env, cwd=workspace)
    _require(done.returncode == 0, f"gaia uninstall failed in {workspace}")
    _require(json.loads(done.stdout)["manifest"]["source"] == "manifest", "uninstall did not revert from a manifest")
    _require(not (workspace / ".claude" / "gaia-manifest.json").exists(), "the manifest survived uninstall")
    again = _run([*gaia, "uninstall", "--workspace", str(workspace), "--json", "--dry-run", "--no-backup"], env=env, cwd=workspace)
    left = json.loads(again.stdout)["manifest"]
    _require(left["source"] == "none" and not left["reverted"], f"uninstall left a footprint: {left}")
    print(f"uninstall: manifest empty in {workspace.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=int, required=True, help="published schema version (21, 26, 33, 37, 51, 57)")
    parser.add_argument("--pack-dir", type=Path, required=True, help="directory holding the one npm pack tarball")
    parser.add_argument("--root", type=Path, required=True, help="empty scratch directory for this cell")
    args = parser.parse_args()

    root = args.root.resolve()
    data, home, project = root / "gaia data", root / "home dir", root / "npm project"
    npm_ws, plugin_ws, plugin_data = root / "npm workspace", root / "plugin workspace", root / "plugin data"
    for directory in (data, home, project, npm_ws, plugin_ws, plugin_data):
        directory.mkdir(parents=True)
    db = data / "gaia.db"

    load_base(args.base, db)
    print(f"loaded published base v{_schema_version(db)}")

    tarballs = sorted(args.pack_dir.resolve().glob("*.tgz"))
    _require(len(tarballs) == 1, f"expected one tarball in {args.pack_dir}, found {tarballs}")
    npm = shutil.which("npm")
    _require(npm is not None, "npm is not on PATH")
    _require(_run([npm, "init", "-y"], env=dict(os.environ), cwd=project).returncode == 0, "npm init failed")
    installed = _run([npm, "install", "--no-audit", "--no-fund", str(tarballs[0])], env=dict(os.environ), cwd=project)
    _require(installed.returncode == 0, "npm install of the tarball failed")
    package = project / "node_modules" / Path(*PACKAGE_NAME.split("/"))
    _require((package / "bin" / "gaia").is_file(), f"{PACKAGE_NAME} is not installed at {package}")

    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_PROJECT_DIR", "INIT_CWD", "GAIA_WORKSPACE_PATH")}
    env.update(HOME=str(home), USERPROFILE=str(home), GAIA_DATA_DIR=str(data), GAIA_DB=str(db))
    gaia = [sys.executable, str(package / "bin" / "gaia")]

    doctor = (package / "bin" / "cli" / "doctor.py").read_text(encoding="utf-8")
    expected = int(re.search(r"^EXPECTED_SCHEMA_VERSION\s*=\s*(\d+)", doctor, re.M).group(1))
    applied = _run([*gaia, "migrate", "apply", "--db", str(db)], env=env, cwd=root)
    if applied.returncode != 0:
        consent = re.search(r"--consent-chain (\S+)", applied.stderr)
        _require(consent is not None, "gaia migrate apply failed without asking a chain consent")
        applied = _run([*gaia, "migrate", "apply", "--db", str(db), "--consent-chain", consent.group(1)], env=env, cwd=root)
    _require(applied.returncode == 0, "gaia migrate apply failed")
    final = _schema_version(db)
    _require(final == expected, f"ledger at v{final}, expected v{expected}")
    print(f"migrated v{args.base} -> v{final}")

    installed = _run([*gaia, "install", "--workspace", str(npm_ws), "--db-path", str(db)], env=env, cwd=npm_ws)
    _require(installed.returncode == 0, "gaia install failed")
    registered = {}
    for settings in sorted((npm_ws / ".claude").glob("settings*.json")):
        registered.update(json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}))
    pre_tool_use = {
        "session_id": "upgrade-smoke", "cwd": str(npm_ws), "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "tool_input": {"command": "git status"},
    }
    hook = _run_hook(_hook_command(registered, "PreToolUse"), pre_tool_use, env=env, cwd=npm_ws)
    _require(hook.returncode == 0, f"the registered PreToolUse command exited {hook.returncode}")
    print("npm channel: registered hook command ran")

    built = _run([sys.executable, str(package / "scripts" / "build-plugin.py"), "gaia", "--manifests-only", "--output-dir", str(package)], env=env, cwd=package)
    _require(built.returncode == 0, "build-plugin.py failed on the installed package")
    plugin_env = {**env, "CLAUDE_PLUGIN_ROOT": str(package), "CLAUDE_PLUGIN_DATA": str(plugin_data), "PYTHONPATH": str(package)}
    registered_ws = _run([sys.executable, "-m", "gaia.install_root", str(plugin_ws)], env=plugin_env, cwd=package)
    _require(registered_ws.returncode == 0, "registering the plugin workspace failed")
    plugin_hooks = json.loads((package / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    session_start = {"session_id": "upgrade-smoke", "cwd": str(plugin_ws), "hook_event_name": "SessionStart", "source": "startup"}
    hook = _run_hook(_hook_command(plugin_hooks, "SessionStart"), session_start, env=plugin_env, cwd=plugin_ws)
    _require(hook.returncode == 0, f"the plugin SessionStart command exited {hook.returncode}")
    _require((plugin_ws / ".claude" / "gaia-manifest.json").is_file(), "the plugin session recorded no manifest")
    print("plugin channel: SessionStart ran and recorded its writes")

    _uninstall_leaves_nothing(gaia, npm_ws, env)
    _uninstall_leaves_nothing(gaia, plugin_ws, plugin_env)
    print(f"PASS v{args.base} -> v{final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
