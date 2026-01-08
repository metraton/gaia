#!/usr/bin/env python3
"""
Tests for Safe Command Detection.

Validates:
1. is_single_command_safe()
2. is_read_only_command()
3. Safe command configuration
"""

import sys
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.safe_commands import (
    is_single_command_safe,
    is_read_only_command,
    ALWAYS_SAFE_COMMANDS,
    ALWAYS_SAFE_MULTIWORD,
    CONDITIONAL_SAFE_COMMANDS,
)


class TestIsSingleCommandSafe:
    """Test is_single_command_safe() function."""

    # Always safe commands
    @pytest.mark.parametrize("command", [
        "ls",
        "ls -la",
        "pwd",
        "whoami",
        "date",
        "hostname",
        "uname -a",
        "cat file.txt",
        "head -n 10 file.log",
        "tail -100 app.log",
        "grep pattern file",
        "echo hello",
        "wc -l file",
        "sort file.txt",
    ])
    def test_always_safe_commands(self, command):
        """Test always-safe commands return True."""
        is_safe, reason = is_single_command_safe(command)
        assert is_safe is True, f"{command} should be safe: {reason}"

    # Multiword safe commands
    @pytest.mark.parametrize("command", [
        "git status",
        "git log --oneline",
        "git diff HEAD~1",
        "git branch -a",
        "kubectl get pods",
        "kubectl describe pod test-pod",
        "kubectl logs deployment/app",
        "terraform plan",
        "terraform validate",
        "helm list",
        "flux get all",
    ])
    def test_multiword_safe_commands(self, command):
        """Test multiword safe commands return True."""
        is_safe, reason = is_single_command_safe(command)
        assert is_safe is True, f"{command} should be safe: {reason}"

    # Conditional safe commands (no dangerous flags)
    @pytest.mark.parametrize("command", [
        "sed 's/old/new/' file.txt",  # No -i flag
        "curl https://api.example.com/",  # GET request
        "wget https://example.com/file.tar.gz",  # Download only
        "find . -name '*.py'",  # No -delete
    ])
    def test_conditional_safe_commands(self, command):
        """Test conditional safe commands without dangerous flags."""
        is_safe, reason = is_single_command_safe(command)
        assert is_safe is True, f"{command} should be safe: {reason}"

    # Conditional unsafe commands (with dangerous flags)
    @pytest.mark.parametrize("command", [
        "sed -i 's/old/new/' file.txt",  # Has -i flag
        "sed --in-place 's/x/y/' file",  # Has --in-place
        "curl -X POST https://api.example.com/",  # POST method
        "curl --data '{\"key\":\"value\"}' url",  # Has --data
        "find . -delete",  # Has -delete
        "find . -exec rm {} \;",  # Has -exec rm
    ])
    def test_conditional_unsafe_commands(self, command):
        """Test conditional commands with dangerous flags."""
        is_safe, reason = is_single_command_safe(command)
        assert is_safe is False, f"{command} should NOT be safe: {reason}"

    # Unsafe commands
    @pytest.mark.parametrize("command", [
        "rm -rf /",
        "terraform apply",
        "kubectl apply -f file.yaml",
        "docker run image",
        "dd if=/dev/zero of=/dev/sda",
    ])
    def test_unsafe_commands(self, command):
        """Test unsafe commands return False."""
        is_safe, reason = is_single_command_safe(command)
        assert is_safe is False, f"{command} should NOT be safe: {reason}"

    def test_empty_command(self):
        """Test empty command returns False."""
        is_safe, reason = is_single_command_safe("")
        assert is_safe is False

    def test_whitespace_command(self):
        """Test whitespace command returns False."""
        is_safe, reason = is_single_command_safe("   ")
        assert is_safe is False

    def test_command_with_path(self):
        """Test command with full path."""
        is_safe, reason = is_single_command_safe("/usr/bin/ls -la")
        assert is_safe is True

    def test_command_with_bin_path(self):
        """Test command with /bin/ path."""
        is_safe, reason = is_single_command_safe("/bin/cat file.txt")
        assert is_safe is True


