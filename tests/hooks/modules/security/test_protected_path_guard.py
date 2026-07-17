#!/usr/bin/env python3
"""Tests for the Bash command-string protected-path guard.

Regression coverage for the security hole where `git mv` (routed through
GIT_LOCAL_SAFE_SUBCOMMANDS) could overwrite protected hook code through Bash
with no consent, bypassing both the tier gate and the Write/Edit sensitive-path
backstop (which never inspects Bash command strings).

The guard categorically denies any WRITE-capable command whose target resolves
into the protected .claude/ tree, while leaving READS untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.protected_path_guard import (
    check,
    rejection_message,
    targets_protected_path,
    _is_protected_claude_path,
)


# ----------------------------------------------------------------------------
# The core hole: git mv into .claude/ must be blocked
# ----------------------------------------------------------------------------

class TestGitMvIntoClaudeBlocked:
    def test_git_mv_overwrites_hook_code_blocked(self):
        """The finding's exact payload: git mv into a protected hook path."""
        cmd = "git mv payload.py .claude/hooks/pre_tool_use.py"
        allowed, reason = check(cmd)
        assert allowed is False
        assert ".claude/hooks/pre_tool_use.py" in reason
        assert "not approvable" in reason

    def test_git_mv_absolute_claude_path_blocked(self):
        cmd = "git mv payload.py /home/user/proj/.claude/hooks/post_tool_use.py"
        allowed, _ = check(cmd)
        assert allowed is False

    def test_git_mv_into_claude_settings_blocked(self):
        cmd = "git mv evil.json .claude/settings.json"
        allowed, _ = check(cmd)
        assert allowed is False

    def test_git_mv_settings_local_anywhere_blocked(self):
        cmd = "git mv x .claude/nested/dir/settings.local.json"
        allowed, _ = check(cmd)
        assert allowed is False

    def test_git_mv_with_cd_prefix_blocked(self):
        """A leading `cd` component must not hide the writer component."""
        cmd = "cd /repo && git mv payload.py .claude/hooks/pre_tool_use.py"
        allowed, _ = check(cmd)
        assert allowed is False

    def test_dotdot_traversal_into_claude_blocked(self):
        cmd = "git mv payload.py ../foo/../.claude/hooks/pre_tool_use.py"
        allowed, _ = check(cmd)
        assert allowed is False


# ----------------------------------------------------------------------------
# Other write-capable mechanisms into .claude/ are also blocked
# ----------------------------------------------------------------------------

class TestOtherWritersBlocked:
    @pytest.mark.parametrize("cmd", [
        "mv payload.py .claude/hooks/pre_tool_use.py",
        "cp payload.py .claude/hooks/pre_tool_use.py",
        "install -m 755 payload.py .claude/hooks/pre_tool_use.py",
        "tee .claude/hooks/pre_tool_use.py",
        "ln -sf payload.py .claude/hooks/pre_tool_use.py",
        "sed -i s/a/b/ .claude/hooks/pre_tool_use.py",
        "rm .claude/hooks/pre_tool_use.py",
        "git checkout other-branch -- .claude/hooks/pre_tool_use.py",
        "git restore --source=HEAD .claude/hooks/pre_tool_use.py",
    ])
    def test_writer_into_claude_blocked(self, cmd):
        allowed, _ = check(cmd)
        assert allowed is False, f"{cmd!r} should be blocked"

    def test_redirect_into_claude_blocked(self):
        cmd = "echo evil > .claude/hooks/pre_tool_use.py"
        allowed, _ = check(cmd)
        assert allowed is False


# ----------------------------------------------------------------------------
# Reads and non-.claude writes must pass through (no false positives)
# ----------------------------------------------------------------------------

class TestReadsAndUnrelatedAllowed:
    @pytest.mark.parametrize("cmd", [
        "git diff .claude/hooks/pre_tool_use.py",
        "git log --oneline .claude/hooks/pre_tool_use.py",
        "git show HEAD:.claude/settings.json",
        "cat .claude/settings.json",
        "grep -r pattern .claude/hooks/",
        "ls .claude/hooks/",
        "git status .claude/",
    ])
    def test_reads_of_claude_allowed(self, cmd):
        allowed, reason = check(cmd)
        assert allowed is True, f"{cmd!r} is a read and should pass, got {reason!r}"

    @pytest.mark.parametrize("cmd", [
        "git mv src/a.py src/b.py",
        "mv payload.py gaia/hooks/pre_tool_use.py",
        "cp a.txt b.txt",
        "git mv x .claude-backup/hooks/y.py",
        "git commit -m 'update .claude/hooks docs'",
    ])
    def test_non_protected_writes_allowed(self, cmd):
        allowed, _ = check(cmd)
        assert allowed is True, f"{cmd!r} does not touch protected .claude/ tree"

    def test_md_doc_under_hooks_allowed(self):
        """Docs under .claude/hooks/ do not execute code -- exempt, matching
        the .md carve-out in _is_protected()."""
        cmd = "git mv notes.md .claude/hooks/README.md"
        allowed, _ = check(cmd)
        assert allowed is True

    def test_read_component_not_associated_with_unrelated_writer(self):
        """A read of .claude/ chained with an unrelated writer must not fire."""
        cmd = "cat .claude/settings.json && mv a.txt b.txt"
        allowed, _ = check(cmd)
        assert allowed is True


# ----------------------------------------------------------------------------
# Path predicate unit coverage
# ----------------------------------------------------------------------------

class TestIsProtectedClaudePath:
    @pytest.mark.parametrize("token,expected", [
        (".claude/hooks/pre_tool_use.py", True),
        ("/abs/.claude/hooks/x.py", True),
        (".claude/settings.json", True),
        (".claude/settings.local.json", True),
        (".claude/deep/dir/settings.json", True),
        (".claude/hooks/README.md", False),      # doc exempt
        (".claude/agents/gaia-system.md", False),  # not hooks, not settings
        (".claude-backup/hooks/x.py", False),    # exact component match only
        ("gaia/hooks/pre_tool_use.py", False),   # source tree, not .claude
        ("-f", False),                           # flag token
        ("", False),
    ])
    def test_predicate(self, token, expected):
        assert _is_protected_claude_path(token) is expected


def test_targets_protected_path_returns_offending_path():
    hit = targets_protected_path("git mv a .claude/hooks/pre_tool_use.py")
    assert hit == ".claude/hooks/pre_tool_use.py"


def test_clean_command_returns_none():
    assert targets_protected_path("git status") is None


def test_rejection_message_names_path():
    msg = rejection_message(".claude/hooks/x.py")
    assert ".claude/hooks/x.py" in msg
    assert "hard security boundary" in msg
