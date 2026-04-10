#!/usr/bin/env python3
"""
Tests for blocked_message_formatter module.

Validates:
1. format_blocked_message() produces actionable output for known categories
2. Output includes domain reason and agent routing per CATEGORY_AGENT_MAP
3. Unknown/empty categories fall back gracefully (no agent line)
4. None/empty inputs do not crash
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Add hooks to path
HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.blocked_message_formatter import (
    CATEGORY_AGENT_MAP,
    format_blocked_message,
    _extract_category,
    _get_suggestion,
)


# ============================================================================
# HELPERS
# ============================================================================

def _make_result(category: str, suggestions=None):
    """Build a minimal result object mimicking BashValidationResult."""
    return SimpleNamespace(
        reason=f"Command blocked by security policy: {category}",
        suggestions=suggestions or [],
    )


def _make_result_raw(reason: str, suggestions=None):
    """Build a result with an arbitrary reason string."""
    return SimpleNamespace(
        reason=reason,
        suggestions=suggestions or [],
    )


# ============================================================================
# CATEGORY_AGENT_MAP STRUCTURE
# ============================================================================

class TestCategoryAgentMap:
    """Verify the mapping structure is well-formed."""

    def test_map_is_non_empty(self):
        assert len(CATEGORY_AGENT_MAP) > 0

    def test_all_values_are_strings(self):
        for category, agent in CATEGORY_AGENT_MAP.items():
            assert isinstance(category, str), f"Key {category!r} is not a string"
            assert isinstance(agent, str), f"Value for {category!r} is not a string"

    def test_expected_categories_present(self):
        expected = {"aws_critical", "kubernetes_critical", "git_destructive"}
        actual = set(CATEGORY_AGENT_MAP.keys())
        missing = expected - actual
        assert not missing, f"Expected categories missing from CATEGORY_AGENT_MAP: {missing}"


# ============================================================================
# format_blocked_message WITH KNOWN CATEGORIES
# ============================================================================

class TestFormatBlockedMessageKnownCategories:
    """Test format_blocked_message() with categories from CATEGORY_AGENT_MAP."""

    @pytest.mark.parametrize("category,expected_agent", [
        ("aws_critical", "terraform-architect"),
        ("gcp_critical", "terraform-architect"),
        ("kubernetes_critical", "gitops-operator"),
        ("flux_critical", "gitops-operator"),
        ("git_destructive", "developer"),
        ("docker_critical", "developer"),
        ("npm_critical", "developer"),
        ("sql_critical", "developer"),
        ("disk_operations", "developer"),
        ("rm_critical", "developer"),
        ("repo_delete", "developer"),
    ])
    def test_known_category_includes_agent_routing(self, category, expected_agent):
        """Each known category must produce a Dispatch-to line with the correct agent."""
        result = _make_result(category)
        msg = format_blocked_message(result)
        assert f"Dispatch to: {expected_agent}" in msg

    @pytest.mark.parametrize("category", [
        "aws_critical",
        "kubernetes_critical",
        "git_destructive",
    ])
    def test_output_includes_blocked_marker(self, category):
        """Output must start with [BLOCKED] marker."""
        result = _make_result(category)
        msg = format_blocked_message(result)
        assert msg.startswith("[BLOCKED]")

    @pytest.mark.parametrize("category", [
        "aws_critical",
        "kubernetes_critical",
        "git_destructive",
    ])
    def test_output_includes_reason(self, category):
        """Output must include the original reason text."""
        result = _make_result(category)
        msg = format_blocked_message(result)
        assert f"Command blocked by security policy: {category}" in msg

    @pytest.mark.parametrize("category", [
        "aws_critical",
        "kubernetes_critical",
        "git_destructive",
    ])
    def test_output_includes_irreversible_marker(self, category):
        """Output must note the operation is irreversible."""
        result = _make_result(category)
        msg = format_blocked_message(result)
        assert "irreversible" in msg.lower()


# ============================================================================
# SUGGESTION HANDLING
# ============================================================================

class TestSuggestionHandling:
    """Verify suggestion line behavior."""

    def test_explicit_suggestion_from_result(self):
        """When result has suggestions, the first one is used."""
        result = _make_result("aws_critical", suggestions=["Use IaC tool instead"])
        msg = format_blocked_message(result)
        assert "Suggestion: Use IaC tool instead" in msg

    def test_fallback_suggestion_for_known_category(self):
        """When no suggestions in result but category is known, category-based fallback is used."""
        result = _make_result("aws_critical", suggestions=[])
        msg = format_blocked_message(result)
        assert "Suggestion:" in msg
        assert "aws_critical" in msg

    def test_fallback_suggestion_for_unknown_category(self):
        """When category is unknown and no suggestions, reason is used as fallback."""
        result = _make_result_raw("Some unknown reason", suggestions=[])
        msg = format_blocked_message(result)
        assert "Suggestion: Some unknown reason" in msg


# ============================================================================
# UNKNOWN / EMPTY CATEGORY FALLBACK
# ============================================================================

class TestUnknownCategoryFallback:
    """Test graceful fallback for unknown or empty categories."""

    def test_unknown_category_no_dispatch_line(self):
        """An unrecognized category should not produce a Dispatch-to line."""
        result = _make_result("totally_unknown_category")
        msg = format_blocked_message(result)
        assert "Dispatch to:" not in msg

    def test_reason_without_category_prefix(self):
        """A reason that does not follow the standard format should still produce output."""
        result = _make_result_raw("Something went wrong")
        msg = format_blocked_message(result)
        assert "[BLOCKED]" in msg
        assert "Dispatch to:" not in msg

    def test_empty_reason_string(self):
        """An empty reason string should not crash."""
        result = _make_result_raw("")
        msg = format_blocked_message(result)
        assert "[BLOCKED]" in msg


# ============================================================================
# NONE / EMPTY INPUTS -- ROBUSTNESS
# ============================================================================

class TestRobustness:
    """Ensure None/empty inputs do not crash."""

    def test_result_with_none_suggestions(self):
        """Suggestions=None should not crash."""
        result = SimpleNamespace(
            reason="Command blocked by security policy: aws_critical",
            suggestions=None,
        )
        msg = format_blocked_message(result)
        assert "[BLOCKED]" in msg

    def test_result_with_empty_suggestions_list(self):
        """Empty suggestions list should not crash."""
        result = _make_result("kubernetes_critical", suggestions=[])
        msg = format_blocked_message(result)
        assert "[BLOCKED]" in msg


# ============================================================================
# _extract_category INTERNAL HELPER
# ============================================================================

class TestExtractCategory:
    """Unit tests for _extract_category helper."""

    def test_standard_format(self):
        assert _extract_category("Command blocked by security policy: aws_critical") == "aws_critical"

    def test_no_prefix_returns_none(self):
        assert _extract_category("Some other reason") is None

    def test_empty_string_returns_none(self):
        assert _extract_category("") is None

    def test_category_with_trailing_whitespace(self):
        result = _extract_category("Command blocked by security policy:  git_destructive  ")
        assert result == "git_destructive"


# ============================================================================
# _get_suggestion INTERNAL HELPER
# ============================================================================

class TestGetSuggestion:
    """Unit tests for _get_suggestion helper."""

    def test_prefers_result_suggestions(self):
        result = SimpleNamespace(reason="any", suggestions=["Use X instead"])
        assert _get_suggestion(result, "aws_critical") == "Use X instead"

    def test_fallback_to_category_message(self):
        result = SimpleNamespace(reason="any", suggestions=[])
        suggestion = _get_suggestion(result, "kubernetes_critical")
        assert "kubernetes_critical" in suggestion

    def test_fallback_to_reason_when_no_category(self):
        result = SimpleNamespace(reason="Some reason text", suggestions=[])
        assert _get_suggestion(result, None) == "Some reason text"

    def test_none_suggestions_uses_category_fallback(self):
        result = SimpleNamespace(reason="any", suggestions=None)
        suggestion = _get_suggestion(result, "git_destructive")
        assert "git_destructive" in suggestion
