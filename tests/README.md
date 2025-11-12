# Claude Agent System - Test Suite

Comprehensive test suite for the Claude agent orchestration system.

## Overview

This test suite validates all components of the agent system including:
- System structure and file organization
- Agent definitions and prompts
- Configuration files
- Routing and context provisioning
- Security validators and approval gates
- Git commit standards enforcement

## Test Structure

```
tests/
├── system/                    # System integrity tests
│   ├── test_directory_structure.py      # File/directory existence
│   ├── test_agent_definitions.py        # Agent prompt validation
│   └── test_configuration_files.py      # Config file validation
│
├── tools/                     # Tool functionality tests
│   ├── test_agent_router.py            # Semantic routing tests
│   └── test_context_provider.py        # Context generation tests
│
├── validators/                # Validation logic tests
│   ├── test_approval_gate.py           # Approval workflow tests
│   └── test_commit_validator.py        # Git commit validation tests
│
├── integration/               # End-to-end integration tests
│   └── (future integration tests)
│
├── fixtures/                  # Test fixtures and data
│   └── (test data files)
│
└── README.md                  # This file
```

## Running Tests

### Run All Tests
```bash
cd $PROJECT_ROOT/.claude
python3 -m pytest tests/ -v
```

### Run Specific Test Categories
```bash
# System integrity tests
python3 -m pytest tests/system/ -v

# Tool tests
python3 -m pytest tests/tools/ -v

# Validator tests
python3 -m pytest tests/validators/ -v
```

### Run Individual Test Files
```bash
python3 -m pytest tests/system/test_directory_structure.py -v
python3 -m pytest tests/tools/test_agent_router.py -v
python3 -m pytest tests/validators/test_commit_validator.py -v
```

### Run with Coverage
```bash
python3 -m pytest tests/ --cov=.claude/tools --cov-report=term
```

## Test Categories

### 1. System Tests (`system/`)

**test_directory_structure.py** (~15 tests)
- Validates all required directories exist
- Checks file structure integrity
- Verifies symlinks and permissions

**test_agent_definitions.py** (~10 tests)
- Validates agent prompt structure
- Checks required sections in agent files
- Ensures consistency across agents

**test_configuration_files.py** (~12 tests)
- Validates JSON configuration files
- Checks git_standards.json structure
- Verifies schema files

### 2. Tool Tests (`tools/`)

**test_agent_router.py** (~15 tests)
- Semantic routing accuracy (target: >75%)
- Intent classification tests
- Capability validation tests
- Agent selection logic

**test_context_provider.py** (7 tests)
- Context contract generation
- Context enrichment logic
- Token efficiency validation

### 3. Validator Tests (`validators/`)

**test_approval_gate.py** (17 tests)
- Approval workflow validation
- User response processing
- Audit trail logging

**test_commit_validator.py** (31 tests)
- Conventional Commits validation
- Forbidden footer detection
- Commit message format checking


### 5. Integration Tests (`integration/`)

**test_hooks_integration.py** (~55 tests)
- Pre-hook validation and blocking (~18 tests)
- PolicyEngine command classification (~10 tests)
- GitOps security validation (~9 tests)
- Settings permission matching (~8 tests)
- Ask permission triggers (~4 tests)
- Permission workflow scenarios (~4 tests)
- Post-hook audit logging (~2 tests)

**test_hooks_workflow.py** (~19 tests)
- Complete validation → execution → audit workflows (~4 tests)
- Error handling and recovery (~5 tests)
- Settings merge and resolution (~2 tests)
- GitOps-specific workflows (~3 tests)
- Tier escalation scenarios (~3 tests)
- Audit trail integrity (~2 tests)

**Key Features:**
- End-to-end workflow validation
- Pre/post hook integration
- Settings merge and permission resolution
- GitOps security enforcement
- Tier-based command classification
- Audit trail verification

**Testing Ask Permissions:**
```bash
# Integration tests verify ask permissions are triggered correctly
python3 -m pytest tests/integration/test_hooks_integration.py::TestAskPermissionTriggers -v

# Test complete workflow including settings merge
python3 -m pytest tests/integration/test_hooks_workflow.py::TestSettingsMergeWorkflow -v
```

**Quick Run:**
```bash
# Run all integration tests
python3 -m pytest tests/integration/ -v

# Run specific test class
python3 -m pytest tests/integration/test_hooks_integration.py::TestPreToolUseHook -v

# Run workflow tests
python3 -m pytest tests/integration/test_hooks_workflow.py -v
```

