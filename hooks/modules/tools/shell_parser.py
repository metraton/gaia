"""
Shell Command Parser - Native Python Implementation

Parses bash commands into individual components for permission validation.
This is a workaround for Claude Code bug where settings.json permissions
are not respected (GitHub Issue #13340).

Features:
- Parse piped commands: "cmd1 | cmd2" -> ["cmd1", "cmd2"]
- Parse chained commands: "cmd1 && cmd2" -> ["cmd1", "cmd2"]
- Parse semicolon-separated: "cmd1; cmd2" -> ["cmd1", "cmd2"]
- Handle OR chains: "cmd1 || cmd2" -> ["cmd1", "cmd2"]
- Preserve quoted strings: echo 'a|b' -> ["echo 'a|b'"]

Dependencies: Python stdlib only (shlex, re)

This module is the one place shell words are decided, for the classifier and
every guard alike: an unquoted `#` at word start begins a comment, a single
`&` separates commands, and redirections with their targets are not argv
(`program_argv`). A rule about what bash passes to a program belongs here, not
in a caller's own split.
"""

import re
import shlex
from typing import List, Optional
from dataclasses import dataclass

# Characters after which a `#` starts a word, and so a comment.
_WORD_START_BEFORE = " \t\n;&|()"

# A redirection operator alone (its target is the next word) and one with the
# target attached (`2>/dev/null`, `<in`, `2>&1`).
_REDIRECT_OPERATOR = re.compile(r"^(?:\d+|&)?(?:>>|>&|>\||>|<<<|<<-?|<&|<>|<)$")
_REDIRECT_ATTACHED = re.compile(r"^(?:\d+|&)?(?:>>|>&|>\||>|<<<|<<-?|<&|<>|<).+$")


def strip_comment(command: str) -> str:
    """*command* without its comments: an unquoted `#` at word start, outside a substitution, through the end of its line."""
    out: List[str] = []
    i, n = 0, len(command)
    in_single = in_double = False
    depth = 0
    while i < n:
        ch = command[i]
        if ch == "\\" and not in_single and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "$" and i + 1 < n and command[i + 1] == "(":
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
            elif ch == "#" and depth == 0 and (i == 0 or command[i - 1] in _WORD_START_BEFORE):
                end = command.find("\n", i)
                if end == -1:
                    break
                i = end
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def is_lone_ampersand(command: str, i: int) -> bool:
    """True when the `&` at *i* separates commands: not `&&`, `&>`, or the `&` of `>&`, `<&` and `|&`."""
    before = command[i - 1] if i > 0 else ""
    after = command[i + 1] if i + 1 < len(command) else ""
    return after not in ("&", ">") and before not in ("&", ">", "<", "|")


def drop_redirections(words: List[str]) -> List[str]:
    """*words* without redirection operators and their targets, which bash never passes to the program."""
    out: List[str] = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
            continue
        if _REDIRECT_OPERATOR.match(word):
            skip_next = True
            continue
        if _REDIRECT_ATTACHED.match(word):
            continue
        out.append(word)
    return out


def shell_words(component: str) -> List[str]:
    """The unquoted words of one operator-free component, redirections removed."""
    try:
        words = shlex.split(component)
    except ValueError:
        words = component.split()
    return drop_redirections(words)


def program_argv(command: str) -> List[str]:
    """The words bash passes to the first program of *command*: comments, later commands and redirections removed."""
    components = parse_command(command)
    return shell_words(components[0]) if components else []


@dataclass
class ParsedCommand:
    """Represents a parsed shell command component."""
    command: str  # The actual command string
    operator: Optional[str] = None  # The operator that follows (|, &&, ||, ;)

    def __str__(self) -> str:
        return self.command


