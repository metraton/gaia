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
  5. plugin channel: the package's hooks.json must equal what
     ``build-plugin.py`` builds, then its SessionStart command runs as written
     with the package as CLAUDE_PLUGIN_ROOT
  6. ``gaia uninstall`` on both workspaces, which must leave no manifest and
     nothing left to revert

Every path the package sees contains a space. Exit 0 only when all six hold.

With ``--from-release`` the cell is instead a user's global upgrade, run in the
runner's real home and npm prefix: the published release is installed with
``npm install -g`` and wired by its own ``gaia install``, the branch tarball
replaces it, and a bare ``gaia install`` with no input must converge, change
nothing on a second run, leave doctor without hook, shim or old-data warnings,
and leave neither manifest nor shim behind ``npm uninstall -g``.
"""

from __future__ import annotations

import argparse
import hashlib
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
    result = json.loads(done.stdout)
    _require(result.get("manifest", {}).get("source") == "manifest", f"uninstall did not revert from a manifest: {result.get('cleanup_error')}")
    _require(not (workspace / ".claude" / "gaia-manifest.json").exists(), "the manifest survived uninstall")
    again = _run([*gaia, "uninstall", "--workspace", str(workspace), "--json", "--dry-run", "--no-backup"], env=env, cwd=workspace)
    left = json.loads(again.stdout)["manifest"]
    _require(left["source"] == "none" and not left["reverted"], f"uninstall left a footprint: {left}")
    print(f"uninstall: manifest empty in {workspace.name}")


# The doctor checks that speak for hooks, shims and data an older release left.
UPGRADE_DOCTOR_CHECKS = frozenset({
    "Hook shell", "Hook Python", "Hook registrations", "Hook commands", "Hook files", "hooks-importable",
    "PATH gaia", "Launcher Python", "Global CLI alignment", "Symlinks", "Symlinks freshness",
    "Schema version", "Memory store", "Last install error",
})
LAUNCHERS = ("gaia", "gaia.cmd", "gaia.ps1")


def _footprint(workspace: Path, home: Path, db: Path) -> dict[str, str]:
    """Digest of what an install writes: the workspace's .claude tree, the launchers and the ledger."""
    seen: dict[str, str] = {}

    def walk(path: Path, key: str) -> None:
        if path.is_symlink() or os.path.isjunction(path):
            seen[key] = "link:" + os.readlink(path)
        elif path.is_dir():
            for child in sorted(path.iterdir()):
                if child.name != "logs":
                    walk(child, f"{key}/{child.name}")
        elif path.is_file():
            seen[key] = hashlib.sha256(path.read_bytes()).hexdigest()

    walk(workspace / ".claude", ".claude")
    for name in LAUNCHERS:
        walk(home / ".local" / "bin" / name, f"~/.local/bin/{name}")
    seen["schema_version"] = str(_schema_version(db))
    return seen


