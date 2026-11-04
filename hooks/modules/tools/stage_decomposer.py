"""
Stage Decomposer - Shell command stage tracking with operator preservation.

Splits a command into stages and keeps the operator between them.
ShellCommandParser.parse() discards operators; StageDecomposer preserves them
so downstream classifiers know how stages are connected (pipe vs AND vs OR).

A "stage" is one command with its arguments plus the operator that links it
to the next stage.  The last stage has operator=None.

Handles:
- Pipes (|), semicolons (;), AND (&&), OR (||)
- Command substitution $(...)
- Backtick substitution `...`
- Nested command substitution

Dependencies: Python stdlib, ``shell_parser`` and ``security.shell_substitution``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .shell_parser import ShellCommandParser, is_lone_ampersand, strip_comment
from ..security.shell_substitution import extract_substitutions, mask_quoted_text

_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


@dataclass
class Stage:
    """A single command stage in a pipeline or chain."""

    command: str
    args: List[str] = field(default_factory=list)
    operator: Optional[str] = None

    def __str__(self) -> str:
        return self.command

    @property
    def executable(self) -> str:
        """Return the executable name (first token of the command)."""
        tokens = self.command.strip().split()
        return tokens[0] if tokens else ""


@dataclass
class DecomposedCommand:
    """Result of decomposing a raw command string into stages."""

    # The command as decomposed: its comments stripped
    raw: str
    stages: List[Stage] = field(default_factory=list)
    substitutions: List[str] = field(default_factory=list)

    @property
    def executables(self) -> List[str]:
        """Return the list of executables across all stages."""
        return [s.executable for s in self.stages if s.executable]


class StageDecomposer:
    """
    Decomposes a raw shell command string into ordered Stage objects.

    Walks the string itself, quote-aware, to recover the operators that
    ShellCommandParser.parse() discards.

    Usage::

        decomposer = StageDecomposer()
        result = decomposer.decompose("ls | grep foo && wc -l")
        # result.stages[0].command  == "ls"
        # result.stages[0].operator == "|"
        # result.stages[1].command  == "grep foo"
        # result.stages[1].operator == "&&"
        # result.stages[2].command  == "wc -l"
        # result.stages[2].operator is None
    """

    def __init__(self) -> None:
        self._parser = ShellCommandParser()

    def decompose(self, command: str) -> DecomposedCommand:
        """
        Decompose a raw command string into Stage objects.

        Args:
            command: Raw shell command string.

        Returns:
            DecomposedCommand with stages and any extracted substitutions.
        """
        if not command or not command.strip():
            return DecomposedCommand(raw=command or "", stages=[], substitutions=[])

        command = strip_comment(command).strip()
        if not command:
            return DecomposedCommand(raw="", stages=[], substitutions=[])

        substitutions = self._extract_substitutions(command)
        pairs = self._split_with_operators(command)

        stages: List[Stage] = []
        for cmd_text, op in pairs:
            cmd_text = cmd_text.strip()
            if not cmd_text:
                continue
            args = self._tokenize_args(cmd_text)
            stages.append(Stage(command=cmd_text, args=args, operator=op))

        return DecomposedCommand(raw=command, stages=stages, substitutions=substitutions)

    def pipe_feeders(self, command: str, components: List[str]) -> List[Optional[str]]:
        """Per component, the stage that alone starts the pipe into it; all None when *command*'s stages differ from *components*."""
        stages = self.decompose(command).stages
        feeders: List[Optional[str]] = [None] * len(components)
        if [stage.command for stage in stages] != [component.strip() for component in components]:
            return feeders
        for index in range(1, len(stages)):
            starts_pipe = index == 1 or stages[index - 2].operator != "|"
            if stages[index - 1].operator == "|" and starts_pipe:
                feeders[index] = stages[index - 1].command
        return feeders

    def _split_with_operators(self, command: str) -> List[tuple]:
        """
        Walk *command* and return a list of (segment, operator_or_None) tuples.

        The final segment always has operator=None.
        Quotes and escape sequences are respected so operators inside quoted
        strings or $(...) subshells are not treated as segment boundaries.
        """
        segments: List[tuple] = []
        current: List[str] = []
        i = 0
        n = len(command)

        in_single_quote = False
        in_double_quote = False
        paren_depth = 0
        backtick_depth = 0

        while i < n:
            ch = command[i]

            if ch == "\\" and not in_single_quote and i + 1 < n:
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue

            if ch == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(ch)
                i += 1
                continue

            if ch == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(ch)
                i += 1
                continue

            if in_single_quote or in_double_quote:
                current.append(ch)
                i += 1
                continue

            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                paren_depth += 1
                current.append(ch)
                current.append("(")
                i += 2
                continue

            if ch == "(" and paren_depth > 0:
                paren_depth += 1
                current.append(ch)
                i += 1
                continue

            if ch == ")" and paren_depth > 0:
                paren_depth -= 1
                current.append(ch)
                i += 1
                continue

            if ch == "`":
                if backtick_depth > 0:
                    backtick_depth -= 1
                else:
                    backtick_depth += 1
                current.append(ch)
                i += 1
                continue

            if paren_depth > 0 or backtick_depth > 0:
                current.append(ch)
                i += 1
                continue

            if i + 1 < n:
                two = command[i : i + 2]
                if two in ("&&", "||"):
                    segments.append(("".join(current), two))
                    current = []
                    i += 2
                    continue

            if ch in ("|", ";", "\n") or (ch == "&" and is_lone_ampersand(command, i)):
                segments.append(("".join(current), ch))
                current = []
                i += 1
                continue

            current.append(ch)
            i += 1

        if current:
            segments.append(("".join(current), None))

        return segments

    def _tokenize_args(self, command_text: str) -> List[str]:
        """
        Split *command_text* into tokens (command + args).

        The words are the ones bash runs: inside double quotes a backslash
        escapes only ``$``, a backtick, ``"``, ``\\`` and a newline, so the
        quoted Windows path ``"C:\\Users\\x"`` keeps its separators.

        Returns a list where element 0 is the executable and the remainder
        are arguments.
        """
        tokens: List[str] = []
        current: List[str] = []
        i = 0
        n = len(command_text)
        in_single = False
        in_double = False

        while i < n:
            ch = command_text[i]

            if ch == "\\" and in_double and i + 1 < n and command_text[i + 1] not in _DOUBLE_QUOTE_ESCAPABLE:
                current.append(ch)
                i += 1
                continue

            if ch == "\\" and not in_single and i + 1 < n:
                current.append(command_text[i + 1])
                i += 2
                continue

            if ch == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue

            if ch == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue

            if not in_single and not in_double and ch in (" ", "\t"):
                if current:
                    tokens.append("".join(current))
                    current = []
                i += 1
                continue

            current.append(ch)
            i += 1

        if current:
            tokens.append("".join(current))

        return tokens

    # $(...) holding at most one level of nested parentheses, and `...` with no nesting.
    _SUBST_PAREN_RE = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
    _SUBST_BACKTICK_RE = re.compile(r"`([^`]*)`")

    def _extract_substitutions(self, command: str) -> List[str]:
        """Return the inner strings of the $(...) and `...` substitutions bash would run.

        Quoting decides what runs, so a balanced command is read by the
        quote-aware extractor. A command with a quote left open or an ANSI-C
        ``$'...'`` span keeps the quote-blind patterns below, which report every
        opener they see.
        """
        if "$'" not in command and mask_quoted_text(command) is not None:
            return extract_substitutions(command)
        results: List[str] = []
        results.extend(m.group(1).strip() for m in self._SUBST_PAREN_RE.finditer(command))
        results.extend(m.group(1).strip() for m in self._SUBST_BACKTICK_RE.finditer(command))
        return results
