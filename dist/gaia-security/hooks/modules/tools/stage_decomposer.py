"""
Stage Decomposer - Shell command stage tracking with operator preservation.

Wraps ShellCommandParser to add operator tracking between stages.
ShellCommandParser.parse() discards operators; StageDecomposer preserves them
so downstream classifiers know how stages are connected (pipe vs AND vs OR).

A "stage" is one command with its arguments plus the operator that links it
to the next stage.  The last stage has operator=None.

Handles:
- Pipes (|), semicolons (;), AND (&&), OR (||)
- Command substitution $(...)
- Backtick substitution `...`
- Nested command substitution

Dependencies: Python stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .shell_parser import ShellCommandParser


@dataclass
class Stage:
    """A single command stage in a pipeline or chain."""

    # The raw command token (e.g., "grep -r foo")
    command: str
    # Arguments as a list parsed from the command token
    args: List[str] = field(default_factory=list)
    # Operator connecting THIS stage to the NEXT stage (None for the last stage)
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

    # Original command string (before decomposition)
    raw: str
    # Ordered list of stages
    stages: List[Stage] = field(default_factory=list)
    # Command substitutions extracted from the command ($(...) or `...`)
    substitutions: List[str] = field(default_factory=list)

    @property
    def is_compound(self) -> bool:
        """Return True if the command has more than one stage."""
        return len(self.stages) > 1

    @property
    def executables(self) -> List[str]:
        """Return the list of executables across all stages."""
        return [s.executable for s in self.stages if s.executable]


class StageDecomposer:
    """
    Decomposes a raw shell command string into ordered Stage objects.

    Wraps ShellCommandParser for quote-aware splitting, then re-walks the
    original string to recover the operators that ShellCommandParser.parse()
    discards.

    Zero external dependencies -- Python stdlib only.

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        command = command.strip()

        # Extract command substitutions before splitting so they don't
        # interfere with operator detection.
        substitutions = self._extract_substitutions(command)

        # Walk the command string once to collect (command_text, operator) pairs.
        pairs = self._split_with_operators(command)

        stages: List[Stage] = []
        for cmd_text, op in pairs:
            cmd_text = cmd_text.strip()
            if not cmd_text:
                continue
            args = self._tokenize_args(cmd_text)
            stages.append(Stage(command=cmd_text, args=args, operator=op))

        return DecomposedCommand(raw=command, stages=stages, substitutions=substitutions)

    # ------------------------------------------------------------------
    # Internal: operator-aware splitting
    # ------------------------------------------------------------------

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
        paren_depth = 0       # tracks $( ... ) nesting
        backtick_depth = 0    # tracks ` ... ` nesting

        while i < n:
            ch = command[i]

            # ---- escape sequence (outside single-quotes) ----
            if ch == "\\" and not in_single_quote and i + 1 < n:
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue

            # ---- single quote toggle ----
            if ch == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(ch)
                i += 1
                continue

            # ---- double quote toggle ----
            if ch == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(ch)
                i += 1
                continue

            # ---- inside quotes: pass through ----
            if in_single_quote or in_double_quote:
                current.append(ch)
                i += 1
                continue

            # ---- $( ... ) command substitution ----
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

            # ---- backtick command substitution ----
            if ch == "`":
                if backtick_depth > 0:
                    backtick_depth -= 1
                else:
                    backtick_depth += 1
                current.append(ch)
                i += 1
                continue

            # ---- if inside a substitution, pass through ----
            if paren_depth > 0 or backtick_depth > 0:
                current.append(ch)
                i += 1
                continue

            # ---- two-character operators: &&, || ----
            if i + 1 < n:
                two = command[i : i + 2]
                if two in ("&&", "||"):
                    segments.append(("".join(current), two))
                    current = []
                    i += 2
                    continue

            # ---- single-character operators: |, ;, \n ----
            if ch in ("|", ";", "\n"):
                segments.append(("".join(current), ch))
                current = []
                i += 1
                continue

            current.append(ch)
            i += 1

        # Final segment has no following operator.
        if current:
            segments.append(("".join(current), None))

        return segments

    # ------------------------------------------------------------------
    # Internal: argument tokenisation
    # ------------------------------------------------------------------

    def _tokenize_args(self, command_text: str) -> List[str]:
        """
        Split *command_text* into tokens (command + args).

        Uses a simple quote-aware tokeniser -- does NOT invoke shlex so we
        stay dependency-free and avoid locale issues.

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

    # ------------------------------------------------------------------
    # Internal: command substitution extraction
    # ------------------------------------------------------------------

    # Match $(...) -- does not handle arbitrarily deep nesting but covers
    # the common single-level case.
    _SUBST_PAREN_RE = re.compile(r"\$\(([^()]*(?:\([^()]*\)[^()]*)*)\)")
    # Match `...` (non-greedy, no nesting)
    _SUBST_BACKTICK_RE = re.compile(r"`([^`]*)`")

    def _extract_substitutions(self, command: str) -> List[str]:
        """Return a list of inner strings from $(...) and `...` substitutions."""
        results: List[str] = []
        results.extend(m.group(1).strip() for m in self._SUBST_PAREN_RE.finditer(command))
        results.extend(m.group(1).strip() for m in self._SUBST_BACKTICK_RE.finditer(command))
        return results
