"""Recognize a quoted heredoc whose body a Gaia CLI call reads as DATA.

``gaia plan save --content-file=- <<'PLAN'`` hands the plan to the CLI on stdin;
the shell never executes a byte of it. The operator splitter has no heredoc
model, so without this every prose line became a compound component and one
mutative-looking word denied the whole save.

The exemption is deliberately narrow, and each condition below is what keeps
it from reaching text the shell would run:

- the delimiter is QUOTED, so the body cannot expand ``$(...)`` or backticks;
- the receiver is ``gaia`` and a ``--<name>-file`` flag takes ``-``, which is how
  the plan/task/brief/ac/memory commands read content from stdin -- or a bare
  ``cat`` with no argument, which only echoes the body (the
  ``"$(cat <<'EOF' ... EOF)"`` idiom that passes a value to a CLI). Interpreters
  (``bash``, ``python3 -``, ``ssh``, ``kubectl exec``) are never receivers;
- the declaring line is one plain command: no unquoted operator, redirect,
  substitution or variable, so the heredoc is that command's stdin and nothing
  else's. A quoted argument (``--reason "..."``, which every plan rewrite
  carries) is allowed only where the shell keeps it literal: single quotes
  as-is, double quotes only with no ``$``, backtick or backslash inside. The
  receiver and the ``--<name>-file -`` flag must be unquoted words, so a quoted
  value that merely spells the flag does not count as one;
- the terminator ends the command, so no trailing text escapes the analysis.

Other stdin readers were left out on purpose. ``gh ... --body-file -`` is
T3, and consent must be granted over bytes that include the body. ``cat >
file`` is a shell-authored write that belongs to the Write tool.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from .shell_substitution import _heredoc_body_span, _read_heredoc_opener

_DATA_RECEIVERS = frozenset({"gaia"})
_ECHO_RECEIVER = [("cat", False)]
_STDIN_CONTENT_FLAG = re.compile(r"--[a-z][a-z0-9-]*-file")
_UNQUOTED_SHELL_SYNTAX = frozenset("|&;<>()`$\\")
_DOUBLE_QUOTE_EXPANSION = frozenset("$`\\")


def _literal_words(header: str) -> Optional[List[Tuple[str, bool]]]:
    """Split a header into ``(value, quoted)`` words; None if the shell would expand or chain it."""
    words: List[Tuple[str, bool]] = []
    parts: List[str] = []
    quoted = False
    index = 0
    while index < len(header):
        char = header[index]
        if char in " \t":
            if parts:
                words.append(("".join(parts), quoted))
                parts, quoted = [], False
            index += 1
            continue
        if char in "'\"":
            closing = header.find(char, index + 1)
            if closing == -1:
                return None
            inner = header[index + 1:closing]
            if char == '"' and _DOUBLE_QUOTE_EXPANSION.intersection(inner):
                return None
            parts.append(inner)
            quoted = True
            index = closing + 1
            continue
        if char in _UNQUOTED_SHELL_SYNTAX:
            return None
        parts.append(char)
        index += 1
    if parts:
        words.append(("".join(parts), quoted))
    return words


def _reads_content_from_stdin(tokens: List[str]) -> bool:
    for index, token in enumerate(tokens):
        name, equals, value = token.partition("=")
        if not _STDIN_CONTENT_FLAG.fullmatch(name):
            continue
        if equals and value == "-":
            return True
        if not equals and tokens[index + 1:index + 2] == ["-"]:
            return True
    return False


def data_heredoc_header(command: str) -> Optional[str]:
    """Return the command without its heredoc when the body is pure data, else None."""
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
    delimiter, expands, strip_tabs, after_word = opener
    if expands or declaring_line[after_word:].strip():
        return None

    header = declaring_line[:opener_at].strip()
    words = _literal_words(header)
    if not words:
        return None
    if words != _ECHO_RECEIVER:
        receiver, receiver_quoted = words[0]
        if receiver_quoted or os.path.basename(receiver) not in _DATA_RECEIVERS:
            return None
        unquoted = [value if not quoted else "" for value, quoted in words]
        if not _reads_content_from_stdin(unquoted):
            return None

    _body_start, resume, terminated = _heredoc_body_span(
        command, first_newline + 1, delimiter, strip_tabs,
    )
    if not terminated or command[resume:].strip():
        return None
    return header
