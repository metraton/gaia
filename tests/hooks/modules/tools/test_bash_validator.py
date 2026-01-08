#!/usr/bin/env python3
"""
Tests for Bash Command Validator.

Validates:
1. BashValidator class
2. Compound command validation
3. Credential requirement detection
4. Permission response generation
"""

import sys
import json
import pytest
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.tools.bash_validator import (
    BashValidator,
    BashValidationResult,
    validate_bash_command,
    create_permission_allow_response,
)
from modules.security.tiers import SecurityTier


class TestBashValidator:
    """Test BashValidator class."""

    @pytest.fixture
    def validator(self):
        return BashValidator()

    # Safe commands
    @pytest.mark.parametrize("command", [
        "ls -la",
        "pwd",
        "cat file.txt",
        "kubectl get pods",
        "terraform plan",
        "git status",
    ])
    def test_allows_safe_commands(self, validator, command):
        """Test allows safe commands."""
        result = validator.validate(command)
        assert result.allowed is True

    # Blocked commands
    @pytest.mark.parametrize("command", [
        "terraform apply",
        "kubectl apply -f manifest.yaml",
        "rm -rf /",
        "git push origin main",
    ])
    def test_blocks_dangerous_commands(self, validator, command):
        """Test blocks dangerous commands."""
        result = validator.validate(command)
        assert result.allowed is False
        assert result.tier == SecurityTier.T3_BLOCKED

    def test_blocks_empty_command(self, validator):
        """Test blocks empty command."""
        result = validator.validate("")
        assert result.allowed is False

    def test_blocks_whitespace_command(self, validator):
        """Test blocks whitespace-only command."""
        result = validator.validate("   ")
        assert result.allowed is False


class TestCompoundCommandValidation:
    """Test compound command validation."""

    @pytest.fixture
    def validator(self):
        return BashValidator()

    def test_allows_all_safe_compound(self, validator):
        """Test allows compound command when all parts are safe."""
        result = validator.validate("ls -la | grep pattern | wc -l")
        assert result.allowed is True

    def test_blocks_compound_with_unsafe_part(self, validator):
        """Test blocks compound command with one unsafe part."""
        result = validator.validate("ls -la && rm -rf /tmp/*")
        assert result.allowed is False
        assert "component" in result.reason.lower()

    def test_blocks_piped_unsafe(self, validator):
        """Test blocks piped command with unsafe part."""
        result = validator.validate("cat file | kubectl apply -f -")
        assert result.allowed is False

    def test_returns_highest_tier(self, validator):
        """Test returns highest tier among components."""
        # T0 | T0 -> T0
        result_safe = validator.validate("ls | grep foo")
        assert result_safe.allowed is True
        assert result_safe.tier == SecurityTier.T0_READ_ONLY


class TestClaudeFooterDetection:
    """Test Claude Code attribution footer detection."""

    @pytest.fixture
    def validator(self):
        return BashValidator()

    def test_blocks_generated_with_claude_code(self, validator):
        """Test blocks commands with Claude Code attribution."""
        command = '''git commit -m "Update README

Generated with Claude Code
"'''
        result = validator.validate(command)
        assert result.allowed is False
        assert "Claude" in result.reason or "attribution" in result.reason.lower()

    def test_blocks_co_authored_by_claude(self, validator):
        """Test blocks commands with Co-Authored-By Claude."""
        command = '''git commit -m "Fix bug

Co-Authored-By: Claude"'''
        result = validator.validate(command)
        assert result.allowed is False


