"""Whether this host can start Gaia's hooks, which every registration runs as `sh <launcher>`."""
from __future__ import annotations

import shutil
import sys
from typing import Optional


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
