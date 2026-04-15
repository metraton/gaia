#!/usr/bin/env python3
"""
Tests for Shell Unwrapper.

Validates:
1. Single-layer wrapper detection and stripping
2. Double-layer (nested) wrapper stripping
3. No-wrap passthrough (commands without wrappers)
4. Various shell interpreters (bash, sh, zsh, dash)
5. Path-prefixed shells (/bin/bash, /usr/bin/env bash)
6. Prefix commands (exec, env with VAR=val, nohup, sudo)
7. Different quoting styles (single, double, unquoted)
8. Edge cases (empty, whitespace, max recursion depth)
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.shell_unwrapper import (
    ShellUnwrapper,
    UnwrapResult,
)


class TestSingleLayerUnwrap:
    """Test stripping a single wrapper layer."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_bash_c_double_quoted(self, unwrapper):
        """Test bash -c with double-quoted payload."""
        result = unwrapper.unwrap('bash -c "ls -la"')
        assert result.inner == "ls -la"
        assert result.depth == 1
        assert result.was_wrapped is True

    def test_bash_c_single_quoted(self, unwrapper):
        """Test bash -c with single-quoted payload."""
        result = unwrapper.unwrap("bash -c 'ls -la'")
        assert result.inner == "ls -la"
        assert result.depth == 1
        assert result.was_wrapped is True

    def test_sh_c(self, unwrapper):
        """Test sh -c wrapper."""
        result = unwrapper.unwrap('sh -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_zsh_c(self, unwrapper):
        """Test zsh -c wrapper."""
        result = unwrapper.unwrap('zsh -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_dash_c(self, unwrapper):
        """Test dash -c wrapper."""
        result = unwrapper.unwrap('dash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_unquoted_payload(self, unwrapper):
        """Test bash -c with unquoted payload."""
        result = unwrapper.unwrap("bash -c ls")
        assert result.inner == "ls"
        assert result.depth == 1


class TestDoubleLayerUnwrap:
    """Test stripping nested wrapper layers."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_double_nested(self, unwrapper):
        """Test bash -c wrapping sh -c."""
        result = unwrapper.unwrap("bash -c \"sh -c 'actual command'\"")
        assert result.inner == "actual command"
        assert result.depth == 2
        assert result.was_wrapped is True

    def test_triple_nested(self, unwrapper):
        """Test three layers of nesting."""
        result = unwrapper.unwrap("bash -c \"sh -c \\\"dash -c 'inner'\\\"\"")
        assert result.inner == "inner"
        assert result.depth == 3


class TestNoWrapPassthrough:
    """Test commands without wrappers pass through unchanged."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_simple_command(self, unwrapper):
        """Test simple command passes through."""
        result = unwrapper.unwrap("ls -la")
        assert result.inner == "ls -la"
        assert result.depth == 0
        assert result.was_wrapped is False

    def test_git_command(self, unwrapper):
        """Test git command passes through."""
        result = unwrapper.unwrap("git status")
        assert result.inner == "git status"
        assert result.depth == 0
        assert result.was_wrapped is False

    def test_command_with_c_flag_not_shell(self, unwrapper):
        """Test non-shell command with -c flag is not mistakenly unwrapped."""
        result = unwrapper.unwrap("gcc -c main.c")
        assert result.inner == "gcc -c main.c"
        assert result.depth == 0
        assert result.was_wrapped is False

    def test_bash_without_c_flag(self, unwrapper):
        """Test bash without -c is not unwrapped."""
        result = unwrapper.unwrap("bash script.sh")
        assert result.inner == "bash script.sh"
        assert result.depth == 0
        assert result.was_wrapped is False


class TestPathPrefixedShells:
    """Test shells with absolute path prefixes."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_bin_bash(self, unwrapper):
        """Test /bin/bash -c wrapper."""
        result = unwrapper.unwrap('/bin/bash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_usr_bin_bash(self, unwrapper):
        """Test /usr/bin/bash -c wrapper."""
        result = unwrapper.unwrap('/usr/bin/bash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_bin_sh(self, unwrapper):
        """Test /bin/sh -c wrapper."""
        result = unwrapper.unwrap('/bin/sh -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1


class TestPrefixCommands:
    """Test prefix commands before the shell wrapper."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_env_bash(self, unwrapper):
        """Test env bash -c wrapper."""
        result = unwrapper.unwrap('env bash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_env_with_vars(self, unwrapper):
        """Test env VAR=val bash -c wrapper."""
        result = unwrapper.unwrap('env FOO=bar bash -c "echo $FOO"')
        assert result.inner == "echo $FOO"
        assert result.depth == 1

    def test_exec_bash(self, unwrapper):
        """Test exec bash -c wrapper."""
        result = unwrapper.unwrap('exec bash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_sudo_bash(self, unwrapper):
        """Test sudo bash -c wrapper."""
        result = unwrapper.unwrap('sudo bash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1

    def test_nohup_bash(self, unwrapper):
        """Test nohup bash -c wrapper."""
        result = unwrapper.unwrap('nohup bash -c "echo hello"')
        assert result.inner == "echo hello"
        assert result.depth == 1


class TestIsWrapped:
    """Test is_wrapped convenience method."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_wrapped_returns_true(self, unwrapper):
        """Test is_wrapped returns True for wrapped command."""
        assert unwrapper.is_wrapped('bash -c "ls"') is True

    def test_unwrapped_returns_false(self, unwrapper):
        """Test is_wrapped returns False for plain command."""
        assert unwrapper.is_wrapped("ls -la") is False

    def test_empty_returns_false(self, unwrapper):
        """Test is_wrapped returns False for empty input."""
        assert unwrapper.is_wrapped("") is False


class TestUnwrapResult:
    """Test UnwrapResult dataclass."""

    def test_str_representation(self):
        """Test __str__ returns inner command."""
        r = UnwrapResult(inner="ls -la", depth=1, was_wrapped=True)
        assert str(r) == "ls -la"

    def test_unwrapped_result(self):
        """Test passthrough result values."""
        r = UnwrapResult(inner="git status", depth=0, was_wrapped=False)
        assert r.inner == "git status"
        assert r.depth == 0
        assert r.was_wrapped is False


class TestEdgeCases:
    """Test edge cases."""

    @pytest.fixture
    def unwrapper(self):
        return ShellUnwrapper()

    def test_empty_string(self, unwrapper):
        """Test empty string returns empty inner."""
        result = unwrapper.unwrap("")
        assert result.inner == ""
        assert result.depth == 0
        assert result.was_wrapped is False

    def test_none_input(self, unwrapper):
        """Test None input returns empty inner."""
        result = unwrapper.unwrap(None)
        assert result.inner == ""
        assert result.depth == 0

    def test_whitespace_only(self, unwrapper):
        """Test whitespace-only input."""
        result = unwrapper.unwrap("   ")
        assert result.depth == 0
        assert result.was_wrapped is False

    def test_escaped_quotes_in_payload(self, unwrapper):
        """Test escaped quotes inside double-quoted payload."""
        result = unwrapper.unwrap('bash -c "echo \\"hello\\""')
        assert "hello" in result.inner
        assert result.depth == 1

    def test_multiword_unquoted_payload(self, unwrapper):
        """Test unquoted payload with multiple words."""
        result = unwrapper.unwrap("bash -c echo hello world")
        assert result.inner == "echo hello world"
        assert result.depth == 1
