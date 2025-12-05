#!/usr/bin/env python3
"""
End-to-End test for workflow enforcement.

Tests complete workflow from Phase 0 to Phase 6 with guards.
"""

import sys
import json
from pathlib import Path

# Add hooks to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "hooks"))

def test_task_validation():
    """Test that Task tool invocations are validated"""
    print("🧪 Testing Task Tool Validation...")

    from pre_tool_use import pre_tool_use_hook

    # Test 1: T3 operation without approval should be blocked
    print("\n  Test 1: T3 without approval...")
    result = pre_tool_use_hook('Task', {
        'subagent_type': 'terraform-architect',
        'prompt': 'Run terraform apply in production',
        'description': 'Apply terraform changes'
    })

    if result and "T3 operation detected without approval" in result:
        print("  ✅ PASSED: Correctly blocked T3 without approval")
        test1_pass = True
    else:
        print(f"  ❌ FAILED: Should have blocked T3 without approval. Got: {result}")
        test1_pass = False

    # Test 2: T3 operation with approval should pass
    print("\n  Test 2: T3 with approval...")
    result = pre_tool_use_hook('Task', {
        'subagent_type': 'terraform-architect',
        'prompt': 'User approval received. Phase 5: Realization. Run terraform apply',
        'description': 'Apply terraform changes'
    })

    if result is None or (result and "allowed" in result.lower()):
        print("  ✅ PASSED: Correctly allowed T3 with approval")
        test2_pass = True
    else:
        print(f"  ❌ FAILED: Should have allowed T3 with approval. Got: {result}")
        test2_pass = False

    # Test 3: Non-T3 operation should pass
    print("\n  Test 3: Non-T3 operation...")
    result = pre_tool_use_hook('Task', {
        'subagent_type': 'gcp-troubleshooter',
        'prompt': 'Check cluster status',
        'description': 'Get cluster information'
    })

    if result is None or (result and "allowed" in result.lower()):
        print("  ✅ PASSED: Correctly allowed non-T3 operation")
        test3_pass = True
    else:
        print(f"  ❌ FAILED: Should have allowed non-T3. Got: {result}")
        test3_pass = False

    # Test 4: Unknown agent should be blocked
    print("\n  Test 4: Unknown agent...")
    result = pre_tool_use_hook('Task', {
        'subagent_type': 'unknown-agent',
        'prompt': 'Do something',
        'description': 'Test unknown agent'
    })

    if result and "Unknown agent" in result:
        print("  ✅ PASSED: Correctly blocked unknown agent")
        test4_pass = True
    else:
        print(f"  ❌ FAILED: Should have blocked unknown agent. Got: {result}")
        test4_pass = False

    return all([test1_pass, test2_pass, test3_pass, test4_pass])

def test_bash_validation_still_works():
    """Ensure bash validation still works"""
    print("\n🧪 Testing Bash Validation (regression test)...")

    from pre_tool_use import pre_tool_use_hook

    # Test that terraform apply is blocked
    result = pre_tool_use_hook('Bash', {
        'command': 'terraform apply -auto-approve'
    })

    if result and ("blocked" in result.lower() or "not allowed" in result.lower()):
        print("  ✅ PASSED: Bash validation still blocks dangerous commands")
        return True
    else:
        print(f"  ❌ FAILED: Bash should block terraform apply. Got: {result}")
        return False

def main():
    """Run all tests"""
    print("="*60)
    print("WORKFLOW ENFORCEMENT TESTS")
    print("="*60)

    tests = [
        test_task_validation,
        test_bash_validation_still_works
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n  ❌ Test failed with exception: {e}")
            results.append(False)

    print("\n" + "="*60)
    print(f"RESULTS: {sum(results)}/{len(results)} test suites passed")
    print("="*60)

    if all(results):
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1

if __name__ == "__main__":
    sys.exit(main())