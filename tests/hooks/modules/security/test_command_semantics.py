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
