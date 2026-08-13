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
# Grouping / substitution wrappers do not lift the categorical boundary
# ----------------------------------------------------------------------------

class TestGroupingWrappedWritesBlocked:
    """A wrapper character glued to the writer must not open this boundary.

    Write capability is decided by ``tokens[0]``, so ``(cp`` and ``$(cp``
    matched nothing and the guard returned None -- the .claude/ hooks tree,
    whose whole purpose is that no agent can rewrite the security layer, was
    reachable through a subshell. Two of these forms did not pass outright but
    degraded from CATEGORICAL to merely APPROVABLE, which is the same breach:
    the boundary is not a price, it is a refusal.
    """

    @pytest.mark.parametrize("cmd", [
        "(cp payload.py .claude/hooks/pre_tool_use.py)",
        "( cp payload.py .claude/hooks/pre_tool_use.py )",
        "{ cp payload.py .claude/hooks/pre_tool_use.py; }",
        "$(cp payload.py .claude/hooks/pre_tool_use.py)",
        "`cp payload.py .claude/hooks/pre_tool_use.py`",
        "((cp payload.py .claude/hooks/pre_tool_use.py))",
        "(tee .claude/hooks/pre_tool_use.py)",
        "(git checkout -- .claude/hooks/pre_tool_use.py)",
        "(mv payload.py .claude/settings.json)",
        "$(sed -i s/a/b/ .claude/hooks/pre_tool_use.py)",
    ])
    def test_wrapped_writer_into_claude_blocked(self, cmd):
        allowed, reason = check(cmd)
        assert allowed is False, f"{cmd!r} should be categorically blocked"
        assert "[PROTECTED_PATH]" in reason

    @pytest.mark.parametrize("cmd", [
        "(cat .claude/settings.json)",
        "$(grep -r pattern .claude/hooks/)",
        "(cd /home/jorge/ws/me && ls .claude/hooks/)",
        'gaia contract add evidence_report.key_outputs '
        '"(cp payload.py .claude/hooks/pre_tool_use.py) was never run"',
        'grep -rn "SessionStart" /home/jorge/ws/me/.claude/settings.local.json',
    ])
    def test_wrapped_read_or_mention_still_allowed(self, cmd):
        """Unwrapping is applied to the component STRING, never per token.

        A read inside a subshell is still a read, and a dangerous command
        QUOTED into another command's argument survives tokenization as one
        opaque token -- neither may start costing consent because the wrapper
        family was closed.
        """
        allowed, reason = check(cmd)
        assert allowed is True, f"{cmd!r} is not a write, got {reason!r}"


class TestMidStringSubstitutionWritesBlocked:
    """The same boundary, one token to the right of the wrapper family above.

    Unwrapping reaches position 0 only. A substitution in the MIDDLE of a chain
    puts ``echo`` at position 0 and the write inside the parens -- and the
    shell evaluates that substitution BEFORE ``echo`` ever starts, so the hook
    file is overwritten whatever ``echo`` then does with the output. Every form
    here passed the guard outright, which is the breach in its purest form: not
    a price, an absence.

    Double-quoted forms are here rather than among the mentions on purpose. A
    substitution inside DOUBLE quotes still executes; only single quotes make
    it text.
    """

    @pytest.mark.parametrize("cmd", [
        "echo $(cp payload.py .claude/hooks/pre_tool_use.py)",
        'echo "$(cp payload.py .claude/hooks/pre_tool_use.py)"',
        "echo `cp payload.py .claude/hooks/pre_tool_use.py`",
        "echo $(git mv payload.py .claude/hooks/pre_tool_use.py)",
        "ls $(tee .claude/hooks/pre_tool_use.py)",
        "echo $(mv payload.py .claude/settings.json)",
        "echo $(sed -i s/a/b/ .claude/hooks/pre_tool_use.py)",
        "echo $(echo $(cp payload.py .claude/hooks/pre_tool_use.py))",
        "diff /tmp/a <(cp payload.py .claude/hooks/pre_tool_use.py)",
        "echo ${FOO:-$(cp payload.py .claude/hooks/pre_tool_use.py)}",
        # A pipe inside the body: extraction runs on the FULL command, so the
        # body survives intact and is only then split on operators.
        "echo $(cat payload.py | tee .claude/hooks/pre_tool_use.py)",
        # ANSI-C quoting honours the backslash, so an escaped quote does not
        # end the string. Reading it as a plain single quote desynced the
        # scanner and made everything to its right invisible -- this boundary
        # included, in both substitution spellings.
        r"echo $'it\'s' $(cp payload.py .claude/hooks/pre_tool_use.py)",
        r"echo $'it\'s' " + '"$(cp payload.py .claude/hooks/pre_tool_use.py)"',
        # A parameter expansion carrying a close-paren as data must not end the
        # body early and drop the writer that follows it.
        "echo $(grep -rn ${x//)/y} /tmp; cp payload.py .claude/hooks/pre_tool_use.py)",
    ])
    def test_substituted_writer_into_claude_blocked(self, cmd):
        allowed, reason = check(cmd)
        assert allowed is False, f"{cmd!r} should be categorically blocked"
        assert "[PROTECTED_PATH]" in reason

    @pytest.mark.parametrize("cmd", [
        # Single quotes: the characters are there, the execution is not.
        "echo 'literal $(cp payload.py .claude/hooks/pre_tool_use.py)'",
        "grep -rn '$(cp payload.py .claude/hooks/pre_tool_use.py)' hooks/",
        "gaia contract add evidence_report.open_gaps "
        "'echo $(cp payload.py .claude/hooks/pre_tool_use.py) was free'",
        "gaia memory search '`cp payload.py .claude/hooks/pre_tool_use.py`'",
        r'echo "\$(cp payload.py .claude/hooks/pre_tool_use.py)"',
        "ls -la # $(cp payload.py .claude/hooks/pre_tool_use.py)",
        # A read reached through a substitution is still a read.
        "echo $(cat .claude/settings.json)",
        "echo $(grep -rn SessionStart .claude/settings.local.json)",
        "wc -l $(ls .claude/hooks/)",
        # ANSI-C quoting is how a shell writes a newline or a tab; a
        # substitution merely NAMED inside one is still text.
        r"echo $'mention $(cp payload.py .claude/hooks/pre_tool_use.py)'",
        r"printf $'a\tb\n'",
    ])
    def test_substituted_read_or_mention_still_allowed(self, cmd):
        """Quoting, not position, is what separates a mention from a use.

        A guard that reached into the middle of a string by looking for the
        CHARACTERS would gate every one of these -- and the first three are
        exactly what an agent types to report a finding about this boundary.
        Gating them makes the system unusable for discussing its own security.
        """
        allowed, reason = check(cmd)
        assert allowed is True, f"{cmd!r} is not a write, got {reason!r}"


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
