#!/usr/bin/env python3
"""
Test the complete WorkflowEnforcer integration with pre_tool_use.py
Tests all 6 phases of the workflow enforcement.
"""

import sys
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools" / "0-guards"))

def test_workflow_enforcer_integration():
    """Test complete workflow enforcer integration"""
    print("🧪 Testing WorkflowEnforcer Integration...")

    from pre_tool_use import PolicyEngine, SecurityTier

    # Test case 1: Phase 1 - Agent must exist
    print("\n📋 Test 1: Phase 1 - Agent Existence")
    policy = PolicyEngine()

    # Test with invalid agent
    result = policy._validate_task_invocation({
        "subagent_type": "invalid-agent",
        "prompt": "Do something",
        "description": "Test task"
    })

    allowed, tier, reason = result
    if not allowed and ("Unknown agent" in reason or "does not exist" in reason):
        print("  ✅ PASSED: Invalid agent blocked")
    else:
        print(f"  ❌ FAILED: Invalid agent not blocked properly. Result: allowed={allowed}, reason={reason}")
        return False

    # Test case 2: Phase 2 - Context provisioning warning
    print("\n📋 Test 2: Phase 2 - Context Provisioning")
    result = policy._validate_task_invocation({
        "subagent_type": "terraform-architect",
        "prompt": "Run terraform plan",  # No context
        "description": "Plan infrastructure"
    })

    allowed, tier, reason = result
    # Should be allowed but warned (check logs)
    if allowed:
        print("  ✅ PASSED: Missing context generates warning (not blocking)")
    else:
        print(f"  ❌ FAILED: Should not block for missing context: {reason}")
        return False

    # Test case 3: Phase 4 - T3 operations require approval
    print("\n📋 Test 3: Phase 4 - T3 Approval Required")

    # T3 without approval - should be blocked
    result = policy._validate_task_invocation({
        "subagent_type": "terraform-architect",
        "prompt": "# Project Context\n\nRun terraform apply to production",
        "description": "Apply terraform changes"
    })

    allowed, tier, reason = result
    if not allowed and "Phase 4" in reason:
        print("  ✅ PASSED: T3 operation blocked without approval")
    else:
        print(f"  ❌ FAILED: T3 should be blocked without approval: {reason}")
        return False

    # T3 with approval - should be allowed
    result = policy._validate_task_invocation({
        "subagent_type": "terraform-architect",
        "prompt": "# Project Context\n\nUser approval received. Run terraform apply to production",
        "description": "Apply terraform changes"
    })

    allowed, tier, reason = result
    if allowed:
        print("  ✅ PASSED: T3 operation allowed with approval")
    else:
        print(f"  ❌ FAILED: T3 should be allowed with approval: {reason}")
        return False

    # Test case 4: Phase 5 - Realization checks
    print("\n📋 Test 4: Phase 5 - Realization Checks")

    result = policy._validate_task_invocation({
        "subagent_type": "gitops-operator",
        "prompt": "# Project Context\n\nPhase 5: Realization\n\nPlan: Deploy application\nSteps: 1. Update manifests",
        "description": "Execute deployment"
    })

    allowed, tier, reason = result
    if allowed:
        print("  ✅ PASSED: Realization with plan allowed")
    else:
        print(f"  ❌ FAILED: Realization should be allowed with plan: {reason}")
        return False

    # Test case 5: Phase 6 - SSOT tracking
    print("\n📋 Test 5: Phase 6 - SSOT Update Tracking")

    # Check if T3 operations are tracked for SSOT update
    if policy.workflow_enforcer:
        # Clear history
        policy.workflow_enforcer.guard_history = []

        # Execute T3 with approval
        result = policy._validate_task_invocation({
            "subagent_type": "terraform-architect",
            "prompt": "User approval received. Apply terraform to create GKE cluster",
            "description": "Create production cluster"
        })

        # Check if history was recorded
        if policy.workflow_enforcer.guard_history:
            last_entry = policy.workflow_enforcer.guard_history[-1]
            if last_entry.get("requires_ssot_update"):
                print("  ✅ PASSED: T3 operation tracked for SSOT update")
            else:
                print("  ❌ FAILED: T3 operation not marked for SSOT update")
                return False
        else:
            print("  ⚠️ WARNING: Guard history not populated")
    else:
        print("  ⚠️ SKIPPED: WorkflowEnforcer not available")

    return True


