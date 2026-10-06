"""
Shell Unwrapper - Detect and strip wrapper shells from commands.

Many commands arrive wrapped in a shell invocation:
    bash -c "actual command"
    sh -c 'rm -rf /tmp/build'
    env bash -c "sh -c 'inner command'"

The wrapped inner command is what matters for classification, not the
wrapper itself.  ShellUnwrapper recursively peels wrapper shells until
it reaches the actual payload.

A command is a wrapper when, after leading ``NAME=value`` assignments and
transparent wrappers (``env``, ``sudo``, ``timeout``, ``exec``, ...), an
sh-family shell carries ``c`` in a short-flag cluster (``-c``, ``-lc``,
``-e -c``) or fish's ``--command``; the payload is its first operand.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Optional

from .mutative_verbs import (
    _WrapperGrammar,
    _consume_shell_word,
    _peel_leading_command_wrappers,
    _skip_wrapper_arguments,
)


@dataclass
class UnwrapResult:
    """Result of unwrapping a shell command."""

    # The innermost command after all wrappers are stripped.
    inner: str
    # Number of wrapper layers removed (0 = no wrapper detected).
    depth: int
    # True if any wrapper was detected and stripped.
    was_wrapped: bool

    def __str__(self) -> str:
        return self.inner


# Launchers that run a shell unchanged but are absent from the shared
# _COMMAND_WRAPPERS table; kept here so recognising them widens wrapper
# detection without widening every other consumer of that table.
_SHELL_LAUNCHERS = {
    "exec": _WrapperGrammar(value_flags=frozenset({"-a"})),
    "setsid": _WrapperGrammar(),
    "strace": _WrapperGrammar(value_flags=frozenset({"-e", "-o", "-p", "-s", "-u", "-E"})),
    "ltrace": _WrapperGrammar(value_flags=frozenset({"-e", "-o", "-p", "-s", "-u"})),
}

_SHELLS = frozenset({
    "sh", "bash", "dash", "ash", "ksh", "mksh", "zsh", "csh", "tcsh", "fish",
})

_LONG_VALUE_OPTIONS = frozenset({"--rcfile", "--init-file", "--init-command"})

# Maximum recursion depth to prevent infinite loops on pathological input.
_MAX_DEPTH = 10


def _peel_launchers(command: str) -> str:
    """Return *command* without its leading assignments, wrappers and launchers."""
    s = command.strip()
    while True:
        s, _ = _peel_leading_command_wrappers(s)
        word_end = _consume_shell_word(s, 0)
        grammar = _SHELL_LAUNCHERS.get(s[:word_end].rsplit("/", 1)[-1])
        if grammar is None:
            return s
        s = s[_skip_wrapper_arguments(s, word_end, grammar):]


def _next_word_end(s: str, i: int) -> int:
    """Return the index just past the shell word following ``s[i]``'s whitespace."""
    while i < len(s) and s[i].isspace():
        i += 1
    return _consume_shell_word(s, i)


def _unquote_payload(word: str, rest: str) -> str:
    """Return the quoted *word* unquoted, or the unquoted *rest* of the line."""
    if word[:1] in ("'", '"'):
        try:
            parts = shlex.split(word)
        except ValueError:
            parts = []
        if len(parts) == 1:
            return parts[0].strip()
    return rest.strip()


def shell_command_string(command: str) -> Optional[str]:
    """Return the string *command* hands a shell to run as its program, else None.

    Options are read the way a shell reads them: ``-o``/``-O`` clusters and
    the long value options consume the next word, and ``-``/``--`` end them.
    """
    s = _peel_launchers(command)
    word_end = _consume_shell_word(s, 0)
    if s[:word_end].rsplit("/", 1)[-1] not in _SHELLS:
        return None

    runs_string = False
    options_open = True
    i = word_end
    while True:
        while i < len(s) and s[i].isspace():
            i += 1
        if i >= len(s):
            return None
        start = i
        i = _consume_shell_word(s, i)
        word = s[start:i]
        if not options_open or word[:1] not in ("-", "+"):
            break
        if word in ("-", "--"):
            options_open = False
        elif word.startswith("--"):
            name, has_value, value = word.partition("=")
            if name == "--command":
                if has_value:
                    return _unquote_payload(value, value)
                runs_string = True
            elif name in _LONG_VALUE_OPTIONS and not has_value:
                i = _next_word_end(s, i)
        else:
            letters = word[1:]
            if word[0] == "-" and "c" in letters:
                runs_string = True
            if "o" in letters or "O" in letters:
                i = _next_word_end(s, i)

    if not runs_string:
        return None
    return _unquote_payload(word, s[start:])


class ShellUnwrapper:
    """Detect and recursively strip shell wrapper invocations."""

    def unwrap(self, command: str) -> UnwrapResult:
        """
        Recursively strip shell wrappers from *command*.

        Args:
            command: Raw shell command string.

        Returns:
            UnwrapResult with the innermost command, depth, and whether
            any wrapper was detected.
        """
        if not command or not command.strip():
            return UnwrapResult(inner=command or "", depth=0, was_wrapped=False)

        current = command.strip()
        depth = 0

        while depth < _MAX_DEPTH:
            inner = shell_command_string(current)
            if inner is None:
                break
            current = inner.strip()
            depth += 1

        return UnwrapResult(
            inner=current,
            depth=depth,
            was_wrapped=depth > 0,
        )

    def is_wrapped(self, command: str) -> bool:
        """Return True if *command* has a shell wrapper layer."""
        if not command or not command.strip():
            return False
        return shell_command_string(command.strip()) is not None
