# 4-Validation Module

**Purpose:** Approval gates and commit validation

## Overview

This module ensures infrastructure changes and critical operations follow proper governance:
- T3 approval gate enforcement
- Conventional commit validation
- Pre-commit hook integration

## Core Classes

### `ApprovalGate`
Manages T3 operation approval workflow.

**Methods:**
```python
from tools.validation import ApprovalGate

gate = ApprovalGate()
approval = gate.request_approval("terraform apply", operation_type="T3")
# Returns: ApprovalRequest with user options
```

### `CommitMessageValidator`
Validates commit messages against Conventional Commits spec.

**Methods:**
```python
from tools.validation import CommitMessageValidator, validate_commit_message

validator = CommitMessageValidator()
result = validate_commit_message("feat: add new API endpoint")
# Returns: ValidationResult(is_valid=True, errors=[])
```

## Core Functions

### `validate_commit_message(message)`
Quick validation function.

```python
from tools.validation import validate_commit_message

result = validate_commit_message("fix: resolve bug")
if result.is_valid:
    print("✓ Commit valid")
else:
    print(f"✗ Errors: {result.errors}")
```

### `safe_validate_before_commit(message)`
Pre-commit hook integration.

```python
from tools.validation import safe_validate_before_commit

if not safe_validate_before_commit("bad commit"):
    exit(1)  # Commit blocked
```

### `request_approval(operation, operation_type)`
Request user approval for T3 operations.

### `process_approval_response(approval_request, user_response)`
Process user's approval decision.

## Commit Message Format

Follows Conventional Commits:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Valid types:**
- `feat` - New feature
- `fix` - Bug fix
- `refactor` - Code refactoring
- `docs` - Documentation
- `test` - Tests
- `chore` - Maintenance
- `ci` - CI/CD

**Examples:**
```
feat(auth): add JWT token validation
fix(api): resolve null pointer exception
docs(readme): update setup instructions
refactor(core): simplify request handler
```

## Security Tiers

| Tier | Operations | Validation | Approval |
|------|-----------|-----------|----------|
| T0 | Read-only | Yes | No |
| T1 | Local changes | Yes | No |
| T2 | Reversible remote | Yes | No |
| T3 | Irreversible | Yes | **YES** |

## Git Standards

See `.claude/config/git-standards.md` for complete specification.

**Max commit message:** 72 characters
**Imperative mood:** "add feature" not "added feature"
**No period at end**

## Files

- `approval_gate.py` - T3 approval workflow
- `commit_validator.py` - Commit message validation
- `README.md` - This file

## Integration

**Pre-commit hook:**
```bash
# .git/hooks/pre-commit
from tools.validation import safe_validate_before_commit
import sys

message = sys.argv[1]
if not safe_validate_before_commit(message):
    exit(1)
```

## See Also

- `.claude/config/git-standards.md` - Full git standards
- `.claude/config/git_standards.json` - Validation schemas
- `hooks/pre_tool_use.py` - T3 enforcement hooks
- `tests/validators/test_approval_gate.py` - Tests
