"""The install smoke: Gaia as each install channel, its session commands judged by that copy's own hooks, then run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

PREFERENCE_SLUG = "user_pref_release_smoke"
PREFERENCE_TITLE = "Release smoke: answer in one line."
SUBAGENT_ID = "a0000000000000000"
IDENTITY_CHECKS = ("Package integrity", "Install provenance")
TIMEOUT = 240
_WINDOWS = os.name == "nt"
# A contract is closed by the specialist that owns it; the orchestrator's guard refuses it by design.
_SUBAGENT_ONLY_GROUPS = ("contract",)
_PACKAGE_PATH = ("@jaguilar87", "gaia")

_VALIDATE = """
import json, sys
root, workspace = sys.argv[1], sys.argv[2]
sys.path[:0] = [root + "/hooks", root]
from modules.tools.bash_validator import BashValidator
verdicts = []
for command, roles in json.load(sys.stdin):
    for agent_type, agent_id in (("gaia-orchestrator", None), ("developer", "SUBAGENT_ID"))[2 - roles:]:
        payload = {"session_id": "release-smoke", "agent_type": agent_type}
        if agent_id:
            payload["agent_id"] = agent_id
        result = BashValidator().validate(
            command, is_subagent=bool(agent_id), session_id="release-smoke",
            agent_type=agent_type, hook_payload=payload, cwd=workspace,
        )
        verdicts.append({"command": command, "role": agent_type, "allowed": result.allowed, "reason": result.reason})
print(json.dumps(verdicts))
""".replace("SUBAGENT_ID", SUBAGENT_ID)

_RECORD_DEV_INSTALL = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from gaia.install_provenance import capture_source, record_install
record_install(Path(sys.argv[2]), capture_source(Path(sys.argv[1]), tarball=Path(sys.argv[3])))
"""


@dataclass
class Channel:
    """One install of Gaia: where it lives, how a user spells it, how it runs, and the PATH entry that serves a bare `gaia`."""

    name: str
    root: Path
    spellings: list[str]
    run: list[str]
    path_dir: Path | None = None


def smoke(tarball: Path, version: str) -> tuple[list[str], list[str]]:
    """Lay *tarball* out as every channel in a throwaway home and drive each; returns (problems, channels passed)."""
    with tempfile.TemporaryDirectory(prefix="gaia-release-smoke-", ignore_cleanup_errors=True) as tmp:
        home = Path(tmp).resolve()
        try:
            with tarfile.open(tarball) as archive:
                archive.extractall(home / "unpacked", filter="data")
        except (OSError, tarfile.TarError) as exc:
            return [f"cannot extract {tarball}: {exc}"], []
        workspace, data_dir = home / "workspace", home / "gaia-data"
        workspace.mkdir()
        channels = lay_out(home / "unpacked" / "package", home, workspace, data_dir, version)
        return _drive_all(channels, home, workspace, data_dir, tarball=tarball, version=version)


def smoke_installed(channels: list[Channel], version: str) -> tuple[list[str], list[str]]:
    """Drive copies installed on this machine with a throwaway home and data directory; returns (problems, channels passed)."""
    with tempfile.TemporaryDirectory(prefix="gaia-release-smoke-live-", ignore_cleanup_errors=True) as tmp:
        home = Path(tmp).resolve()
        workspace, data_dir = home / "workspace", home / "gaia-data"
        workspace.mkdir()
        return _drive_all(channels, home, workspace, data_dir, tarball=None, version=version)


def lay_out(package: Path, home: Path, workspace: Path, data_dir: Path, version: str) -> list[Channel]:
    """Copy *package* to where each channel installs it, with the launchers npm writes, and describe each."""
    prefix = home / "npm-global"
    global_root = (prefix if _WINDOWS else prefix / "lib") / "node_modules" / Path(*_PACKAGE_PATH)
    local_root = workspace / "node_modules" / Path(*_PACKAGE_PATH)
    marketplace = home / ".claude" / "plugins" / "cache" / "gaia-marketplace" / "gaia" / version
    dev_plugin = data_dir / "cache" / "dev-plugin" / "release-smoke"
    for root in (marketplace, local_root, global_root, dev_plugin):
        shutil.copytree(package, root, symlinks=True)
    return [
        installed_channel("marketplace", marketplace),
        installed_channel("npm-local", local_root, _write_launchers(workspace / "node_modules" / ".bin", local_root)),
        installed_channel("npm-global", global_root, _write_launchers(prefix if _WINDOWS else prefix / "bin", global_root)),
        installed_channel("dev-plugin", dev_plugin),
    ]


