"""Whether this host can run Gaia's PreToolUse gate so that a failure blocks the tool."""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from typing import Optional

# Claude Code 2.1.295 added `onFailure: "block"`. Must equal the claude-code
# engine scripts/build-plugin.py writes into plugin.json and the one
# bin/pre-publish-validate.js requires; tests/hooks/test_hook_fails_closed.py
# checks all three.
MIN_CLAUDE_CODE_VERSION = (2, 1, 295)


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


def claude_code_version() -> Optional[tuple[int, int, int]]:
    """The installed Claude Code version, or None when it cannot be read."""
    for cmd in ("claude", "claude-code"):
        if not shutil.which(cmd):
            continue
        try:
            proc = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        found = re.match(r"\s*(\d+)\.(\d+)\.(\d+)", proc.stdout or "")
        return tuple(map(int, found.groups())) if found else None
    return None


def outdated_claude_code() -> Optional[str]:
    """Explain why the installed Claude Code would let a failed gate pass, or None.

    An unreadable or absent version returns None: refusing then would block
    installs that precede Claude Code or target only OpenCode.
    """
    version = claude_code_version()
    if version is None or version >= MIN_CLAUDE_CODE_VERSION:
        return None
    found = ".".join(map(str, version))
    minimum = ".".join(map(str, MIN_CLAUDE_CODE_VERSION))
    return (
        f"Claude Code {found} is older than {minimum}: its PreToolUse hooks ignore "
        "onFailure, so a gate that cannot start lets the tool run. Run `claude update`."
    )
