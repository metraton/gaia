"""
subagent_resume_point_guard.py -- the session's resume point is the orchestrator's to write.

PreToolUse Bash guard that refuses `gaia session resume-point set` from a
SUBAGENT dispatch context. The resume point is the orchestrator's own note of
where the session stands; the compaction snapshot shows it to the next turn, so
a subagent writing it would speak for the orchestrator. Reads stay free:
`gaia session snapshot`, `gaia session preview` and any `--help`.

Categorical, not approvable, like `subagent_memory_write_guard`: the subagent's
path is to report its state in its contract, which the orchestrator reads.

The decision is made on the shell's words (shlex-unquoted per component), never
on raw text: `resume-poin\\t`, `'resume'-point` and `resume""-point` are the
word `resume-point` to the shell. A command word only the shell can resolve
(`$G`, `${G}`, a substitution) that is followed by the write path is refused
too, since nothing here can show it is not `gaia`.

Public API:
    SESSION_VERBS: every `gaia session` verb path and how it is classified
    is_resume_point_write(command: str) -> bool
    check(command, *, is_subagent) -> tuple[bool, str | None]
"""

from __future__ import annotations

import shlex
from typing import Dict, Optional, Tuple

# Every verb under `gaia session` (bin/cli/session.py). The group classifies
# read-only in mutative_verbs.COMMAND_SUBCOMMAND_TIER_EXCEPTIONS, so a verb
# must be declared here before it ships; test_subagent_resume_point_guard
# fails on any verb the CLI registers that this map does not name.
SESSION_VERBS: Dict[Tuple[str, ...], str] = {
    ("preview",): "read",
    ("snapshot",): "read",
    ("resume-point", "set"): "orchestrator-write",
}

_WRITE_PATHS = tuple(("session",) + path for path, kind in SESSION_VERBS.items() if kind == "orchestrator-write")
_HELP_FLAGS = frozenset({"--help", "-h", "-?", "--usage"})
_SHELL_RESOLVED = ("$", "`", "(", ")")

REJECTION_MESSAGE = (
    "The session's resume point is the orchestrator's to write; a subagent "
    "does not run `gaia session resume-point set`. Report where your work "
    "stands in your contract instead. Do NOT retry -- this is not approvable."
)


def is_resume_point_write(command: str) -> bool:
    """True iff ``command`` runs `gaia session resume-point set`, in any spelling the shell rejoins, outside a help-only call."""
    if not command:
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
    """True iff one operator-free component's words run the resume-point write."""
    try:
        words = shlex.split(component)
    except ValueError:
        words = component.split()
    if help_exempt and _HELP_FLAGS & set(words):
        return False

    positional = [w for w in words if not w.startswith("-")]
    for index in range(1, len(positional)):
        if tuple(positional[index:index + 3]) not in _WRITE_PATHS:
            continue
        command_word = positional[index - 1]
        if command_word.rsplit("/", 1)[-1] == "gaia" or any(c in command_word for c in _SHELL_RESOLVED):
            return True
    return False


def check(command: str, *, is_subagent: bool = False) -> Tuple[bool, Optional[str]]:
    """(True, None) unless a subagent writes the resume point, then (False, message)."""
    if is_subagent and is_resume_point_write(command):
        return False, REJECTION_MESSAGE
    return True, None
