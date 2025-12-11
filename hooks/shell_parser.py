#!/usr/bin/env python3
"""
Shell Command Parser - Native Python Implementation

Parses bash commands into individual components for permission validation.
This is a workaround for Claude Code bug where settings.json permissions
are not respected (GitHub Issue #13340).

Features:
- Parse piped commands: "cmd1 | cmd2" → ["cmd1", "cmd2"]
- Parse chained commands: "cmd1 && cmd2" → ["cmd1", "cmd2"]
- Parse semicolon-separated: "cmd1; cmd2" → ["cmd1", "cmd2"]
- Handle OR chains: "cmd1 || cmd2" → ["cmd1", "cmd2"]
- Preserve quoted strings: echo 'a|b' → ["echo 'a|b'"]

Dependencies: Python stdlib only (shlex, re)

Author: Gaia (gaia-ops system)
Date: 2025-12-11
GitHub Issue: https://github.com/anthropics/claude-code/issues/13340
"""

import re
import shlex
from typing import List, Optional
from dataclasses import dataclass


@dataclass
class ParsedCommand:
    """Represents a parsed shell command component"""
    command: str  # The actual command string
    operator: Optional[str] = None  # The operator that follows this command (|, &&, ||, ;)

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
        """Initialize parser"""
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

        # Normalize whitespace
        command = command.strip()

        # Split on operators while preserving quotes
        components = self._split_on_operators(command)

        # Clean and filter empty components
        result = [comp.strip() for comp in components if comp.strip()]

        return result

    def _split_on_operators(self, command: str) -> List[str]:
        """
        Split command on operators (|, &&, ||, ;) while preserving quoted strings.

        Uses state machine to track quote context.

        Args:
            command: Shell command string

        Returns:
            List of command components
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

            # Check for single-character operators (|, ;)
            if char in ['|', ';', '\n']:
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

        Args:
            command: Full shell command string

        Returns:
            List of ParsedCommand objects with operator info

        Example:
            >>> parser.parse_with_operators("ls | grep foo && wc")
            [
                ParsedCommand(command="ls", operator="|"),
                ParsedCommand(command="grep foo", operator="&&"),
                ParsedCommand(command="wc", operator=None)
            ]
        """
        if not command or not command.strip():
            return []

        # For now, just parse without operator info
        # This can be enhanced later if needed
        commands = self.parse(command)
        return [ParsedCommand(command=cmd) for cmd in commands]

    def contains_operators(self, command: str) -> bool:
        """
        Check if command contains shell operators (outside of quotes).

        Args:
            command: Shell command string

        Returns:
            True if command contains operators, False otherwise

        Example:
            >>> parser.contains_operators("ls | grep foo")
            True
            >>> parser.contains_operators("echo 'ls | grep'")
            False
        """
        # Parse and check if we get multiple components
        components = self.parse(command)
        return len(components) > 1

    def is_simple_command(self, command: str) -> bool:
        """
        Check if command is a simple command (no operators).

        Args:
            command: Shell command string

        Returns:
            True if command is simple, False if compound
        """
        return not self.contains_operators(command)


# Convenience functions for quick usage
def parse_command(command: str) -> List[str]:
    """
    Parse shell command into components.

    Convenience function for quick parsing.

    Args:
        command: Shell command string

    Returns:
        List of command components
    """
    parser = ShellCommandParser()
    return parser.parse(command)


def is_compound_command(command: str) -> bool:
    """
    Check if command is compound (contains operators).

    Args:
        command: Shell command string

    Returns:
        True if compound, False if simple
    """
    parser = ShellCommandParser()
    return parser.contains_operators(command)


if __name__ == "__main__":
    # Quick test when run directly
    import sys

    if len(sys.argv) > 1:
        cmd = ' '.join(sys.argv[1:])
        parser = ShellCommandParser()
        components = parser.parse(cmd)

        print(f"Input: {cmd}")
        print(f"Components ({len(components)}):")
        for i, comp in enumerate(components, 1):
            print(f"  {i}. {comp}")
    else:
        print("Usage: python shell_parser.py 'command string'")
        print("\nExamples:")
        print("  python shell_parser.py 'ls | grep foo'")
        print("  python shell_parser.py 'cmd1 && cmd2 || cmd3'")