## Test Metrics

### Current Coverage (Updated 2025-11-07)
- **Total Tests:** 257 tests passing
  - `integration/` - ~60 tests - Hooks workflow and security validation
  - `system/` - ~10 tests - Agent definitions and configuration
  - `tools/` - ~15 tests - Routing and context provisioning
  - `validators/` - ~10 tests - Approval gates and commit validation
  - `permissions-validation/` - ~5 tests - Permission system validation
  - Additional test suites - ~157 tests
- **Pass Rate:** >95%
- **Execution Time:** <2 seconds
- **Routing Accuracy:** 92.7% on semantic routing tests

### Run Current Metrics
```bash
python3 -m pytest tests/ -v --tb=short
```

## Key Test Features

### Semantic Routing Tests
- Validates intent classification accuracy
- Tests capability validator logic
- Ensures proper agent selection
- Golden set accuracy benchmarking

### Commit Validation Tests
- Enforces Conventional Commits format
- Blocks forbidden footers (Claude signatures)
- Validates subject line rules
- Tests type and scope validation

### Approval Gate Tests
- Validates approval workflow
- Tests summary generation
- Ensures audit logging
- Verifies user response handling

## Test Dependencies

Required packages:
```bash
pip install pytest pytest-cov
```

Optional (for schema validation):
```bash
pip install jsonschema
```

## Writing New Tests

### Test File Template
```python
"""
Test suite for [component name]
Description of what this test file validates
"""

import pytest
from pathlib import Path


class TestComponentName:
    """Test suite for specific component"""

    @pytest.fixture
    def setup(self):
        """Setup test fixtures"""
        return {}

    def test_feature_works(self, setup):
        """Test description"""
        assert True, "Failure message"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

### Best Practices
1. Use descriptive test names (test_what_it_validates)
2. Include docstrings for all test classes and methods
3. Use fixtures for common setup
4. Assert with clear failure messages
5. Keep tests independent and isolated

## Continuous Integration

These tests should be run:
- Before committing changes to agent system
- After modifying routing logic
- When updating configuration files
- Before deploying to production

## Troubleshooting

### Import Errors
If you see import errors, ensure you're running from the correct directory:
```bash
cd $PROJECT_ROOT/.claude
python3 -m pytest tests/ -v
```

### Path Issues
Tests use `Path(__file__).resolve().parents[N]` to find system directories.
Ensure test files maintain the correct directory depth.

### Fixture Not Found
If a fixture is not found, check that:
1. The fixture is defined in the same test class
2. The fixture name matches the parameter name
3. pytest is discovering the test file correctly

## Contributing

When adding new tests:
1. Place in appropriate category directory
2. Follow naming convention: `test_*.py`
3. Update this README with test count
4. Ensure all tests pass before committing

## References

- Agent Router: `.claude/tools/agent_router.py`
- Context Provider: `.claude/tools/context_provider.py`
- Commit Validator: `.claude/tools/commit_validator.py`
- Approval Gate: `.claude/tools/approval_gate.py`
- Agent Definitions: `.claude/agents/*.md`
- Configuration: `.claude/config/*.json`

---

Last Updated: 2025-11-05
Total Tests: ~212 (includes 53 permissions + 74 integration tests)

### 4. Permissions Tests (`system/`)

**test_permissions_system.py** (53 tests)
- Settings file loading and merging (10 tests)
- Permission priority resolution: deny > ask > allow (12 tests)
- Execution standards enforcement (8 tests)
- Security tier validation (T0-T3) (15 tests)
- Production vs development mode (8 tests)

**Key Features:**
- Validates settings merge logic (project + shared)
- Tests permission precedence rules
- Validates tier definitions and enforcement
- Tests environment-specific behavior

**Quick Run:**
```bash
# From tests directory
./run_permissions_tests.sh

# Or directly with pytest
python3 -m pytest tests/system/test_permissions_system.py -v
```

**Helper Module:** `permissions_helpers.py`
- Settings loading utilities
- Merge logic implementation
- Environment detection
- Permission level checking

**Documentation:** See `PERMISSIONS_MIGRATION.md` for:
- Migration from manual to automated testing
- Test suite structure and coverage
- CI/CD integration guide
- Troubleshooting and best practices

