# Integration Tests - Quick Reference

## Overview

This directory contains **75 integration tests** validating the hooks and permissions system.

- **test_hooks_integration.py**: 55 tests for pre/post hooks, policy engine, permissions
- **test_hooks_workflow.py**: 20 tests for complete workflows and error handling

## Quick Commands

### Run All Integration Tests
```bash
cd $PROJECT_ROOT/ops/.claude-shared
python3 -m pytest tests/integration/ -v
```

### Run Specific Test File
```bash
# Pre/post hook tests
python3 -m pytest tests/integration/test_hooks_integration.py -v

# Workflow tests
python3 -m pytest tests/integration/test_hooks_workflow.py -v
```

### Run Specific Test Class
```bash
# Pre-hook validation tests
python3 -m pytest tests/integration/test_hooks_integration.py::TestPreToolUseHook -v

# Policy engine tests
python3 -m pytest tests/integration/test_hooks_integration.py::TestPolicyEngine -v

# GitOps security tests
python3 -m pytest tests/integration/test_hooks_integration.py::TestGitOpsSecurityValidation -v

# Settings merge tests
python3 -m pytest tests/integration/test_hooks_workflow.py::TestSettingsMergeWorkflow -v

# Complete workflow tests
python3 -m pytest tests/integration/test_hooks_workflow.py::TestCompleteWorkflow -v
```

### Run Specific Test
```bash
# Single test
python3 -m pytest tests/integration/test_hooks_integration.py::TestPreToolUseHook::test_hook_allows_read_operations -v
```

### Run with Different Output Formats
```bash
# Short traceback
python3 -m pytest tests/integration/ -v --tb=short

# No traceback (summary only)
python3 -m pytest tests/integration/ -v --tb=no

# Show print statements
python3 -m pytest tests/integration/ -v -s

# Stop on first failure
python3 -m pytest tests/integration/ -v -x
```

## Test Categories

### Pre-Tool Use Hook Tests (18 tests)
- Read operations allowed
- Write operations blocked
- Dry-run operations allowed
- GitOps security enforcement
- Error message quality

### Policy Engine Tests (10 tests)
- Tier classification (T0-T3)
- Command validation
- Invalid input handling
- Credential requirement detection

### GitOps Security Tests (9 tests)
- Kubectl read/write validation
- Helm operations
- Flux reconciliation blocking
- Dry-run support

### Settings Permission Tests (8 tests)
- Priority rules (deny > ask > allow)
- Pattern matching (wildcards, regex)
- Default deny behavior
- Missing settings handling

### Ask Permission Tests (4 tests)
- Terraform apply triggers ask
- Git push triggers ask
- Kubectl apply triggers ask
- Other commands default deny

### Workflow Tests (20 tests)
- Complete validation → execution → audit flow
- Error handling and recovery
- Settings merge and precedence
- Tier escalation scenarios
- Audit trail integrity

## Expected Results

```
========================= 75 passed in 0.5s =========================
```

- **Pass rate**: 100% (75/75)
- **Execution time**: < 0.5 seconds
- **No failures expected**

## Troubleshooting

### Import Errors
If you see import errors, ensure you're running from the correct directory:
```bash
cd $PROJECT_ROOT/ops/.claude-shared
python3 -m pytest tests/integration/ -v
```

### Missing Hooks
Some tests may be skipped if hooks are not available:
```
⚠️  pre_tool_use hook not available
⚠️  post_tool_use hook not available
```

This is expected if running outside the .claude-shared environment.

### Test Failures
If tests fail, check:
1. Hooks are in correct location: `hooks/pre_tool_use.py` and `hooks/post_tool_use.py`
2. Settings helpers are importable: `tests/system/permissions_helpers.py`
3. Python version is 3.8+ (tested with 3.12.7)

## What's Being Tested

### Pre-Hook Validation
- ✅ Blocks T3 operations (apply, delete, push)
- ✅ Allows T0 operations (get, describe, list)
- ✅ Allows T1 operations (validate, plan, template)
- ✅ Allows T2 operations (--dry-run)
- ✅ Provides helpful error messages

### Post-Hook Audit
- ✅ Logs all command executions
- ✅ Records metrics (duration, success/failure)
- ✅ Handles large output gracefully
- ✅ Creates necessary directories

### Settings System
- ✅ Merges project + shared settings
- ✅ Respects precedence rules
- ✅ Resolves permissions correctly
- ✅ Handles missing configurations

### Complete Workflows
- ✅ Pre-hook → Command → Post-hook flow
- ✅ Blocked commands stop at pre-hook
- ✅ Validation workflows pass through
- ✅ Tier escalation is blocked

## CI/CD Integration

To run in CI/CD pipelines:

```yaml
test-integration:
  script:
    - cd ops/.claude-shared
    - python3 -m pytest tests/integration/ -v --tb=short --junitxml=integration-results.xml
  artifacts:
    reports:
      junit: ops/.claude-shared/integration-results.xml
```

---

**Last Updated**: 2025-11-05  
**Total Tests**: 75  
**Pass Rate**: 100%