def global_upgrade(release: str, pack_dir: Path, root: Path) -> int:
    """Upgrade a global ``npm install -g`` of *release* to the packed tarball with a bare install."""
    workspace = root / "user workspace"
    workspace.mkdir(parents=True)
    home, db = Path.home(), Path.home() / ".gaia" / "gaia.db"
    env = {k: v for k, v in os.environ.items() if not k.startswith("GAIA_") and k not in ("CLAUDE_PROJECT_DIR", "INIT_CWD")}
    npm = shutil.which("npm")
    _require(npm is not None, "npm is not on PATH")
    tarballs = sorted(pack_dir.resolve().glob("*.tgz"))
    _require(len(tarballs) == 1, f"expected one tarball in {pack_dir}, found {tarballs}")
    print(f"python3 on PATH: {shutil.which('python3') or 'absent'}")

    _require(_run([npm, "install", "-g", f"{PACKAGE_NAME}@{release}"], env=env, cwd=workspace).returncode == 0,
             f"npm install -g {PACKAGE_NAME}@{release} failed")
    gaia = shutil.which("gaia", path=env["PATH"])
    _require(gaia is not None, "npm install -g left no gaia shim on PATH")
    old = _run([gaia, "install"], env=env, cwd=workspace, stdin="")
    _require(old.returncode == 0, f"gaia {release} install failed")
    print(f"{release} footprint: {sorted(_footprint(workspace, home, db))}")

    _require(_run([npm, "install", "-g", str(tarballs[0])], env=env, cwd=workspace).returncode == 0,
             "npm install -g of the branch tarball failed")
    first = _run([gaia, "install"], env=env, cwd=workspace, stdin="")
    _require(first.returncode == 0, f"bare gaia install over {release} exited {first.returncode}")
    converged = _footprint(workspace, home, db)
    second = _run([gaia, "install"], env=env, cwd=workspace, stdin="")
    _require(second.returncode == 0, f"second bare gaia install exited {second.returncode}")
    after = _footprint(workspace, home, db)
    changed = sorted(k for k in converged.keys() | after.keys() if converged.get(k) != after.get(k))
    _require(not changed, f"the second bare install changed {changed}")

    doctor = _run([gaia, "doctor", "--json"], env=env, cwd=workspace)
    checks = json.loads(doctor.stdout)["checks"]
    flagged = [f"{c['name']} [{c['severity']}]: {c.get('detail', '')}" for c in checks
               if c.get("name") in UPGRADE_DOCTOR_CHECKS and c.get("severity") in ("warning", "error")]
    _require(not flagged, f"doctor flags the upgrade: {flagged}")

    _require(_run([npm, "uninstall", "-g", PACKAGE_NAME], env=env, cwd=workspace).returncode == 0, "npm uninstall -g failed")
    left = [str(p) for p in [workspace / ".claude" / "gaia-manifest.json", *(home / ".local" / "bin" / n for n in LAUNCHERS)] if p.exists()]
    _require(not left, f"npm uninstall -g left {left}")
    _require(shutil.which("gaia", path=env["PATH"]) is None, f"a gaia shim survived npm uninstall -g: {shutil.which('gaia', path=env['PATH'])}")
    print(f"PASS global {release} -> {tarballs[0].name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--base", type=int, help="published schema version (21, 26, 33, 37, 51, 57)")
    origin.add_argument("--from-release", help="published version installed with npm install -g, then upgraded")
    parser.add_argument("--pack-dir", type=Path, required=True, help="directory holding the one npm pack tarball")
    parser.add_argument("--root", type=Path, required=True, help="empty scratch directory for this cell")
    args = parser.parse_args()

    if args.from_release:
        return global_upgrade(args.from_release, args.pack_dir, args.root.resolve())
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

    installed = _run([*gaia, "install", "--channel", "npm", "--workspace", str(npm_ws), "--db-path", str(db)],
                     env=env, cwd=npm_ws)
    _require(installed.returncode == 0, "gaia install failed")
    registered = {}
    for settings in sorted((npm_ws / ".claude").glob("settings*.json")):
        registered.update(json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}))
    pre_tool_use = {
        "session_id": "upgrade-smoke", "cwd": str(npm_ws), "hook_event_name": "PreToolUse",
        "tool_name": "Bash", "tool_input": {"command": "git status"},
    }
    # A session with no agent is the orchestrator, which may run only the gaia
    # CLI: the block (exit 2) is a decision only the Python hook can reach,
    # where a launcher that found no Python exits 1.
    hook = _run_hook(_hook_command(registered, "PreToolUse"), pre_tool_use, env=env, cwd=npm_ws)
    _require(hook.returncode == 2 and "[BLOCKED]" in hook.stdout + hook.stderr, f"the registered PreToolUse command exited {hook.returncode} without Gaia's decision")
    print("npm channel: registered hook command ran and blocked the orchestrator's git status")

    # The package is the plugin root (marketplace source npm), and build-plugin
    # needs build/, which the package does not ship: so the checkout builds the
    # manifests and the package must carry exactly what it built.
    built_dir = root / "built plugin"
    built_dir.mkdir()
    built = _run([sys.executable, str(REPO_ROOT / "scripts" / "build-plugin.py"), "gaia", "--manifests-only", "--output-dir", str(built_dir)], env=env, cwd=REPO_ROOT)
    _require(built.returncode == 0, "build-plugin.py failed")
    shipped = json.loads((package / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    _require(json.loads((built_dir / "hooks" / "hooks.json").read_text(encoding="utf-8")) == shipped, "the package's hooks.json is not what build-plugin produces")
    plugin_env = {**env, "CLAUDE_PLUGIN_ROOT": str(package), "CLAUDE_PLUGIN_DATA": str(plugin_data), "PYTHONPATH": str(package)}
    registered_ws = _run([sys.executable, "-m", "gaia.install_root", str(plugin_ws)], env=plugin_env, cwd=package)
    _require(registered_ws.returncode == 0, "registering the plugin workspace failed")
    plugin_hooks = shipped["hooks"]
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
