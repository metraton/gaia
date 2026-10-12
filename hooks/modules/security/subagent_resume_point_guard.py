"""
subagent_resume_point_guard.py -- the session's resume point is the orchestrator's to write.

PreToolUse Bash guard that refuses `gaia session resume-point set` from a
SUBAGENT dispatch context. The resume point is the orchestrator's own note of
where the session stands; the compaction snapshot shows it to the next turn, so
a subagent writing it would speak for the orchestrator. Reads stay free:
`gaia session snapshot`, `gaia session preview` and any `--help`.

Categorical, not approvable, like `subagent_memory_write_guard`: the subagent's
path is to report its state in its contract, which the orchestrator reads.

Public API:
    is_resume_point_write(command: str) -> bool
    check(command, *, is_subagent) -> tuple[bool, str | None]
"""

from __future__ import annotations

import shlex
from typing import Optional, Tuple

_WRITE_PATH = ("session", "resume-point", "set")
_HELP_FLAGS = frozenset({"--help", "-h", "-?", "--usage"})

REJECTION_MESSAGE = (
    "The session's resume point is the orchestrator's to write; a subagent "
    "does not run `gaia session resume-point set`. Report where your work "
    "stands in your contract instead. Do NOT retry -- this is not approvable."
)


def is_resume_point_write(command: str) -> bool:
    """True iff ``command`` invokes `gaia session resume-point set`, in any invocation form, outside a help-only call."""
    if not command or "resume-point" not in command:
        return False

    from ..tools.shell_parser import parse_command

    # A substitution runs a command inside the component the help exemption
    # would otherwise speak for, so its presence withdraws the exemption.
    help_exempt = not any(marker in command for marker in ("$(", "`", "<(", ">("))
    return any(
        _component_writes_resume_point(component, help_exempt=help_exempt)
        for component in parse_command(command)
    )


def _component_writes_resume_point(component: str, *, help_exempt: bool) -> bool:
    """True iff one operator-free component invokes the resume-point write."""
    try:
        tokens = shlex.split(component)
    except ValueError:
        tokens = component.split()

    for i, token in enumerate(tokens):
        if token.rsplit("/", 1)[-1] != "gaia":
            continue
        rest = tokens[i + 1:]
        path = tuple(t for t in rest if not t.startswith("-"))[: len(_WRITE_PATH)]
        if path != _WRITE_PATH:
            continue
        if help_exempt and _HELP_FLAGS & set(rest):
            continue
        return True
    return False


def check(command: str, *, is_subagent: bool = False) -> Tuple[bool, Optional[str]]:
    """(True, None) unless a subagent writes the resume point, then (False, message)."""
    if is_subagent and is_resume_point_write(command):
        return False, REJECTION_MESSAGE
    return True, None