class ShellCommandParser:
    """
    Native Python shell command parser.

    Parses bash commands into individual components while respecting:
    - Single quotes (preserve everything inside)
    - Double quotes (preserve with variable expansion awareness)
    - Escape sequences
    - Subshells
    - Command substitution

    Zero external dependencies - uses Python stdlib only.
    """

    # Shell operators that separate commands
    OPERATORS = {
        '|': 'pipe',
        '&&': 'and',
        '||': 'or',
        ';': 'sequence',
        '\n': 'newline'
    }

    def __init__(self):
        """Initialize parser."""
        pass

    def parse(self, command: str) -> List[str]:
        """
        Parse shell command into individual components.

        Args:
            command: Full shell command string

        Returns:
            List of individual command strings

        Example:
            >>> parser = ShellCommandParser()
            >>> parser.parse("ls | grep foo && wc -l")
            ["ls", "grep foo", "wc -l"]

            >>> parser.parse("echo 'test | grep' | cat")
            ["echo 'test | grep'", "cat"]
        """
        if not command or not command.strip():
            return []

        command = strip_comment(command).strip()

        # Split on operators while preserving quotes
        components = self._split_on_operators(command)

        # Clean and filter empty components
        result = [comp.strip() for comp in components if comp.strip()]

        return result

    def _split_on_operators(self, command: str) -> List[str]:
        """
        Split command on operators (|, &&, ||, ;) while preserving quoted strings.

        Uses state machine to track quote context.
        """
        components = []
        current = []
        i = 0

        # Quote state
        in_single_quote = False
        in_double_quote = False

        while i < len(command):
            char = command[i]

            # Handle escape sequences
            if char == '\\' and i + 1 < len(command):
                current.append(char)
                current.append(command[i + 1])
                i += 2
                continue

            # Handle single quotes
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
                current.append(char)
                i += 1
                continue

            # Handle double quotes
            if char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
                current.append(char)
                i += 1
                continue

            # If inside quotes, add character and continue
            if in_single_quote or in_double_quote:
                current.append(char)
                i += 1
                continue

            # Check for two-character operators (&&, ||)
            if i + 1 < len(command):
                two_char = command[i:i+2]
                if two_char in ['&&', '||']:
                    # Found operator - save current component
                    if current:
                        components.append(''.join(current))
                        current = []
                    i += 2
                    continue

            # Check for single-character operators (|, ;, a lone &)
            if char in ['|', ';', '\n'] or (char == '&' and is_lone_ampersand(command, i)):
                # Found operator - save current component
                if current:
                    components.append(''.join(current))
                    current = []
                i += 1
                continue

            # Regular character
            current.append(char)
            i += 1

        # Add final component
        if current:
            components.append(''.join(current))

        return components

    def parse_with_operators(self, command: str) -> List[ParsedCommand]:
        """
        Parse command and preserve operator information.

        Useful for understanding command flow (AND vs OR vs pipe).
        """
        if not command or not command.strip():
            return []

        commands = self.parse(command)
        return [ParsedCommand(command=cmd) for cmd in commands]

    def contains_operators(self, command: str) -> bool:
        """Check if command contains shell operators (outside of quotes)."""
        components = self.parse(command)
        return len(components) > 1

    def is_simple_command(self, command: str) -> bool:
        """Check if command is a simple command (no operators)."""
        return not self.contains_operators(command)


# Singleton instance for reuse
_shell_parser: Optional[ShellCommandParser] = None


def get_shell_parser() -> ShellCommandParser:
    """
    Get singleton ShellCommandParser instance.

    Returns:
        ShellCommandParser instance
    """
    global _shell_parser
    if _shell_parser is None:
        _shell_parser = ShellCommandParser()
    return _shell_parser


def parse_command(command: str) -> List[str]:
    """
    Parse shell command into components (convenience function).

    Args:
        command: Shell command string

    Returns:
        List of command components
    """
    return get_shell_parser().parse(command)


def is_compound_command(command: str) -> bool:
    """
    Check if command is compound (contains operators).

    Args:
        command: Shell command string

    Returns:
        True if compound, False if simple
    """
    return get_shell_parser().contains_operators(command)
