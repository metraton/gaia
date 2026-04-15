#!/usr/bin/env python3
"""
Tests for Stage Decomposer.

Validates:
1. Simple command decomposition (single stage, no operators)
2. Pipe decomposition with operator tracking
3. Semicolon-separated commands
4. AND (&&) and OR (||) chains
5. Mixed operators
6. Quote preservation (operators inside quotes are not split)
7. Command substitution extraction ($(...) and `...`)
8. Edge cases (empty input, whitespace, nested substitutions)
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.stage_decomposer import (
    StageDecomposer,
    Stage,
    DecomposedCommand,
)


class TestSimpleCommands:
    """Test decomposition of simple commands (no operators)."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_single_command(self, decomposer):
        """Test simple command produces one stage with no operator."""
        result = decomposer.decompose("ls -la")
        assert len(result.stages) == 1
        assert result.stages[0].command == "ls -la"
        assert result.stages[0].operator is None

    def test_single_word(self, decomposer):
        """Test single-word command."""
        result = decomposer.decompose("pwd")
        assert len(result.stages) == 1
        assert result.stages[0].command == "pwd"
        assert result.stages[0].operator is None

    def test_is_compound_false(self, decomposer):
        """Test is_compound is False for simple commands."""
        result = decomposer.decompose("ls -la")
        assert result.is_compound is False

    def test_executables_property(self, decomposer):
        """Test executables property returns command names."""
        result = decomposer.decompose("ls -la")
        assert result.executables == ["ls"]

    def test_raw_preserved(self, decomposer):
        """Test raw attribute preserves original command."""
        result = decomposer.decompose("  ls -la  ")
        assert result.raw == "ls -la"

    def test_args_tokenized(self, decomposer):
        """Test args are properly tokenized."""
        result = decomposer.decompose("grep -r foo /tmp")
        assert result.stages[0].args == ["grep", "-r", "foo", "/tmp"]


class TestPipeDecomposition:
    """Test pipe (|) operator tracking."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_simple_pipe(self, decomposer):
        """Test two-command pipe tracks operator."""
        result = decomposer.decompose("ls | grep foo")
        assert len(result.stages) == 2
        assert result.stages[0].command == "ls"
        assert result.stages[0].operator == "|"
        assert result.stages[1].command == "grep foo"
        assert result.stages[1].operator is None

    def test_multi_pipe(self, decomposer):
        """Test three-command pipe chain."""
        result = decomposer.decompose("cat file | grep pattern | wc -l")
        assert len(result.stages) == 3
        assert result.stages[0].operator == "|"
        assert result.stages[1].operator == "|"
        assert result.stages[2].operator is None

    def test_is_compound_true(self, decomposer):
        """Test is_compound is True for piped commands."""
        result = decomposer.decompose("ls | grep foo")
        assert result.is_compound is True

    def test_executables_multi(self, decomposer):
        """Test executables lists all commands in a pipe."""
        result = decomposer.decompose("ls | grep foo | wc -l")
        assert result.executables == ["ls", "grep", "wc"]


class TestSemicolonDecomposition:
    """Test semicolon (;) operator tracking."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_semicolon_two_commands(self, decomposer):
        """Test semicolon-separated commands."""
        result = decomposer.decompose("ls; pwd")
        assert len(result.stages) == 2
        assert result.stages[0].command == "ls"
        assert result.stages[0].operator == ";"
        assert result.stages[1].command == "pwd"
        assert result.stages[1].operator is None

    def test_semicolon_three_commands(self, decomposer):
        """Test three semicolon-separated commands."""
        result = decomposer.decompose("ls; pwd; whoami")
        assert len(result.stages) == 3
        assert result.stages[0].operator == ";"
        assert result.stages[1].operator == ";"
        assert result.stages[2].operator is None


class TestAndOrDecomposition:
    """Test && and || operator tracking."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_and_chain(self, decomposer):
        """Test AND (&&) chain tracks operator."""
        result = decomposer.decompose("mkdir dir && cd dir")
        assert len(result.stages) == 2
        assert result.stages[0].command == "mkdir dir"
        assert result.stages[0].operator == "&&"
        assert result.stages[1].command == "cd dir"
        assert result.stages[1].operator is None

    def test_or_chain(self, decomposer):
        """Test OR (||) chain tracks operator."""
        result = decomposer.decompose("test -f file || echo missing")
        assert len(result.stages) == 2
        assert result.stages[0].command == "test -f file"
        assert result.stages[0].operator == "||"
        assert result.stages[1].command == "echo missing"
        assert result.stages[1].operator is None


class TestMixedOperators:
    """Test commands with mixed operators."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_pipe_then_and(self, decomposer):
        """Test pipe followed by AND."""
        result = decomposer.decompose("ls | grep foo && wc -l")
        assert len(result.stages) == 3
        assert result.stages[0].operator == "|"
        assert result.stages[1].operator == "&&"
        assert result.stages[2].operator is None

    def test_semicolon_then_pipe(self, decomposer):
        """Test semicolon followed by pipe."""
        result = decomposer.decompose("echo start; ls | cat")
        assert len(result.stages) == 3
        assert result.stages[0].operator == ";"
        assert result.stages[1].operator == "|"
        assert result.stages[2].operator is None