def installed_channel(name: str, root: Path, path_dir: Path | None = None) -> Channel:
    """The channel whose install root is *root*; *path_dir* holds its `gaia` launcher when the channel puts one on PATH."""
    cli = root / "bin" / "gaia"
    launcher = path_dir / "gaia" if path_dir is not None and not _WINDOWS else cli
    return Channel(name, root, _spellings(cli), [sys.executable, str(launcher)], path_dir)


def _spellings(cli: Path) -> list[str]:
    forward = cli.as_posix()
    if not _WINDOWS:
        return [forward]
    return [forward, f"/{forward[0].lower()}{forward[2:]}", f"'{cli}'"]


def _write_launchers(bin_dir: Path, root: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    target = os.path.relpath(root / "bin" / "gaia", bin_dir)
    if not _WINDOWS:
        (bin_dir / "gaia").symlink_to(target)
        return bin_dir
    forward, backward = target.replace("\\", "/"), target.replace("/", "\\")
    (bin_dir / "gaia.cmd").write_text(
        "@ECHO off\r\nGOTO start\r\n:find_dp0\r\nSET dp0=%~dp0\r\nEXIT /b\r\n:start\r\nSETLOCAL\r\nCALL :find_dp0\r\n\r\n"
        'IF EXIST "%dp0%\\python3.exe" (\r\n  SET "_prog=%dp0%\\python3.exe"\r\n) ELSE (\r\n  SET "_prog=python3"\r\n'
        "  SET PATHEXT=%PATHEXT:;.JS;=;%\r\n)\r\n\r\n"
        f'endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\\{backward}" %*\r\n',
        encoding="utf-8",
    )
    (bin_dir / "gaia.ps1").write_text(
        "#!/usr/bin/env pwsh\n$basedir=Split-Path $MyInvocation.MyCommand.Definition -Parent\n\n$exe=\"\"\n"
        "if ($PSVersionTable.PSVersion -lt \"6.0\" -or $IsWindows) {\n  $exe=\".exe\"\n}\n$ret=0\n"
        f"if (Test-Path \"$basedir/python3$exe\") {{\n  & \"$basedir/python3$exe\"  \"$basedir/{forward}\" $args\n"
        f"  $ret=$LASTEXITCODE\n}} else {{\n  & \"python3$exe\"  \"$basedir/{forward}\" $args\n  $ret=$LASTEXITCODE\n}}\nexit $ret\n",
        encoding="utf-8",
    )
    (bin_dir / "gaia").write_text(
        "#!/bin/sh\nbasedir=$(dirname \"$(echo \"$0\" | sed -e 's,\\\\,/,g')\")\n\n"
        f"if [ -x \"$basedir/python3\" ]; then\n  exec \"$basedir/python3\"  \"$basedir/{forward}\" \"$@\"\n"
        f"else\n  exec python3  \"$basedir/{forward}\" \"$@\"\nfi\n",
        encoding="utf-8",
    )
    return bin_dir


def _environment(home: Path, data_dir: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("GAIA_", "CLAUDE_", "XDG_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), GAIA_DATA_DIR=str(data_dir), PYTHONUTF8="1")
    env["PATH"] = os.pathsep.join(
        folder for folder in env.get("PATH", "").split(os.pathsep) if folder and not shutil.which("gaia", path=folder)
    )
    return env


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], stdin: str | None = None) -> tuple[int, str]:
    rc, out, err = _run_split(argv, cwd=cwd, env=env, stdin=stdin)
    return rc, out + err


def _run_split(argv: list[str], *, cwd: Path, env: dict[str, str], stdin: str | None = None) -> tuple[int, str, str]:
    try:
        done = subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)
    return done.returncode, done.stdout or "", done.stderr or ""


