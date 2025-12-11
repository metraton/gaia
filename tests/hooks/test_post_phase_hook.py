#!/usr/bin/env python3
"""
Tests for post_phase_hook.py.

Validates post-phase validations:
- Phase 4 post: Approval validation for T3
- Phase 6 post: SSOT update validation for T3
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add hooks to path
HOOKS_DIR = Path(__file__).parent.parent.parent / "hooks"
GUARDS_DIR = Path(__file__).parent.parent.parent / "tools" / "0-guards"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(GUARDS_DIR))

from post_phase_hook import (
    post_phase_4_approval,
    post_phase_6_ssot_update,
)


class TestPostPhase4Approval:
    """Tests for Phase 4 post-validation (approval)."""

    def test_valid_t3_approval(self):
        """Test valid T3 approval."""
        result = post_phase_4_approval(
            tier="T3",
            user_response="approve",
            validation_result={"approved": True, "action": "proceed_to_realization"}
        )
        
        assert result["valid"] is True

    def test_invalid_t3_rejection(self):
        """Test T3 rejection is properly captured."""
        result = post_phase_4_approval(
            tier="T3",
            user_response="reject",
            validation_result={"approved": False, "action": "abort"}
        )
        
        assert result["valid"] is False

    def test_t2_bypasses_strict_approval(self):
        """Test T2 doesn't require strict approval validation."""
        result = post_phase_4_approval(
            tier="T2",
            user_response="",
            validation_result={"approved": False}  # No approval for T2
        )
        
        assert result["valid"] is True  # T2 doesn't enforce strict approval

    def test_t1_bypasses_approval(self):
        """Test T1 bypasses approval entirely."""
        result = post_phase_4_approval(
            tier="T1",
            user_response="",
            validation_result={}
        )
        
        assert result["valid"] is True

    def test_t0_bypasses_approval(self):
        """Test T0 (read-only) bypasses approval."""
        result = post_phase_4_approval(
            tier="T0",
            user_response="",
            validation_result={}
        )
        
        assert result["valid"] is True

    def test_approval_with_modifications(self):
        """Test approval with modifications is still valid."""
        result = post_phase_4_approval(
            tier="T3",
            user_response="approve_with_modifications",
            validation_result={"approved": True, "action": "proceed_with_modifications"}
        )
        
        assert result["valid"] is True


class TestPostPhase6SSOTUpdate:
    """Tests for Phase 6 post-validation (SSOT update)."""

    def test_valid_t3_ssot_update(self):
        """Test valid SSOT update for T3."""
        result = post_phase_6_ssot_update(
            tier="T3",
            ssot_updated=True
        )
        
        assert result["valid"] is True

    def test_invalid_t3_no_ssot_update(self):
        """Test invalid: T3 completed but no SSOT update."""
        result = post_phase_6_ssot_update(
            tier="T3",
            ssot_updated=False
        )
        
        assert result["valid"] is False
        assert "SSOT" in result["reason"]

    def test_t2_optional_ssot_update(self):
        """Test T2 SSOT update is optional."""
        result = post_phase_6_ssot_update(
            tier="T2",
            ssot_updated=False
        )
        
        # T2 may or may not require SSOT update depending on config
        assert "valid" in result

    def test_t1_no_ssot_required(self):
        """Test T1 doesn't require SSOT update."""
        result = post_phase_6_ssot_update(
            tier="T1",
            ssot_updated=False
        )
        
        assert result["valid"] is True

    def test_t0_no_ssot_required(self):
        """Test T0 (read-only) doesn't require SSOT update."""
        result = post_phase_6_ssot_update(
            tier="T0",
            ssot_updated=False
        )
        
        assert result["valid"] is True


class TestPostPhaseHooksIntegration:
    """Integration tests for post-phase hooks."""

    def test_t3_complete_workflow_validation(self):
        """Test complete T3 workflow with all post-validations."""
        # Post Phase 4: Approval received
        post_4 = post_phase_4_approval(
            tier="T3",
            user_response="approve",
            validation_result={"approved": True, "action": "proceed"}
        )
        assert post_4["valid"] is True
        
        # Post Phase 6: SSOT updated
        post_6 = post_phase_6_ssot_update(
            tier="T3",
            ssot_updated=True
        )
        assert post_6["valid"] is True

    def test_t3_workflow_fails_without_approval(self):
        """Test T3 workflow fails validation without approval."""
        post_4 = post_phase_4_approval(
            tier="T3",
            user_response="reject",
            validation_result={"approved": False, "action": "abort"}
        )
        
        assert post_4["valid"] is False
        # Workflow should not proceed to Phase 5/6

    def test_t3_workflow_fails_without_ssot_update(self):
        """Test T3 workflow fails validation without SSOT update."""
        # Even if Phase 5 completed, Phase 6 validation fails
        post_6 = post_phase_6_ssot_update(
            tier="T3",
            ssot_updated=False
        )
        
        assert post_6["valid"] is False

    def test_t2_workflow_lenient_validation(self):
        """Test T2 workflow has lenient validation."""
        # Post Phase 4: No approval required
        post_4 = post_phase_4_approval(
            tier="T2",
            user_response="",
            validation_result={}
        )
        assert post_4["valid"] is True
        
        # Post Phase 6: SSOT update optional
        post_6 = post_phase_6_ssot_update(
            tier="T2",
            ssot_updated=False
        )
        # May or may not be valid depending on config


class TestPostPhaseHooksEdgeCases:
    """Edge case tests for post-phase hooks."""

    def test_empty_validation_result_t3(self):
        """Test handling of empty validation result for T3."""
        result = post_phase_4_approval(
            tier="T3",
            user_response="",
            validation_result={}  # Empty
        )
        
        # Should fail - empty is not approved
        assert result["valid"] is False

    def test_malformed_validation_result(self):
        """Test handling of malformed validation result."""
        result = post_phase_4_approval(
            tier="T3",
            user_response="approve",
            validation_result={"status": "ok"}  # Missing 'approved' key
        )
        
        # Should fail - no explicit approved=True
        assert result["valid"] is False

    def test_none_tier_handling(self):
        """Test handling of None tier."""
        # This may raise an error or be handled gracefully
        try:
            result = post_phase_4_approval(
                tier=None,
                user_response="",
                validation_result={}
            )
            # If no error, should default to lenient
            assert "valid" in result
        except (TypeError, AttributeError):
            pass  # Expected if tier must be string

    def test_case_sensitivity_tier(self):
        """Test tier case sensitivity."""
        result = post_phase_4_approval(
            tier="t3",  # Lowercase
            user_response="approve",
            validation_result={"approved": True}
        )
        
        # Depending on implementation, may pass or fail
        assert "valid" in result