class TestQuotePreservation:
    """Test that operators inside quotes are NOT treated as separators."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_pipe_in_single_quotes(self, decomposer):
        """Test pipe inside single quotes is preserved."""
        result = decomposer.decompose("echo 'test | with pipe'")
        assert len(result.stages) == 1
        assert "test | with pipe" in result.stages[0].command

    def test_and_in_double_quotes(self, decomposer):
        """Test && inside double quotes is preserved."""
        result = decomposer.decompose('echo "test && with and"')
        assert len(result.stages) == 1
        assert "test && with and" in result.stages[0].command

    def test_semicolon_in_quotes(self, decomposer):
        """Test semicolon inside quotes is preserved."""
        result = decomposer.decompose("echo 'a; b; c'")
        assert len(result.stages) == 1

    def test_mixed_quoted_and_operator(self, decomposer):
        """Test command with both quoted and unquoted operators."""
        result = decomposer.decompose("echo 'hello|world' | cat")
        assert len(result.stages) == 2
        assert "hello|world" in result.stages[0].command
        assert result.stages[0].operator == "|"

    def test_escaped_pipe(self, decomposer):
        """Test escaped pipe is not treated as operator."""
        result = decomposer.decompose("echo test\\|pipe")
        assert len(result.stages) == 1


class TestCommandSubstitution:
    """Test command substitution extraction and non-splitting."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_dollar_paren_not_split(self, decomposer):
        """Test $(...) content does not cause splitting."""
        result = decomposer.decompose("echo $(ls | grep foo)")
        # The pipe inside $() should NOT cause a split
        assert len(result.stages) == 1

    def test_backtick_not_split(self, decomposer):
        """Test backtick content does not cause splitting."""
        result = decomposer.decompose("echo `date`")
        assert len(result.stages) == 1

    def test_substitution_extracted(self, decomposer):
        """Test substitutions are listed in the result."""
        result = decomposer.decompose("echo $(whoami)")
        assert len(result.substitutions) >= 1
        assert "whoami" in result.substitutions

    def test_backtick_substitution_extracted(self, decomposer):
        """Test backtick substitutions are extracted."""
        result = decomposer.decompose("echo `hostname`")
        assert "hostname" in result.substitutions

    def test_substitution_with_external_pipe(self, decomposer):
        """Test $() with pipe inside, and a real pipe outside."""
        result = decomposer.decompose("echo $(ls | wc -l) | cat")
        assert len(result.stages) == 2
        assert result.stages[0].operator == "|"


class TestStageDataclass:
    """Test Stage dataclass behavior."""

    def test_str_representation(self):
        """Test __str__ returns command."""
        s = Stage(command="grep pattern", args=["grep", "pattern"])
        assert str(s) == "grep pattern"

    def test_executable_property(self):
        """Test executable returns first token."""
        s = Stage(command="grep -r pattern /tmp", args=["grep", "-r", "pattern", "/tmp"])
        assert s.executable == "grep"

    def test_executable_empty_command(self):
        """Test executable with empty command returns empty string."""
        s = Stage(command="", args=[])
        assert s.executable == ""

    def test_default_operator_none(self):
        """Test operator defaults to None."""
        s = Stage(command="ls", args=["ls"])
        assert s.operator is None


class TestDecomposedCommand:
    """Test DecomposedCommand dataclass behavior."""

    def test_is_compound_single(self):
        """Test is_compound with single stage."""
        dc = DecomposedCommand(raw="ls", stages=[Stage(command="ls", args=["ls"])])
        assert dc.is_compound is False

    def test_is_compound_multi(self):
        """Test is_compound with multiple stages."""
        dc = DecomposedCommand(
            raw="ls | cat",
            stages=[
                Stage(command="ls", args=["ls"], operator="|"),
                Stage(command="cat", args=["cat"]),
            ],
        )
        assert dc.is_compound is True

    def test_executables_list(self):
        """Test executables returns list of command names."""
        dc = DecomposedCommand(
            raw="ls | grep foo",
            stages=[
                Stage(command="ls", args=["ls"], operator="|"),
                Stage(command="grep foo", args=["grep", "foo"]),
            ],
        )
        assert dc.executables == ["ls", "grep"]


class TestEdgeCases:
    """Test edge cases in decomposition."""

    @pytest.fixture
    def decomposer(self):
        return StageDecomposer()

    def test_empty_string(self, decomposer):
        """Test empty string returns no stages."""
        result = decomposer.decompose("")
        assert result.stages == []
        assert result.is_compound is False

    def test_none_input(self, decomposer):
        """Test None input returns no stages."""
        result = decomposer.decompose(None)
        assert result.stages == []

    def test_whitespace_only(self, decomposer):
        """Test whitespace-only input returns no stages."""
        result = decomposer.decompose("   ")
        assert result.stages == []

    def test_leading_trailing_whitespace(self, decomposer):
        """Test whitespace is stripped from commands."""
        result = decomposer.decompose("  ls -la  ")
        assert result.stages[0].command == "ls -la"

    def test_newline_as_separator(self, decomposer):
        """Test newline acts as separator."""
        result = decomposer.decompose("ls\npwd")
        assert len(result.stages) == 2
        assert result.stages[0].operator == "\n"

    def test_nested_substitution(self, decomposer):
        """Test nested $() does not crash."""
        result = decomposer.decompose("echo $(echo $(date))")
        assert len(result.stages) == 1

    def test_unclosed_quote(self, decomposer):
        """Test unclosed quote does not crash."""
        result = decomposer.decompose("echo 'unclosed")
        assert isinstance(result.stages, list)

    def test_args_with_quotes(self, decomposer):
        """Test args tokenization handles quoted arguments."""
        result = decomposer.decompose("echo 'hello world' foo")
        args = result.stages[0].args
        assert "hello world" in args
        assert "foo" in args
