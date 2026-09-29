"""Recognize an interpreter whose PROGRAM arrives as a heredoc body.

``python3 - <<'PY'``, ``node -``, ``bash -s`` and ``sh -s`` read the code they
run from stdin, so the body is the program, not data a CLI stores. The
counterpart to ``data_heredoc``: there the body is never executed, here it is
the only thing that is.

The shape is deliberately the one ``data_heredoc`` accepts, with the receiver
inverted: a plain declaring line (no operator, redirect, quote, substitution
or variable), the opener last on it, and the terminator ending the command.
Anything else returns ``None`` and is classified as the shell reads it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

from .shell_substitution import _heredoc_body_span, _read_heredoc_opener

CODE_INTERPRETERS = frozenset({
    "python", "python3", "python3.10", "python3.11", "python3.12", "python3.13",
    "node", "ruby", "perl", "php",
})
SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_CODE_INLINE_FLAGS = frozenset({"-c", "-e", "-E", "--eval", "-p", "--print", "-r", "-m"})
_STDIN_SENTINEL = "-"
_SHELL_SYNTAX = frozenset("|&;<>()`$\\'\"")


@dataclass(frozen=True)
class HeredocProgram:
    interpreter: str
    body: str

    @property
    def is_shell(self) -> bool:
        return self.interpreter in SHELL_INTERPRETERS


def _reads_program_from_stdin(interpreter: str, args: List[str]) -> bool:
    """True when *args* leave stdin as the program source.

    A code interpreter takes no positional or a final ``-``; a shell takes no
    positional. Neither may carry an inline-code flag, whose program is its
    argument instead.
    """
    positionals = [arg for arg in args if not arg.startswith("-") or arg == _STDIN_SENTINEL]
    if interpreter in CODE_INTERPRETERS:
        if _CODE_INLINE_FLAGS.intersection(args):
            return False
        if not positionals:
            return True
        return positionals == [_STDIN_SENTINEL] and args[-1] == _STDIN_SENTINEL
    if interpreter in SHELL_INTERPRETERS:
        short_flags = "".join(arg[1:] for arg in args if not arg.startswith("--"))
        return not positionals and "c" not in short_flags
    return False


def heredoc_program(command: str) -> Optional[HeredocProgram]:
    """Return the interpreter and the program it reads from its heredoc, else None."""
    first_newline = command.find("\n")
    if first_newline == -1:
        return None
    declaring_line = command[:first_newline]
    opener_at = declaring_line.find("<<")
    if opener_at == -1:
        return None
    opener = _read_heredoc_opener(declaring_line, opener_at)
    if opener is None:
        return None
    delimiter, _expands, strip_tabs, after_word = opener
    if declaring_line[after_word:].strip():
        return None

    header = declaring_line[:opener_at].strip()
    if not header or _SHELL_SYNTAX.intersection(header):
        return None
    tokens = header.split()
    interpreter = os.path.basename(tokens[0])
    if not _reads_program_from_stdin(interpreter, tokens[1:]):
        return None

    body_start, resume, terminated = _heredoc_body_span(
        command, first_newline + 1, delimiter, strip_tabs,
    )
    if not terminated or command[resume:].strip():
        return None
    body_lines = command[body_start:resume].rstrip("\n").split("\n")[:-1]
    return HeredocProgram(interpreter=interpreter, body="\n".join(body_lines))