def _tail(text: str, size: int = 600) -> str:
    return " ".join(text.split())[-size:]


def _drive_all(channels: list[Channel], home: Path, workspace: Path, data_dir: Path, *,
               tarball: Path | None, version: str) -> tuple[list[str], list[str]]:
    (workspace / ".claude").mkdir(exist_ok=True)
    env = _environment(home, data_dir)
    seeds, problems = _seed(channels[0], workspace, env)
    if seeds is None:
        return problems, []
    passed = []
    for channel in channels:
        found = _drive(channel, workspace, env, seeds, tarball=tarball, version=version)
        problems += [f"{channel.name}: {problem}" for problem in found]
        if not found:
            passed.append(channel.name)
    return problems, passed


def _seed(channel: Channel, workspace: Path, env: dict[str, str]) -> tuple[dict[str, str] | None, list[str]]:
    def cli(*args: str) -> tuple[int, str]:
        return _run([*channel.run, *args], cwd=workspace, env=env)

    rc, out = cli("memory", "add", f"--name={PREFERENCE_SLUG}", "--type=user", "--kind=preference",
                  "--surface=conversation", f"--description={PREFERENCE_TITLE}", "--body=Seeded by the release smoke.")
    if rc != 0:
        return None, [f"seeding the conversation preference failed (exit {rc}): {_tail(out)}"]
    rc, out = cli("approvals", "request-set", "--command", "kubectl delete pod release-smoke", "--cwd", str(workspace),
                  "--what", "Release smoke request", "--question", "Delete the smoke pod?", "--does", "Deletes a pod",
                  "--impact", "The pod is gone", "--verification", "Nothing runs", "--shared-state", "None",
                  "--rollback", "Nothing to undo", "--agent-id", SUBAGENT_ID, "--session-id", "release-smoke", "--json")
    approval_id = _json_field(out, "approval_id") if rc == 0 else None
    if not approval_id:
        return None, [f"seeding a pending approval failed (exit {rc}): {_tail(out)}"]
    rc, out = cli("contract", "init", "--json")
    draft_id = (_json_field(out, "draft_id") or _json_field(out, "contract_id")) if rc == 0 else None
    if not draft_id:
        return None, [f"seeding a contract draft failed (exit {rc}): {_tail(out)}"]
    for field, value in (
        ("agent_status.next_action", "nothing; a release smoke draft"),
        ("evidence_report.open_gaps", '["release smoke: no work was asked"]'),
        ("conclusion", "Release smoke draft, closed blocked on purpose."),
        ("agent_status.agent_state", "BLOCKED"),
    ):
        rc, out = cli("contract", "set", "--draft-id", draft_id, field, value)
        if rc != 0:
            return None, [f"preparing the contract draft ({field}) failed (exit {rc}): {_tail(out)}"]
    return {"approval_id": approval_id, "draft_id": draft_id}, []


def _json_field(output: str, key: str) -> str | None:
    for line in reversed(output.strip().splitlines()):
        try:
            value = json.loads(line).get(key)
        except (ValueError, AttributeError):
            continue
        if isinstance(value, str) and value:
            return value
    return None


