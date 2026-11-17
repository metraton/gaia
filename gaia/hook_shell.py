"""Whether this host can run Gaia's PreToolUse gate so that a failure blocks the tool."""
from __future__ import annotations

import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gaia.subprocess_text import run_text

# Claude Code 2.1.295 added `onFailure: "block"`. Must equal the claude-code
# engine scripts/build-plugin.py writes into plugin.json and the one
# bin/pre-publish-validate.js requires; tests/hooks/test_hook_fails_closed.py
# checks all three.
MIN_CLAUDE_CODE_VERSION = (2, 1, 295)


@dataclass(frozen=True)
class HostVersionFinding:
    """A Claude Code too old to honour onFailure; severity is "error" or "warning"."""

    severity: str
    message: str


GIT_BASH_OVERRIDE = "CLAUDE_CODE_GIT_BASH_PATH"
# Claude Code ignores the override unless its basename is one of these.
_OVERRIDE_BASENAMES = ("bash.exe", "sh.exe", "bash", "sh")
_GIT_INSTALL_ROOTS = (("ProgramFiles", ""), ("ProgramW6432", ""), ("ProgramFiles(x86)", ""),
                      ("LOCALAPPDATA", "Programs"))


def _sh_in_git_root(root: Path) -> Optional[str]:
    for candidate in (root / "usr" / "bin" / "sh.exe", root / "bin" / "sh.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _sh_beside_override(value: object) -> Optional[str]:
    if not isinstance(value, str) or ntpath.basename(value).lower() not in _OVERRIDE_BASENAMES:
        return None
    folder = ntpath.dirname(value)
    for name in ("sh.exe", "sh"):
        candidate = os.path.join(folder, name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _settings_overrides(workspace: Optional[Path]) -> list[tuple[object, str]]:
    """The override each settings file declares, in Claude Code's precedence: local, project, user."""
    files = [workspace / ".claude" / "settings.local.json", workspace / ".claude" / "settings.json"] if workspace else []
    files.append(Path.home() / ".claude" / "settings.json")
    found = []
    for path in files:
        try:
            env = json.loads(path.read_text(encoding="utf-8")).get("env")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(env, dict) and GIT_BASH_OVERRIDE in env:
            found.append((env[GIT_BASH_OVERRIDE], f"{GIT_BASH_OVERRIDE} in {path}"))
    return found


def resolve_hook_shell(workspace: Optional[Path] = None) -> Optional[tuple[str, str]]:
    """The `sh` hook commands start with and how it was found, as ``(path, source)``; on Windows the Git Bash one, wherever PATH points."""
    on_path = shutil.which("sh")
    if sys.platform != "win32":
        return (on_path, "PATH") if on_path else None
    overrides = [(os.environ.get(GIT_BASH_OVERRIDE), f"{GIT_BASH_OVERRIDE} in the environment")]
    for value, source in overrides + _settings_overrides(workspace):
        sh = _sh_beside_override(value)
        if sh:
            return sh, source
    if on_path:
        return on_path, "PATH"
    git = shutil.which("git")
    if git:
        for root in list(Path(git).parents)[:3]:
            sh = _sh_in_git_root(root)
            if sh:
                return sh, f"git on PATH ({git})"
    for variable, subfolder in _GIT_INSTALL_ROOTS:
        base = os.environ.get(variable)
        sh = _sh_in_git_root(Path(base, subfolder, "Git")) if base else None
        if sh:
            return sh, f"Git for Windows under %{variable}%"
    return None


def describe_hook_shell(found: tuple[str, str]) -> str:
    """One line naming the hook shell and its source, printed alike by install, update and doctor."""
    path, source = found
    return f"{path} (found by {source})"


def missing_hook_shell(hosts: Sequence[str] = ("claude_code",), workspace: Optional[Path] = None) -> Optional[str]:
    """Explain why Claude Code's shell-form hooks cannot start on this Windows host, or return None; OpenCode needs no `sh`."""
    if sys.platform != "win32" or "claude_code" not in hosts or resolve_hook_shell(workspace):
        return None
    return (
        "no Git Bash `sh` found: Gaia's hooks run as `sh <launcher>`, and without Git Bash "
        "every gated tool call would be blocked. Install Git for Windows "
        f"(https://git-scm.com/download/win), or set {GIT_BASH_OVERRIDE} to its bash.exe."
    )


def hook_shell_path(workspace: Optional[Path] = None) -> Optional[str]:
    """The `sh` every hook command starts with, or None."""
    found = resolve_hook_shell(workspace)
    return found[0] if found else None


# hooks/launch.sh tries these in this order. Windows' App Execution Alias for
# python3 (and python) under WindowsApps opens the Microsoft Store when no
# Python is installed and exits 9009, so a name that resolves proves nothing
# until it runs.
_HOOK_PYTHON_CANDIDATES = (("python3",), ("python",), ("py", "-3"))


def runs_python3(argv: list[str]) -> bool:
    """True when *argv* starts and exits as Python 3; a missing file or the Store stub is False."""
    try:
        proc = run_text([*argv, "-c", "import sys; sys.exit(sys.version_info[0] != 3)"],
                        capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def hook_python() -> Optional[list[str]]:
    """The resolved argv of the first candidate that runs as Python 3, or None when none does."""
    for name, *flags in _HOOK_PYTHON_CANDIDATES:
        found = shutil.which(name)
        if found and runs_python3([found, *flags]):
            return [found, *flags]
    return None


def running_host_binary() -> Optional[str]:
    """The Claude Code binary running this session, or None outside one.

    CLAUDE_CODE_CHILD_SESSION=1 is documented (env-vars reference) as set only
    by Claude Code in the processes it spawns. CLAUDE_CODE_EXECPATH, the
    binary's path, is measured in those processes but not documented, so it is
    trusted only alongside the documented marker.
    """
    if os.environ.get("CLAUDE_CODE_CHILD_SESSION") != "1":
        return None
    return os.environ.get("CLAUDE_CODE_EXECPATH") or None


def read_claude_code_version(binary: str) -> Optional[tuple[int, int, int]]:
    """The version `binary --version` reports, or None when it cannot be read."""
    try:
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", proc.stdout if isinstance(proc.stdout, str) else "")
    return tuple(map(int, found.groups())) if found else None


def _below_minimum(binary: str) -> Optional[str]:
    version = read_claude_code_version(binary)
    if version is None or version >= MIN_CLAUDE_CODE_VERSION:
        return None
    return ".".join(map(str, version))


def claude_code_version_finding() -> Optional[HostVersionFinding]:
    """Report a Claude Code below the minimum, graded by whether it is the running host.

    The host running this session is an error: its PreToolUse hooks ignore
    onFailure, so a gate that cannot start lets the tool run. A `claude` on PATH
    is only a warning, because nothing proves it is the binary that loads Gaia
    (a desktop app ships its own); an unreadable version reports nothing.
    """
    minimum = ".".join(map(str, MIN_CLAUDE_CODE_VERSION))
    host = running_host_binary()
    if host:
        old = _below_minimum(host)
        if old:
            return HostVersionFinding(
                "error",
                f"the Claude Code running this session ({host}) is {old}, older than {minimum}: "
                "its PreToolUse hooks ignore onFailure, so a gate that cannot start lets the tool run. "
                "Run `claude update`.",
            )
    path_binary = shutil.which("claude") or shutil.which("claude-code")
    if not path_binary or (host and os.path.realpath(path_binary) == os.path.realpath(host)):
        return None
    old = _below_minimum(path_binary)
    if not old:
        return None
    if host:
        scope = "not the host running this session"
    else:
        scope = "no running host was detected, so it may not be the Claude Code that loads Gaia"
    return HostVersionFinding(
        "warning",
        f"{path_binary} is Claude Code {old}, older than {minimum} ({scope}). "
        "If it loads Gaia, run `claude update`.",
    )
