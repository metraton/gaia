"""Recognize a quoted heredoc whose body a Gaia CLI call reads as DATA.

``gaia plan save --content-file=- <<'PLAN'`` hands the plan to the CLI on stdin;
the shell never executes a byte of it. The operator splitter has no heredoc
model, so without this every prose line became a compound component and one
mutative-looking word denied the whole save.

The exemption is deliberately narrow, and each condition below is what keeps
it from reaching text the shell would run:

- the delimiter is QUOTED, so the body cannot expand ``$(...)`` or backticks;
- the receiver is ``gaia`` and a ``--<name>-file`` flag takes ``-``, which is how
  the plan/task/brief/ac/memory commands read content from stdin. Interpreters
  (``bash``, ``python3 -``, ``ssh``, ``kubectl exec``) are never receivers;
- the declaring line is one plain command: no operator, redirect, quote,
  substitution or variable around it, so the heredoc is that command's stdin
  and nothing else's;
- the terminator ends the command, so no trailing text escapes the analysis.

Other stdin readers were left out on purpose. ``gh ... --body-file -`` is
T3, and consent must be granted over bytes that include the body. ``cat >
file`` is a shell-authored write that belongs to the Write tool.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from .shell_substitution import _heredoc_body_span, _read_heredoc_opener

_DATA_RECEIVERS = frozenset({"gaia"})
_STDIN_CONTENT_FLAG = re.compile(r"--[a-z][a-z0-9-]*-file")
_SHELL_SYNTAX = frozenset("|&;<>()`$\\'\"")


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
    if not header or _SHELL_SYNTAX.intersection(header):
        return None
    tokens = header.split()
    if os.path.basename(tokens[0]) not in _DATA_RECEIVERS:
        return None
    if not _reads_content_from_stdin(tokens):
        return None

    _body_start, resume, terminated = _heredoc_body_span(
        command, first_newline + 1, delimiter, strip_tabs,
    )
    if not terminated or command[resume:].strip():
        return None
    return header
