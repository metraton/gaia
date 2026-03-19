#!/usr/bin/env python3
"""Tests for SendMessage resume approval handling and classify_resume_prompt edge cases.

Validates:
- Approval handling integration via _handle_send_message
- Parametrized _classify_resume_prompt edge cases (empty, long, special chars, boundaries)
"""

import sys
from pathlib import Path

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import pre_tool_use
from modules.security.prompt_validator import classify_resume_prompt
from modules.security.approval_constants import (
    NONCE_APPROVAL_PREFIX,
    DEPRECATED_APPROVAL_PHRASES,
)
from modules.security.approval_grants import (
    ApprovalActivationResult,
    ACTIVATION_ACTIVATED,
    ACTIVATION_EXPIRED,
)
from modules.core.paths import clear_path_cache


@pytest.fixture(autouse=True)
def isolated_session(tmp_path, monkeypatch):
    clear_path_cache()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    yield
    clear_path_cache()


@pytest.fixture
def saved_states(monkeypatch):
    """Capture pre-hook states without writing to disk."""
    captured = []

    def _save(state):
        captured.append(state)
        return True

    monkeypatch.setattr(pre_tool_use, "save_hook_state", _save)
    return captured


class TestHandleSendMessageApproval:
    """Approval handling for SendMessage resume should be fail-closed and coherent."""

    def test_valid_nonce_allows_resume_and_marks_approval(self, monkeypatch, saved_states):
        nonce = "deadbeef" * 4
        monkeypatch.setattr(
            pre_tool_use,
            "activate_pending_approval",
            lambda value: ApprovalActivationResult(
                success=True,
                status=ACTIVATION_ACTIVATED,
                reason="Pending approval activated.",
                grant_path=Path("/tmp/grant-test.json"),
            ),
        )

        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": f"APPROVE:{nonce}"},
        )

        assert result is None
        assert len(saved_states) == 1
        assert saved_states[0].metadata["has_approval"] is True

    def test_failed_nonce_activation_denies_resume_and_skips_state(self, monkeypatch, saved_states):
        nonce = "deadbeef" * 4
        monkeypatch.setattr(
            pre_tool_use,
            "activate_pending_approval",
            lambda value: ApprovalActivationResult(
                success=False,
                status=ACTIVATION_EXPIRED,
                reason="Approval nonce expired before activation.",
            ),
        )

        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": f"APPROVE:{nonce}"},
        )

        assert isinstance(result, str)
        assert "Approval activation failed" in result
        assert "Status: expired" in result
        assert "expired" in result.lower()
        assert saved_states == []

    def test_malformed_nonce_token_denies_resume_and_skips_state(self, saved_states):
        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": "APPROVE:commit\n\nRetry the git commit."},
        )

        assert isinstance(result, str)
        assert "Invalid approval token" in result
        assert "APPROVE:commit is invalid" in result
        assert saved_states == []

    def test_deprecated_approval_phrase_denies_resume_and_skips_state(self, saved_states):
        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": "User approved: terraform apply prod/vpc"},
        )

        assert isinstance(result, str)
        assert "Deprecated approval format" in result
        assert saved_states == []

    def test_documentary_nonce_text_is_not_treated_as_malformed_approval(self, saved_states):
        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": "Use APPROVE:<nonce> in the docs and continue."},
        )

        assert result is None
        assert len(saved_states) == 1
        assert saved_states[0].metadata["has_approval"] is False

    def test_resume_without_approval_token_allows_and_marks_false(self, saved_states):
        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": "Continue with the investigation."},
        )

        assert result is None
        assert len(saved_states) == 1
        assert saved_states[0].metadata["has_approval"] is False

    def test_invalid_agent_id_blocked(self, saved_states):
        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "invalid_id", "message": "Continue."},
        )

        assert isinstance(result, str)
        assert "Invalid agent ID format" in result
        assert saved_states == []

    def test_empty_message_blocked(self, saved_states):
        result = pre_tool_use._handle_send_message(
            "SendMessage",
            {"to": "a12345", "message": ""},
        )

        assert isinstance(result, str)
        assert "SendMessage requires a message" in result
        assert saved_states == []


# ============================================================================
# PARAMETRIZED _classify_resume_prompt EDGE CASES
# ============================================================================

class TestClassifyResumePromptEmpty:
    """Empty and whitespace-only prompts."""

    @pytest.mark.parametrize("prompt", [
        "",
        "   ",
        "\n",
        "\t\n  \t",
    ], ids=["empty", "spaces", "newline", "mixed_whitespace"])
    def test_empty_or_whitespace_returns_standard(self, prompt):
        assert classify_resume_prompt(prompt) == "standard"