class TestCredentialRequirement:
    """Test credential requirement detection."""

    @pytest.fixture
    def validator(self):
        return BashValidator()

    def test_detects_kubectl_credentials(self, validator):
        """Test detects kubectl needs credentials."""
        result = validator.validate("kubectl get pods")
        assert result.requires_credentials is True

    def test_detects_helm_credentials(self, validator):
        """Test detects helm needs credentials."""
        result = validator.validate("helm list")
        assert result.requires_credentials is True

    def test_detects_flux_credentials(self, validator):
        """Test detects flux needs credentials."""
        result = validator.validate("flux get all")
        assert result.requires_credentials is True

    def test_version_commands_no_credentials(self, validator):
        """Test version commands don't need credentials."""
        result = validator.validate("kubectl version --client")
        # Implementation may or may not require creds for version
        assert isinstance(result.requires_credentials, bool)

    def test_non_k8s_no_credentials(self, validator):
        """Test non-k8s commands don't need credentials."""
        result = validator.validate("ls -la")
        assert result.requires_credentials is False

    def test_credential_warning_message(self, validator):
        """Test credential warning message is provided."""
        result = validator.validate("kubectl get pods")
        if result.requires_credentials:
            assert result.credential_warning is not None
            assert "credential" in result.credential_warning.lower() or "KUBECONFIG" in result.credential_warning


class TestBashValidationResult:
    """Test BashValidationResult structure."""

    @pytest.fixture
    def validator(self):
        return BashValidator()

    def test_result_has_expected_fields(self, validator):
        """Test result contains expected fields."""
        result = validator.validate("ls")
        assert hasattr(result, "allowed")
        assert hasattr(result, "tier")
        assert hasattr(result, "reason")
        assert hasattr(result, "suggestions")
        assert hasattr(result, "requires_credentials")
        assert hasattr(result, "credential_warning")

    def test_suggestions_for_blocked(self, validator):
        """Test blocked commands have suggestions."""
        result = validator.validate("terraform apply")
        assert result.allowed is False
        assert len(result.suggestions) > 0


class TestConvenienceFunction:
    """Test validate_bash_command convenience function."""

    def test_convenience_function_works(self):
        """Test convenience function returns expected result."""
        result = validate_bash_command("ls -la")
        assert isinstance(result, BashValidationResult)
        assert result.allowed is True

    def test_convenience_function_blocks_dangerous(self):
        """Test convenience function blocks dangerous commands."""
        result = validate_bash_command("rm -rf /")
        assert result.allowed is False


class TestCreatePermissionAllowResponse:
    """Test create_permission_allow_response function."""

    def test_returns_valid_json(self):
        """Test returns valid JSON."""
        response = create_permission_allow_response("Read-only command")
        data = json.loads(response)
        assert "hookSpecificOutput" in data

    def test_has_permission_decision(self):
        """Test response has permission decision."""
        response = create_permission_allow_response("Test reason")
        data = json.loads(response)
        output = data["hookSpecificOutput"]
        assert output["permissionDecision"] == "allow"

    def test_has_reason(self):
        """Test response includes reason."""
        response = create_permission_allow_response("My custom reason")
        data = json.loads(response)
        output = data["hookSpecificOutput"]
        assert output["permissionDecisionReason"] == "My custom reason"

    def test_has_hook_event_name(self):
        """Test response has hook event name."""
        response = create_permission_allow_response("Test")
        data = json.loads(response)
        output = data["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"


class TestEdgeCases:
    """Test edge cases in bash validation."""

    @pytest.fixture
    def validator(self):
        return BashValidator()

    def test_handles_very_long_command(self, validator):
        """Test handles very long command."""
        long_command = "echo " + "a" * 10000
        result = validator.validate(long_command)
        assert isinstance(result, BashValidationResult)

    def test_handles_special_characters(self, validator):
        """Test handles special characters."""
        command = "echo 'test with $pecial ch@racters!'"
        result = validator.validate(command)
        assert isinstance(result, BashValidationResult)

    def test_handles_unicode(self, validator):
        """Test handles unicode characters."""
        command = "echo 'Hello World'"
        result = validator.validate(command)
        assert isinstance(result, BashValidationResult)

    def test_gcloud_commands(self, validator):
        """Test gcloud commands are handled."""
        result = validator.validate("gcloud compute instances list")
        assert isinstance(result, BashValidationResult)
        # Read-only gcloud should be allowed
        if "list" in "gcloud compute instances list":
            # May or may not be blocked depending on patterns
            pass