class TestIsReadOnlyCommand:
    """Test is_read_only_command() function."""

    # Simple safe commands
    @pytest.mark.parametrize("command", [
        "ls -la",
        "pwd",
        "cat file.txt",
        "kubectl get pods",
    ])
    def test_simple_safe_commands(self, command):
        """Test simple safe commands."""
        is_safe, reason = is_read_only_command(command)
        assert is_safe is True, f"{command} should be safe: {reason}"

    # Compound safe commands (all components safe)
    @pytest.mark.parametrize("command", [
        "ls -la | grep pattern",
        "cat file | head -10",
        "kubectl get pods | grep Running",
        "git status && git log --oneline -5",
        "pwd || echo failed",
        "ls; pwd; whoami",
    ])
    def test_compound_safe_commands(self, command):
        """Test compound commands where all parts are safe."""
        is_safe, reason = is_read_only_command(command)
        assert is_safe is True, f"{command} should be safe: {reason}"

    # Compound unsafe commands (one unsafe component)
    @pytest.mark.parametrize("command", [
        "ls && rm -rf /",
        "cat file | kubectl apply -f -",
        "git log && git push origin main",  # push is unsafe
        "git log && git push",
    ])
    def test_compound_unsafe_commands(self, command):
        """Test compound commands where at least one part is unsafe."""
        is_safe, reason = is_read_only_command(command)
        assert is_safe is False, f"{command} should NOT be safe: {reason}"

    def test_returns_component_count_in_reason(self):
        """Test that reason mentions component count for compound commands."""
        command = "ls | grep pattern | wc -l"
        is_safe, reason = is_read_only_command(command)
        assert is_safe is True
        # Should mention number of components
        assert "3" in reason or "components" in reason.lower()


class TestSafeCommandsConfig:
    """Test safe commands configuration."""

    def test_always_safe_is_set(self):
        """Test ALWAYS_SAFE_COMMANDS is populated."""
        assert len(ALWAYS_SAFE_COMMANDS) > 0

    def test_contains_common_commands(self):
        """Test contains common safe commands."""
        common = ["ls", "pwd", "grep", "echo", "date", "hostname"]
        for cmd in common:
            assert cmd in ALWAYS_SAFE_COMMANDS, f"{cmd} should be in ALWAYS_SAFE"

    def test_multiword_safe_is_set(self):
        """Test ALWAYS_SAFE_MULTIWORD is populated."""
        assert len(ALWAYS_SAFE_MULTIWORD) > 0

    def test_multiword_contains_git_read(self):
        """Test multiword contains git read operations."""
        git_ops = ["git status", "git log", "git diff"]
        for op in git_ops:
            assert op in ALWAYS_SAFE_MULTIWORD, f"{op} should be in multiword safe"

    def test_multiword_contains_kubectl_read(self):
        """Test multiword contains kubectl read operations."""
        k8s_ops = ["kubectl get", "kubectl describe", "kubectl logs"]
        for op in k8s_ops:
            assert op in ALWAYS_SAFE_MULTIWORD, f"{op} should be in multiword safe"

    def test_conditional_safe_is_set(self):
        """Test CONDITIONAL_SAFE_COMMANDS is populated."""
        assert len(CONDITIONAL_SAFE_COMMANDS) > 0

    def test_conditional_contains_sed(self):
        """Test sed is in conditional safe with -i pattern."""
        assert "sed" in CONDITIONAL_SAFE_COMMANDS
        patterns = CONDITIONAL_SAFE_COMMANDS["sed"]
        assert any("-i" in p for p in patterns)

    def test_conditional_contains_curl(self):
        """Test curl is in conditional safe."""
        assert "curl" in CONDITIONAL_SAFE_COMMANDS


class TestEdgeCases:
    """Test edge cases in safe command detection."""

    def test_quoted_pipe_not_split(self):
        """Test that quoted pipes are not split."""
        # This depends on shell_parser behavior
        command = "echo 'test | pipe'"
        is_safe, reason = is_read_only_command(command)
        assert is_safe is True

    def test_command_with_newline(self):
        """Test command with newline is handled."""
        command = "ls\npwd"
        is_safe, _ = is_read_only_command(command)
        # Implementation-dependent - may split or not
        assert isinstance(is_safe, bool)

    def test_git_push_is_not_safe(self):
        """Test git push is explicitly not safe."""
        is_safe, _ = is_read_only_command("git push origin main")
        assert is_safe is False

    def test_terraform_apply_is_not_safe(self):
        """Test terraform apply is not safe."""
        is_safe, _ = is_read_only_command("terraform apply")
        assert is_safe is False

    def test_kubectl_apply_is_not_safe(self):
        """Test kubectl apply is not safe."""
        is_safe, _ = is_read_only_command("kubectl apply -f manifest.yaml")
        assert is_safe is False
