"""sudo, su and doas sign under their own rule: running as another user is an effect apart from what the payload does."""

from __future__ import annotations

from typing import Optional

from .canonical_path import program_identity
from .mutative_verbs import (
    _COMMAND_WRAPPERS,
    _ENV_ASSIGN_PREFIX_RE,
    CATEGORY_MUTATIVE,
    MutativeResult,
    _consume_shell_word,
    _skip_wrapper_arguments,
)

PRIVILEGE_PROGRAMS = frozenset({"sudo", "su", "doas"})
PRIVILEGE_RULE = "sign.privilege"


def privilege_program(command: str) -> Optional[str]:
    """The privilege program *command* starts, looking past assignments and transparent wrappers, else None."""
    s, i = command.strip(), 0
    while i < len(s):
        while i < len(s) and s[i].isspace():
            i += 1
        assignment = _ENV_ASSIGN_PREFIX_RE.match(s, i)
        if assignment:
            i = _consume_shell_word(s, assignment.end())
            continue
        end = _consume_shell_word(s, i)
        name = program_identity(s[i:end].strip("\"'"))
        if name in PRIVILEGE_PROGRAMS:
            return name
        grammar = _COMMAND_WRAPPERS.get(name)
        wrapped = None if grammar is None else _skip_wrapper_arguments(s, end, grammar)
        if wrapped is None or wrapped <= i:
            return None
        i = wrapped
    return None


def privilege_verdict(command: str, detected) -> Optional[MutativeResult]:
    """The sign.privilege request for *command*, replacing the payload's rule while its floors still apply; None without sudo, su or doas."""
    program = privilege_program(command)
    if program is None:
        return None
    # A free payload's verb is a lane label, not a verb, and it would become the approval title.
    verb = detected.verb if detected.is_mutative and detected.verb else program
    return MutativeResult(
        is_mutative=True, category=CATEGORY_MUTATIVE, verb=verb,
        confidence="high", reason=f"{program} runs the command as another user", rule=PRIVILEGE_RULE,
    )
