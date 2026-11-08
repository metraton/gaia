"""Refuses a subagent shell command that reads or copies the host's persisted tool output."""

from __future__ import annotations

import os
import re
from typing import Optional

from .sensitive_paths import expand_home

RULE = "floor.host-tool-output"
_SPOOL = re.compile(r"(?:^|/)\.claude/projects/(?:[^/\s'\"]+/)+tool-results(?:/|$)")
_WORD = re.compile(r"[^\s'\"=<>|;&()`]+")

REJECTION = (
    "[HOST_TOOL_OUTPUT] Refusing a command that reads or copies Claude Code's persisted tool output "
    "(~/.claude/projects/*/tool-results): {path}. That file is a transcript of an earlier call, not a "
    "source. Regenerate the data from its source into your scratch entry "
    "(~/.gaia/scratch/<contract_id>) and read it there, or open the spool with the Read tool. Not approvable."
)


def spool_path(command: str, cwd: Optional[str] = None) -> Optional[str]:
    """The host tool-output path *command* names or runs in, or None."""
    if cwd and _SPOOL.search(os.path.abspath(cwd) + "/"):
        return cwd
    for word in _WORD.findall(command or ""):
        path = expand_home(word)
        if path.startswith("~"):
            path = os.path.expanduser(path)
        if _SPOOL.search(path):
            return word
    return None


def check(command: str, cwd: Optional[str], is_subagent: bool) -> Optional[str]:
    """The refusal for a subagent command that touches host tool output, else None."""
    from .protected_path_guard import _stores_its_words

    if not is_subagent or _stores_its_words(command):
        return None
    path = spool_path(command, cwd)
    return REJECTION.format(path=path) if path else None
