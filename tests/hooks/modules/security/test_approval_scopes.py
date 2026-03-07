#!/usr/bin/env python3
"""Tests for explicit approval scope signatures."""

import sys
from pathlib import Path

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from modules.security.approval_scopes import (
    SCOPE_EXACT_COMMAND,
    SCOPE_RESOURCE_FAMILY,
    SCOPE_SEMANTIC_SIGNATURE,
    build_approval_signature,
    matches_approval_signature,
)


class TestApprovalScopes:
    """Approval matching should be explicit and predictable."""

    def test_exact_command_matches_same_tokenized_command(self):
        signature = build_approval_signature(
            'git commit -m "feat: add login"',
            scope_type=SCOPE_EXACT_COMMAND,
        )
        assert signature is not None
        assert matches_approval_signature(signature, "git   commit   -m 'feat: add login'")

    def test_exact_command_rejects_changed_argument(self):
        signature = build_approval_signature(
            'git commit -m "feat: add login"',
            scope_type=SCOPE_EXACT_COMMAND,
        )
        assert signature is not None
        assert not matches_approval_signature(signature, 'git commit -m "feat: change copy"')

    def test_semantic_signature_rejects_cross_cli_same_verb(self):
        signature = build_approval_signature(
            "terraform apply prod/vpc",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
        )
        assert signature is not None
        assert not matches_approval_signature(signature, "kubectl apply -f prod.yaml")

    def test_semantic_signature_rejects_more_dangerous_flag_variant(self):
        signature = build_approval_signature(
            "git push origin main",
            scope_type=SCOPE_SEMANTIC_SIGNATURE,
        )
        assert signature is not None
        assert not matches_approval_signature(signature, "git push origin main --force")

    def test_resource_family_allows_explicit_same_family(self):
        signature = build_approval_signature(
            "git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
        )
        assert signature is not None
        assert matches_approval_signature(signature, 'git commit -m "feat: add login"')

    def test_resource_family_rejects_different_verb(self):
        signature = build_approval_signature(
            "git commit",
            scope_type=SCOPE_RESOURCE_FAMILY,
        )
        assert signature is not None
        assert not matches_approval_signature(signature, "git push origin main")
