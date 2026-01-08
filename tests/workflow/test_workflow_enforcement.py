#!/usr/bin/env python3
"""
End-to-End test for workflow enforcement.

Tests complete workflow from Phase 0 to Phase 6 with guards.
"""

import sys
import json
from pathlib import Path
import pytest

# Add hooks to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))


class TestTaskValidation:
    """Test that Task tool invocations are validated"""
    
    def test_t3_without_approval_blocked(self):
        """Test 1: T3 operation without approval should be blocked"""
        from pre_tool_use import pre_tool_use_hook

        result = pre_tool_use_hook('Task', {
            'subagent_type': 'terraform-architect',
            'prompt': 'Run terraform apply in production',
            'description': 'Apply terraform changes'
        })

        assert result is not None, "T3 without approval should be blocked"
        # Check for any indication that the T3/approval was the issue
        assert "T3" in result or "approval" in result.lower() or "Phase 4" in result or "MANDATORY" in result, \
            f"Should have blocked T3 without approval. Got: {result}"

    def test_t3_with_approval_allowed(self):
        """Test 2: T3 operation with approval should pass"""
        from pre_tool_use import pre_tool_use_hook

        result = pre_tool_use_hook('Task', {
            'subagent_type': 'terraform-architect',
            'prompt': 'User approval received. Phase 5: Realization. Run terraform apply',
            'description': 'Apply terraform changes'
        })

        assert result is None or (result and "allowed" in result.lower()), \
            f"Should have allowed T3 with approval. Got: {result}"

    def test_non_t3_operation_allowed(self):
        """Test 3: Non-T3 operation should pass"""
        from pre_tool_use import pre_tool_use_hook

        result = pre_tool_use_hook('Task', {
            'subagent_type': 'cloud-troubleshooter',
            'prompt': 'Check cluster status',
            'description': 'Get cluster information'
        })

        assert result is None or (result and "allowed" in result.lower()), \
            f"Should have allowed non-T3. Got: {result}"

    def test_unknown_agent_blocked(self):
        """Test 4: Unknown agent should be blocked"""
        from pre_tool_use import pre_tool_use_hook

        result = pre_tool_use_hook('Task', {
            'subagent_type': 'unknown-agent',
            'prompt': 'Do something',
            'description': 'Test unknown agent'
        })

        assert result is not None, "Unknown agent should be blocked"
        # Check for indication that agent doesn't exist
        assert "unknown-agent" in result.lower() or "does not exist" in result.lower() or "agent" in result.lower(), \
            f"Should have blocked unknown agent. Got: {result}"


class TestBashValidation:
    """Ensure bash validation still works"""
    
    def test_terraform_apply_blocked(self):
        """Test that terraform apply is blocked"""
        from pre_tool_use import pre_tool_use_hook

        result = pre_tool_use_hook('Bash', {
            'command': 'terraform apply -auto-approve'
        })

        assert result is not None, "Bash should block terraform apply"
        assert "blocked" in result.lower() or "not allowed" in result.lower(), \
            f"Should have blocked terraform apply. Got: {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