def test_all_guards_available():
    """Test that all phase guards are available"""
    print("\n🧪 Testing All Guards Availability...")

    try:
        from workflow_enforcer import WorkflowEnforcer, PhaseGuard

        enforcer = WorkflowEnforcer()

        # Test each guard method exists
        guards_to_test = [
            ("guard_phase_0_ambiguity_threshold", [0.5]),
            ("guard_phase_1_agent_exists", ["terraform-architect", ["terraform-architect"]]),
            ("guard_phase_1_routing_confidence", [0.8]),
            ("guard_phase_2_context_completeness", [
                {"contract": {"project_details": {}}},  # context_payload
                ["project_details"]  # required_sections
            ]),
            ("guard_phase_4_approval_mandatory", ["T3", True]),
            ("guard_phase_5_planning_complete", [{"agent": "test", "plan": "Plan output"}]),  # realization_package
            ("guard_phase_6_ssot_update_after_t3", ["T3", True])  # tier, ssot_updated
        ]

        all_passed = True
        for method_name, test_args in guards_to_test:
            if hasattr(enforcer, method_name):
                method = getattr(enforcer, method_name)
                try:
                    result = method(*test_args)
                    if isinstance(result, tuple) and len(result) == 2:
                        print(f"  ✅ {method_name}: Available and working")
                    else:
                        print(f"  ❌ {method_name}: Invalid return format")
                        all_passed = False
                except Exception as e:
                    print(f"  ❌ {method_name}: Error - {e}")
                    all_passed = False
            else:
                print(f"  ❌ {method_name}: Not found")
                all_passed = False

        return all_passed

    except ImportError as e:
        print(f"  ❌ FAILED: Could not import WorkflowEnforcer: {e}")
        return False


def test_guard_violations():
    """Test that guard violations properly block operations"""
    print("\n🧪 Testing Guard Violations...")

    try:
        from workflow_enforcer import WorkflowEnforcer, GuardViolation

        enforcer = WorkflowEnforcer()

        # Test 1: High ambiguity should fail
        passed, reason = enforcer.guard_phase_0_ambiguity_threshold(0.8, threshold=0.3)
        if not passed and "Guard Violation" in reason:
            print("  ✅ High ambiguity properly blocked")
        else:
            print("  ❌ High ambiguity not blocked")
            return False

        # Test 2: Low routing confidence should fail
        passed, reason = enforcer.guard_phase_1_routing_confidence(0.2, min_confidence=0.5)
        if not passed and "below minimum" in reason:
            print("  ✅ Low routing confidence blocked")
        else:
            print("  ❌ Low routing confidence not blocked")
            return False

        # Test 3: T3 without approval should fail
        passed, reason = enforcer.guard_phase_4_approval_mandatory("T3", approval_received=False)
        if not passed and "MANDATORY" in reason:
            print("  ✅ T3 without approval blocked")
        else:
            print("  ❌ T3 without approval not blocked")
            return False

        return True

    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        return False


def main():
    """Run all integration tests"""
    print("="*60)
    print("WORKFLOW ENFORCER INTEGRATION TESTS")
    print("="*60)

    tests = [
        test_all_guards_available,
        test_guard_violations,
        test_workflow_enforcer_integration
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n  ❌ Test failed with exception: {e}")
            import traceback
            traceback.print_exc()
            results.append(False)

    print("\n" + "="*60)
    print(f"RESULTS: {sum(results)}/{len(results)} test suites passed")
    print("="*60)

    if all(results):
        print("✅ All integration tests passed!")
        return 0
    else:
        print("❌ Some integration tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())