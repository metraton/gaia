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
word `resume-point` to the shell. A subagent's session call is free only when
its verb words are a read declared in SESSION_VERBS; anything else under
`session` -- a shell-resolved verb (`$S`, a backtick, a glob), an undeclared
verb -- is refused, as is `gaia <shell-resolved group>`. The command word
counts as gaia by name, as a GAIA_CLI_ALIASES wrapper, by realpath to the
trusted binary (a symlink), or when only the shell can resolve it before a
literal `session`. A wrapper script nobody declared keeps its ordinary tier:
nothing static sees inside it.

Public API:
    SESSION_VERBS: every `gaia session` verb path and how it is classified
    is_resume_point_write(command: str) -> bool
    check(command, *, is_subagent) -> tuple[bool, str | None]
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Sequence, Tuple

# Every verb under `gaia session` (bin/cli/session.py), and the only source of
# its tier: mutative_verbs classifies a `gaia session` call read-only only when
# its words are one of these paths, spelled literally. A verb must be declared
# here before it ships; test_subagent_resume_point_guard fails on any verb the
# CLI registers that this map does not name.
SESSION_VERBS: Dict[Tuple[str, ...], str] = {
    ("preview",): "read",
    ("snapshot",): "read",
    ("resume-point", "set"): "orchestrator-write",
}

_HELP_FLAGS = frozenset({"--help", "-h", "-?", "--usage"})
_SHELL_RESOLVED = ("$", "`", "(", ")", "*", "?", "[")


def is_shell_resolved(word: str) -> bool:
    """True when only the shell can say what *word* becomes: an expansion, a substitution or a glob."""
    return any(c in word for c in _SHELL_RESOLVED)


def declared_session_verb(words: Sequence[str]) -> Optional[Tuple[str, ...]]:
    """The SESSION_VERBS path *words* begin with, literally, or None."""
    for path in SESSION_VERBS:
        if tuple(words[:len(path)]) == path:
            return path
    return None


def _runs_gaia(word: str) -> bool:
    """True when *word* runs the gaia CLI or cannot be shown not to: its name, a declared wrapper, the trusted binary, or a shell-resolved word."""
    if is_shell_resolved(word):
        return True
    name = os.path.basename(word)
    if name == "gaia":
        return True
    from .cli_aliases import aliased_cli
    from .gaia_cli_only_guard import is_trusted_gaia_binary

    return aliased_cli(name) == "gaia" or is_trusted_gaia_binary(word)

REJECTION_MESSAGE = (
    "The session's resume point is the orchestrator's to write; a subagent "
    "does not run `gaia session resume-point set`. Report where your work "
    "stands in your contract instead. Do NOT retry -- this is not approvable."
)


def is_resume_point_write(command: str) -> bool:
    """True iff ``command`` may run `gaia session resume-point set`: a gaia session call not proven a declared read, outside a help-only call."""
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
    """True iff one operator-free component's words may run a session write: any session call not proven a declared read.

    Help is read from the gaia program's own argv only (the words after its
    command word), so a wrapper's option value such as `env -u --help` does
    not exempt the call.
    """
    from ..tools.shell_parser import shell_words

    words = shell_words(component)
    for index, word in enumerate(words):
        if word.startswith("-"):
            continue
        own_argv = words[index + 1:]
        positional = [w for w in own_argv if not w.startswith("-")]
        if not positional:
            continue
        group = positional[0]
        if group == "session":
            if not _runs_gaia(word):
                continue
        elif not is_shell_resolved(group) or is_shell_resolved(word) or not _runs_gaia(word):
            continue
        verb_words = positional[1:]
        if group == "session" and not verb_words:
            continue
        if help_exempt and _HELP_FLAGS & set(own_argv):
            continue
        path = declared_session_verb(verb_words) if group == "session" else None
        if path is None or SESSION_VERBS[path] != "read":
            return True
    return False


def check(command: str, *, is_subagent: bool = False) -> Tuple[bool, Optional[str]]:
    """(True, None) unless a subagent writes the resume point, then (False, message)."""
    if is_subagent and is_resume_point_write(command):
        return False, REJECTION_MESSAGE
    return True, None