class TestClassifyResumePromptNonce:
    """Valid nonce patterns and their boundaries."""

    def test_valid_32_hex_nonce(self):
        nonce = "a" * 32
        assert classify_resume_prompt(f"APPROVE:{nonce}") == "nonce"

    def test_valid_nonce_mixed_hex(self):
        assert classify_resume_prompt("APPROVE:deadbeefdeadbeefdeadbeefdeadbeef") == "nonce"

    def test_valid_nonce_embedded_in_text(self):
        nonce = "0123456789abcdef" * 2
        prompt = f"I confirm. APPROVE:{nonce} Please proceed."
        assert classify_resume_prompt(prompt) == "nonce"

    def test_nonce_31_chars_is_malformed(self):
        """31-char hex is not a valid 32-char nonce -- prefix matches, nonce does not."""
        short_nonce = "a" * 31
        assert classify_resume_prompt(f"APPROVE:{short_nonce}") == "malformed_nonce"

    def test_nonce_33_chars_is_still_nonce(self):
        """33-char hex: the first 32 match the nonce regex, so it classifies as nonce."""
        long_nonce = "a" * 33
        # The regex \bAPPROVE:([a-f0-9]{32})\b will match the first 32 chars
        # IF the 33rd char is not a word char. Since 'a' IS a word char,
        # the \b boundary fails, so this is actually malformed_nonce.
        result = classify_resume_prompt(f"APPROVE:{long_nonce}")
        assert result == "malformed_nonce"

    def test_nonce_with_uppercase_hex_is_malformed(self):
        """Pattern requires lowercase hex only."""
        upper_nonce = "DEADBEEF" * 4
        assert classify_resume_prompt(f"APPROVE:{upper_nonce}") == "malformed_nonce"

    def test_nonce_with_non_hex_chars_is_malformed(self):
        almost_nonce = "g" * 32
        assert classify_resume_prompt(f"APPROVE:{almost_nonce}") == "malformed_nonce"


class TestClassifyResumePromptMalformed:
    """APPROVE: prefix present but nonce is invalid."""

    def test_approve_with_word_is_malformed(self):
        assert classify_resume_prompt("APPROVE:commit") == "malformed_nonce"

    def test_approve_with_empty_value_is_malformed(self):
        """APPROVE: followed by nothing (or whitespace) -- prefix starts the stripped prompt."""
        assert classify_resume_prompt("APPROVE:") == "malformed_nonce"

    def test_approve_with_command_name_is_malformed(self):
        assert classify_resume_prompt("APPROVE:terraform apply prod/vpc") == "malformed_nonce"

    def test_approve_prefix_not_at_start_is_not_malformed(self):
        """APPROVE: in the middle of text does not trigger malformed_nonce
        (startswith check is on stripped prompt)."""
        result = classify_resume_prompt("The format is APPROVE:something but do not use it")
        assert result == "standard"

    def test_documentary_nonce_placeholder_is_standard(self):
        """APPROVE:<nonce> is documentary -- angle brackets are not hex."""
        result = classify_resume_prompt("Use APPROVE:<nonce> in docs and continue.")
        assert result == "standard"


class TestClassifyResumePromptDeprecated:
    """Deprecated approval phrases."""

    @pytest.mark.parametrize("phrase", list(DEPRECATED_APPROVAL_PHRASES))
    def test_each_deprecated_phrase_detected(self, phrase):
        prompt = f"The agent said: {phrase} so we continue."
        assert classify_resume_prompt(prompt) == "deprecated"

    def test_deprecated_phrase_case_insensitive(self):
        assert classify_resume_prompt("USER APPROVED: terraform apply") == "deprecated"

    def test_deprecated_phrase_mixed_case(self):
        assert classify_resume_prompt("Approval Confirmed by the team.") == "deprecated"


class TestClassifyResumePromptStandard:
    """Normal prompts that should classify as standard."""

    @pytest.mark.parametrize("prompt", [
        "Continue with the investigation.",
        "Check the pods in the staging namespace.",
        "Run terraform plan on modules/vpc.",
        "Fix the failing CI pipeline.",
        "Look at the auth-service logs for errors.",
    ], ids=["continue", "check_pods", "tf_plan", "fix_ci", "check_logs"])
    def test_normal_prompts_are_standard(self, prompt):
        assert classify_resume_prompt(prompt) == "standard"


class TestClassifyResumePromptLong:
    """Very long prompts should not crash or misclassify."""

    def test_long_standard_prompt(self):
        prompt = "Continue investigating. " * 500  # ~11500 chars
        assert classify_resume_prompt(prompt) == "standard"

    def test_long_prompt_with_valid_nonce_at_end(self):
        padding = "Check the logs. " * 500
        nonce = "abcdef0123456789" * 2
        prompt = f"{padding}APPROVE:{nonce}"
        assert classify_resume_prompt(prompt) == "nonce"

    def test_long_prompt_with_deprecated_phrase_buried(self):
        padding = "More context here. " * 200
        prompt = f"{padding}The team said user approved: the change."
        assert classify_resume_prompt(prompt) == "deprecated"


class TestClassifyResumePromptSpecialChars:
    """Prompts with special characters, unicode, and unusual formatting."""

    @pytest.mark.parametrize("prompt", [
        "Continue\nwith\nmultiline\nprompt.",
        "Tab\there\tand\tthere.",
        "Emoji test: check the pods please.",
        "Path: /home/user/.claude/hooks/pre_tool_use.py",
        'JSON: {"key": "value", "nested": {"a": 1}}',
        "Backticks: `kubectl get pods -n staging`",
        "Regex-like: ^a[0-9]{5,}$ should not crash",
    ], ids=[
        "multiline", "tabs", "no_emoji", "path",
        "json_string", "backticks", "regex_like",
    ])
    def test_special_char_prompts_are_standard(self, prompt):
        assert classify_resume_prompt(prompt) == "standard"

    def test_prompt_with_newlines_before_approve_prefix(self):
        """APPROVE: not at start of stripped prompt -- standard."""
        prompt = "\n\nSome context\nAPPROVE:not_a_nonce"
        assert classify_resume_prompt(prompt) == "standard"

    def test_approve_prefix_after_leading_whitespace(self):
        """Stripped prompt starts with APPROVE: -- malformed_nonce."""
        prompt = "   APPROVE:badtoken"
        assert classify_resume_prompt(prompt) == "malformed_nonce"
