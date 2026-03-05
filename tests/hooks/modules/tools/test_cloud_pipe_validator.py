#!/usr/bin/env python3
"""
Tests for cloud_pipe_validator.

Validates regex patterns correctly detect violations without false positives:
- Pipe `|` detected, but logical OR `||` is NOT a pipe
- Redirect `>` / `>>` detected, but `2>&1` is NOT a redirect
- Chaining `;` / `&&` detected correctly
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.cloud_pipe_validator import (
    validate_cloud_pipe,
    _find_violation,
    _strip_quoted_sections,
)


class TestPipeDetection:
    """Test pipe regex: should catch real pipes, not logical OR."""

    def test_real_pipe_detected(self):
        """Real pipe `|` in a cloud command should trigger violation."""
        result = validate_cloud_pipe("kubectl get pods | grep nginx")
        assert result is not None
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "pipe" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_logical_or_not_detected_as_pipe(self):
        """Logical OR `||` should NOT trigger pipe violation."""
        result = validate_cloud_pipe("kubectl get pods || echo 'failed'")
        # || should NOT match as a pipe, but it might match as chaining
        # The key is it does NOT say "pipe" in the reason
        if result is not None:
            reason = result["hookSpecificOutput"]["permissionDecisionReason"].lower()
            # If it's blocked, it should NOT be for "pipe" -- might be "chaining"
            assert "no pipes" not in reason

    def test_logical_or_with_complex_command(self):
        """Complex command with `||` should not be falsely flagged as pipe."""
        result = validate_cloud_pipe("kubectl delete namespace test 2>&1 || echo 'blocked'")
        # Should NOT be flagged as a pipe violation
        if result is not None:
            reason = result["hookSpecificOutput"]["permissionDecisionReason"].lower()
            assert "no pipes" not in reason


class TestRedirectDetection:
    """Test redirect regex: should catch real redirects, not fd duplication."""

    def test_real_redirect_detected(self):
        """Real redirect `>` should trigger violation."""
        result = validate_cloud_pipe("terraform apply > output.log")
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_append_redirect_detected(self):
        """Append redirect `>>` should trigger violation."""
        result = validate_cloud_pipe("gcloud compute instances list >> instances.txt")
        assert result is not None
        assert "redirect" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_fd_duplication_not_detected(self):
        """File descriptor duplication `2>&1` should NOT trigger redirect violation."""
        result = validate_cloud_pipe("terraform apply 2>&1")
        assert result is None, (
            f"2>&1 should not trigger a violation, got: "
            f"{result['hookSpecificOutput']['permissionDecisionReason'] if result else 'None'}"
        )

    def test_fd_duplication_in_complex_command(self):
        """Command with `2>&1` should not trigger redirect."""
        result = validate_cloud_pipe("kubectl get pods 2>&1")
        assert result is None

    def test_stderr_redirect_to_dev_null(self):
        """Redirect `2>/dev/null` has a `>` that could match -- verify handling.

        Note: `2>` has a digit before `>`. Our regex uses lookbehind for `>&`
        but the `2>` case is a real redirect (stderr to file), so it SHOULD match.
        """
        result = validate_cloud_pipe("kubectl get pods 2>/dev/null")
        # This IS a real redirect (stderr to /dev/null), so it should match
        assert result is not None


class TestChainingDetection:
    """Test chaining regex: `;` and `&&`."""

    def test_semicolon_detected(self):
        """Semicolon chaining should trigger violation."""
        result = validate_cloud_pipe("kubectl get pods; kubectl get svc")
        assert result is not None
        assert "chaining" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()

    def test_and_chain_detected(self):
        """Double ampersand `&&` chaining should trigger violation."""
        result = validate_cloud_pipe("kubectl get pods && kubectl get svc")
        assert result is not None
        assert "chaining" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


class TestNonCloudCommands:
    """Test that non-cloud commands are NOT checked."""

    @pytest.mark.parametrize("command", [
        "ls -la | grep test",
        "cat file.txt > output.txt",
        "echo hello && echo world",
        "python script.py | head",
    ])
    def test_non_cloud_commands_pass(self, command):
        """Non-cloud commands should not trigger any violation."""
        result = validate_cloud_pipe(command)
        assert result is None


class TestQuotedStrings:
    """Test that operators inside quotes are not detected."""

    def test_pipe_in_quotes_ignored(self):
        """Pipe character inside quotes should not trigger violation."""
        result = validate_cloud_pipe("kubectl get pods --field-selector='status.phase|Running'")
        assert result is None

    def test_redirect_in_quotes_ignored(self):
        """Redirect character inside quotes should not trigger violation."""
        result = validate_cloud_pipe("gcloud compute instances list --filter='name > abc'")
        assert result is None


class TestCombinedFalsePositives:
    """Test the specific false positive scenario from the bug report."""

    def test_kubectl_delete_with_or_and_fd_dup(self):
        """The exact bug scenario: `kubectl delete namespace test 2>&1 || echo 'blocked'`.

        This should NOT trigger cloud_pipe_validator at all because:
        - `||` is logical OR, not a pipe
        - `2>&1` is fd duplication, not a redirect
        """
        result = validate_cloud_pipe("kubectl delete namespace test 2>&1 || echo 'blocked'")
        # Should NOT be flagged as a pipe or redirect
        assert result is None, (
            f"Should not trigger cloud_pipe_validator, got: "
            f"{result['hookSpecificOutput']['permissionDecisionReason'] if result else 'None'}"
        )

    def test_real_pipe_still_caught(self):
        """Real pipe in kubectl should still be caught."""
        result = validate_cloud_pipe("kubectl get pods | grep nginx")
        assert result is not None

    def test_real_redirect_still_caught(self):
        """Real redirect in terraform should still be caught."""
        result = validate_cloud_pipe("terraform apply > output.log")
        assert result is not None
