"""Whether this host can run Gaia's PreToolUse gate so that a failure blocks the tool."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

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


def missing_hook_shell() -> Optional[str]:
    """Explain why hooks cannot start on this host, or return None when they can.

    Only Windows can lack `sh`: Claude Code runs shell-form hooks through Git
    Bash there and falls back to PowerShell when Git Bash is absent, where `sh`
    does not resolve and every PreToolUse hook would fail and block.
    """
    if sys.platform != "win32" or shutil.which("sh"):
        return None
    return (
        "no `sh` on PATH: Gaia's hooks run as `sh <launcher>`, and without Git Bash "
        "every gated tool call would be blocked. Install Git for Windows "
        "(https://git-scm.com/download/win) and make its `sh` resolvable on PATH."
    )


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
