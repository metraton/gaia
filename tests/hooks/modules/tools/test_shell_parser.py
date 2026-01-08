#!/usr/bin/env python3
"""
Tests for Shell Command Parser.

Validates:
1. ShellCommandParser class
2. Operator-based splitting
3. Quote preservation
4. Convenience functions
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.shell_parser import (
    ShellCommandParser,
    ParsedCommand,
    get_shell_parser,
    parse_command,
    is_compound_command,
)


class TestShellCommandParser:
    """Test ShellCommandParser class."""

    @pytest.fixture
    def parser(self):
        return ShellCommandParser()

    # Simple commands
    def test_parses_simple_command(self, parser):
        """Test parsing simple command."""
        result = parser.parse("ls -la")
        assert result == ["ls -la"]

    def test_parses_single_word(self, parser):
        """Test parsing single word command."""
        result = parser.parse("pwd")
        assert result == ["pwd"]

    # Pipe commands
    def test_parses_pipe(self, parser):
        """Test parsing piped commands."""
        result = parser.parse("ls | grep foo")
        assert result == ["ls", "grep foo"]

    def test_parses_multiple_pipes(self, parser):
        """Test parsing multiple pipes."""
        result = parser.parse("cat file | grep pattern | wc -l")
        assert result == ["cat file", "grep pattern", "wc -l"]

    # AND chains
    def test_parses_and_chain(self, parser):
        """Test parsing AND chain."""
        result = parser.parse("mkdir dir && cd dir")
        assert result == ["mkdir dir", "cd dir"]

    # OR chains
    def test_parses_or_chain(self, parser):
        """Test parsing OR chain."""
        result = parser.parse("test -f file || echo missing")
        assert result == ["test -f file", "echo missing"]

    # Semicolon separator
    def test_parses_semicolon(self, parser):
        """Test parsing semicolon-separated commands."""
        result = parser.parse("ls; pwd; whoami")
        assert result == ["ls", "pwd", "whoami"]

    # Mixed operators
    def test_parses_mixed_operators(self, parser):
        """Test parsing mixed operators."""
        result = parser.parse("ls | grep foo && wc -l")
        assert len(result) == 3

    # Quote preservation
    def test_preserves_single_quotes(self, parser):
        """Test preserves content in single quotes."""
        result = parser.parse("echo 'test | with pipe'")
        assert len(result) == 1
        assert "test | with pipe" in result[0]

    def test_preserves_double_quotes(self, parser):
        """Test preserves content in double quotes."""
        result = parser.parse('echo "test && with and"')
        assert len(result) == 1
        assert "test && with and" in result[0]

    def test_mixed_quoted_and_operator(self, parser):
        """Test command with both quoted and unquoted operators."""
        result = parser.parse("echo 'hello|world' | cat")
        assert len(result) == 2
        assert "hello|world" in result[0]

    # Escape handling
    def test_handles_escaped_characters(self, parser):
        """Test handles escaped characters."""
        result = parser.parse("echo test\\|pipe")
        # Escaped pipe should not split
        assert len(result) == 1

    # Empty/edge cases
    def test_handles_empty_string(self, parser):
        """Test handles empty string."""
        result = parser.parse("")
        assert result == []

    def test_handles_whitespace(self, parser):
        """Test handles whitespace-only string."""
        result = parser.parse("   ")
        assert result == []

    def test_strips_whitespace(self, parser):
        """Test strips leading/trailing whitespace."""
        result = parser.parse("  ls -la  ")
        assert result == ["ls -la"]


class TestParsedCommand:
    """Test ParsedCommand dataclass."""

    def test_creates_parsed_command(self):
        """Test creating ParsedCommand."""
        pc = ParsedCommand(command="ls -la")
        assert pc.command == "ls -la"
        assert pc.operator is None

    def test_str_representation(self):
        """Test string representation."""
        pc = ParsedCommand(command="grep pattern")
        assert str(pc) == "grep pattern"

    def test_with_operator(self):
        """Test with operator set."""
        pc = ParsedCommand(command="ls", operator="|")
        assert pc.command == "ls"
        assert pc.operator == "|"


class TestParseWithOperators:
    """Test parse_with_operators method."""

    @pytest.fixture
    def parser(self):
        return ShellCommandParser()

    def test_returns_parsed_commands(self, parser):
        """Test returns list of ParsedCommand objects."""
        result = parser.parse_with_operators("ls | grep foo")
        assert len(result) == 2
        assert all(isinstance(pc, ParsedCommand) for pc in result)

    def test_empty_returns_empty_list(self, parser):
        """Test empty input returns empty list."""
        result = parser.parse_with_operators("")
        assert result == []


class TestContainsOperators:
    """Test contains_operators method."""

    @pytest.fixture
    def parser(self):
        return ShellCommandParser()

    def test_detects_pipe(self, parser):
        """Test detects pipe operator."""
        assert parser.contains_operators("ls | grep") is True

    def test_detects_and(self, parser):
        """Test detects AND operator."""
        assert parser.contains_operators("cmd1 && cmd2") is True

    def test_detects_or(self, parser):
        """Test detects OR operator."""
        assert parser.contains_operators("cmd1 || cmd2") is True

    def test_detects_semicolon(self, parser):
        """Test detects semicolon."""
        assert parser.contains_operators("cmd1; cmd2") is True

    def test_no_operators(self, parser):
        """Test returns False for simple command."""
        assert parser.contains_operators("ls -la") is False

    def test_quoted_operators_not_detected(self, parser):
        """Test quoted operators are not detected."""
        assert parser.contains_operators("echo 'a|b'") is False


class TestIsSimpleCommand:
    """Test is_simple_command method."""

    @pytest.fixture
    def parser(self):
        return ShellCommandParser()

    def test_simple_command(self, parser):
        """Test identifies simple command."""
        assert parser.is_simple_command("ls -la") is True

    def test_compound_command(self, parser):
        """Test identifies compound command."""
        assert parser.is_simple_command("ls | grep foo") is False


class TestGetShellParser:
    """Test get_shell_parser singleton."""

    def test_returns_parser(self):
        """Test returns ShellCommandParser instance."""
        parser = get_shell_parser()
        assert isinstance(parser, ShellCommandParser)

    def test_returns_same_instance(self):
        """Test returns same instance (singleton)."""
        parser1 = get_shell_parser()
        parser2 = get_shell_parser()
        assert parser1 is parser2


class TestParseCommandFunction:
    """Test parse_command convenience function."""

    def test_parses_command(self):
        """Test convenience function works."""
        result = parse_command("ls | grep foo")
        assert result == ["ls", "grep foo"]

    def test_handles_empty(self):
        """Test handles empty string."""
        result = parse_command("")
        assert result == []


class TestIsCompoundCommandFunction:
    """Test is_compound_command convenience function."""

    def test_detects_compound(self):
        """Test detects compound command."""
        assert is_compound_command("ls && pwd") is True

    def test_detects_simple(self):
        """Test detects simple command."""
        assert is_compound_command("ls -la") is False


class TestEdgeCases:
    """Test edge cases in shell parsing."""

    @pytest.fixture
    def parser(self):
        return ShellCommandParser()

    def test_nested_quotes(self, parser):
        """Test handles nested quotes."""
        # Single inside double
        result = parser.parse('echo "test\'s value"')
        assert len(result) == 1

    def test_unclosed_quote(self, parser):
        """Test handles unclosed quote."""
        # Implementation-dependent behavior
        result = parser.parse("echo 'unclosed")
        # Should not crash
        assert isinstance(result, list)

    def test_newline_as_separator(self, parser):
        """Test newline acts as separator."""
        result = parser.parse("ls\npwd")
        assert len(result) == 2

    def test_multiple_spaces(self, parser):
        """Test handles multiple spaces."""
        result = parser.parse("ls    -la")
        assert len(result) == 1
        # Preserves command (may or may not normalize spaces)

    def test_heredoc_style_not_split(self, parser):
        """Test heredoc-style content is handled."""
        # Complex heredoc handling depends on implementation
        result = parser.parse("cat << 'EOF'\ntest|pipe\nEOF")
        # Should not crash
        assert isinstance(result, list)
