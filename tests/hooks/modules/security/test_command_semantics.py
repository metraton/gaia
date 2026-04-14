#!/usr/bin/env python3
"""Tests for semantic command analysis used by the security layer."""

import sys
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.command_semantics import (
    analyze_command,
    _contains_ordered_sequence,
    _is_short_value_flag,
)


class TestCommandSemantics:
    """Verify the semantic command view used by security checks."""

    def test_analysis_is_idempotent(self):
        command = "git -C repo push origin main --force"
        first = analyze_command(command)
        second = analyze_command(first.normalized_command)

        assert second.normalized_command == first.normalized_command
        assert second.semantic_tokens == first.semantic_tokens

    def test_normalizes_flags_with_and_without_equals(self):
        semantics = analyze_command(
            "aws --profile prod --region=us-east-1 ec2 delete-vpc --vpc-id vpc-123"
        )

        assert semantics.base_cmd == "aws"
        assert "--profile" in semantics.flag_tokens
        assert "--region" in semantics.flag_tokens
        assert "--vpc-id" in semantics.flag_tokens

    def test_semantic_tokens_keep_execution_command_untouched(self):
        command = "kubectl --context prod --namespace default delete namespace payments"
        semantics = analyze_command(command)

        assert semantics.raw_command == command
        assert semantics.base_cmd == "kubectl"
        assert "delete" in semantics.semantic_tokens
        assert "namespace" in semantics.semantic_tokens

    def test_ordered_sequence_matching_allows_gaps(self):
        semantics = analyze_command(
            "gcloud --project dev --configuration shared container clusters delete cluster-a"
        )
        assert _contains_ordered_sequence(
            semantics.semantic_head_tokens,
            ("gcloud", "container", "clusters", "delete"),
        )


class TestShortValueFlagAbsorption:
    """Verify that single-letter short flags before the subcommand absorb
    their value argument, keeping it out of semantic_tokens.

    This is the fix for the T3 relay bug: ``git -C /path push origin main``
    must produce the same semantic signature as ``git push origin main`` so
    that an approval grant for the latter matches the former.
    """

    def test_git_dash_c_path_absorbed(self):
        """git -C <path> push ... should have same semantic_tokens as git push ..."""
        base = analyze_command("git push origin main")
        with_c = analyze_command("git -C /home/jorge/ws/me push origin main")

        assert with_c.semantic_tokens == base.semantic_tokens
        assert with_c.base_cmd == "git"
        # The path should be in flag_tokens, not non_flag_tokens
        assert "/home/jorge/ws/me" in with_c.flag_tokens
        assert "/home/jorge/ws/me" not in with_c.non_flag_tokens

    def test_kubectl_dash_n_namespace_absorbed(self):
        """kubectl -n <ns> delete pod ... should match kubectl delete pod ..."""
        base = analyze_command("kubectl delete pod my-pod")
        with_n = analyze_command("kubectl -n production delete pod my-pod")

        assert with_n.semantic_tokens == base.semantic_tokens
        assert "production" in with_n.flag_tokens
        assert "production" not in with_n.non_flag_tokens

    def test_combined_flags_not_treated_as_value_consuming(self):
        """Combined flags like -rf should NOT absorb the next token."""
        semantics = analyze_command("tar -rf archive.tar newfile.txt")

        # -rf is a combined flag, NOT a single-letter value flag.
        # Both tokens after the flag should be in non_flag_tokens.
        assert "archive.tar" in semantics.non_flag_tokens
        assert "newfile.txt" in semantics.non_flag_tokens

    def test_flag_after_subcommand_does_not_absorb(self):
        """Short flags that appear AFTER a non-flag (subcommand) should
        NOT absorb the next token -- absorption only applies to global
        flags before the first positional argument."""
        semantics = analyze_command("git push -f origin main")

        # -f comes after "push" (a non-flag), so "origin" is NOT absorbed
        assert "push" in semantics.non_flag_tokens
        assert "origin" in semantics.non_flag_tokens
        assert "main" in semantics.non_flag_tokens

    def test_idempotency_with_absorbed_flag(self):
        """Analyzing the normalized_command of a -C command should be stable."""
        first = analyze_command("git -C /tmp/repo push origin main")
        second = analyze_command(first.normalized_command)

        assert second.semantic_tokens == first.semantic_tokens
        assert second.normalized_command == first.normalized_command

    def test_is_short_value_flag_helper(self):
        """Direct tests for the _is_short_value_flag helper."""
        # Single-letter short flags
        assert _is_short_value_flag("-C") is True
        assert _is_short_value_flag("-n") is True
        assert _is_short_value_flag("-f") is True

        # Combined flags -- NOT single-letter
        assert _is_short_value_flag("-rf") is False
        assert _is_short_value_flag("-av") is False

        # Long flags -- excluded
        assert _is_short_value_flag("--chdir") is False
        assert _is_short_value_flag("--namespace") is False

        # Not flags at all
        assert _is_short_value_flag("push") is False
        assert _is_short_value_flag("-") is False
        assert _is_short_value_flag("") is False