def _drive(channel: Channel, workspace: Path, env: dict[str, str], seeds: dict[str, str], *,
           tarball: Path | None, version: str) -> list[str]:
    env = {**env, "PATH": os.pathsep.join(filter(None, (str(channel.path_dir or ""), env["PATH"])))}
    session = [
        ["--version"], ["now"], ["doctor"], ["memory", "show", PREFERENCE_SLUG],
        ["approvals", "question", seeds["approval_id"]], ["contract", "finalize", "--draft-id", seeds["draft_id"]],
    ]
    commands = [
        (" ".join([spelling, *args]), 1 if args[0] in _SUBAGENT_ONLY_GROUPS else 2)
        for spelling in [*channel.spellings, "gaia"] for args in session
    ]
    problems = _refusals(channel, workspace, env, commands)

    if tarball is not None and channel.name == "npm-local":
        problems += _switch_from_dev(channel, workspace, env, tarball, version)
    for args in session:
        if args == ["--version"]:
            problems += _version_problems(channel, workspace, env, version)
            continue
        argv = ["doctor", "--json", "--workspace", str(workspace)] if args == ["doctor"] else args
        rc, out = _run([*channel.run, *argv], cwd=workspace, env=env)
        if args == ["doctor"]:
            problems += _identity_errors(out)
        elif rc != 0:
            problems.append(f"`gaia {' '.join(args)}` exited {rc}: {_tail(out)}")

    payload = {"session_id": "release-smoke", "hook_event_name": "UserPromptSubmit", "prompt": "hello", "cwd": str(workspace)}
    rc, out = _run([sys.executable, str(channel.root / "hooks" / "user_prompt_submit.py")],
                   cwd=workspace, env=env, stdin=json.dumps(payload))
    if PREFERENCE_TITLE not in out:
        problems.append(f"the prompt hook (exit {rc}) did not name the seeded conversation preference: {_tail(out)}")
    return problems


def _version_problems(channel: Channel, workspace: Path, env: dict[str, str], version: str) -> list[str]:
    """`gaia --version` loads every plugin, so any stderr there is a CLI that fails before the user's command runs."""
    rc, out, err = _run_split([*channel.run, "--version"], cwd=workspace, env=env)
    if rc == 0 and out.splitlines() == [f"gaia {version}"] and not err:
        return []
    return [f"`gaia --version` must exit 0 printing only `gaia {version}`; exit {rc}, "
            f"stdout: {_tail(out, 200)!r}, stderr: {_tail(err, 300)!r}"]


def _refusals(channel: Channel, workspace: Path, env: dict[str, str], commands: list[str]) -> list[str]:
    rc, out = _run([sys.executable, "-c", _VALIDATE, str(channel.root), str(workspace)],
                   cwd=workspace, env=env, stdin=json.dumps(commands))
    try:
        verdicts = json.loads(out.strip().splitlines()[-1]) if rc == 0 else None
    except (ValueError, IndexError):
        verdicts = None
    if verdicts is None:
        return [f"the hook probe exited {rc}: {_tail(out)}"]
    return [
        f"{v['role']}: `{v['command']}` refused -- {_tail(v['reason'] or '', 300)}"
        for v in verdicts if not v["allowed"]
    ]


def _identity_errors(doctor_output: str) -> list[str]:
    try:
        checks = json.loads(doctor_output[doctor_output.index("{"):]).get("checks", [])
    except (ValueError, AttributeError):
        return [f"`gaia doctor --json` printed no report: {_tail(doctor_output)}"]
    return [
        f"doctor {check.get('name')}: {_tail(str(check.get('detail', '')), 300)}"
        for check in checks
        if check.get("name") in IDENTITY_CHECKS and check.get("severity") == "error"
    ]


def _switch_from_dev(channel: Channel, workspace: Path, env: dict[str, str], tarball: Path, version: str) -> list[str]:
    manifest = workspace / "package.json"

    def declare(spec: str) -> None:
        manifest.write_text(json.dumps({"name": "release-smoke", "private": True,
                                        "dependencies": {"@jaguilar87/gaia": spec}}) + "\n", encoding="utf-8")

    declare(f"file:{tarball.as_posix()}")
    rc, out = _run([sys.executable, "-c", _RECORD_DEV_INSTALL, str(channel.root), str(workspace), str(tarball)],
                   cwd=workspace, env=env)
    if rc != 0:
        return [f"recording a dev install failed (exit {rc}): {_tail(out)}"]
    declare(f"^{version}")
    rc, out = _run([*channel.run, "install", "--channel", "npm", "--workspace", str(workspace), "--quiet"],
                   cwd=workspace, env=env)
    return [] if rc == 0 else [f"`gaia install --channel npm` after a dev install exited {rc}: {_tail(out)}"]
