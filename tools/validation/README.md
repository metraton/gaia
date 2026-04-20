# Validation Module

**Purpose:** Approval gates and commit validation for T3 operations

## Overview

This module provides two critical validation components for the gaia-ops system:

1. **Commit Message Validator** - Enforces Conventional Commits format
2. **Approval Gate** - Manages T3 approval workflow with audit trail

## Core Components

### 1. Commit Message Validator

**MOVED:** Commit validation has been moved to `hooks/modules/validation/commit_validator.py`
and is now only used internally by `hooks/modules/tools/bash_validator.py`.

This ensures commit validation is enforced automatically during git commit commands
without requiring explicit imports in agent code.

**What it validates:**
- ✅ Conventional Commits format (`type(scope): description`)
- ✅ Allowed types (feat, fix, refactor, docs, test, chore, ci, perf, style, build)
- ✅ Subject line rules (max 72 chars, no period at end)
- ✅ Forbidden footers (no "Generated with" footers)

**Configuration:** `.claude/config/git_standards.json` (SSOT)
**Logs:** `.claude/logs/commit-violations.jsonl`

---

### 2. Approval Gate

Manages T3 operation approval workflow with structured questions and audit trail.

**Files:**
- `approval_gate.py` - Main approval gate
- `test_approval_gate.py` - Test suite

**Usage:**
```python
from tools.validation import request_approval, process_approval_response

# Generate approval question
approval = request_approval(
    realization_package,
    agent_name="gitops-operator",
    phase="Phase 4"
)

# Show summary to user
print(approval["summary"])

# Ask for approval
response = AskUserQuestion(**approval["question_config"])

# Process response
result = process_approval_response(
    approval["gate_instance"],
    response,
    realization_package,
    agent_name,
    phase
)

if result["approved"]:
    # Proceed to execution
    execute_plan()
else:
    # Halt workflow
    return {"status": "rejected"}
```

**Features:**
- 📊 Visual summary of realization package
- 🔍 Counts operations and resources
- 📝 Audit trail of all approval decisions
- ✅ Structured approval questions for AskUserQuestion

**Logs:** `.claude/logs/approvals.jsonl`

**Testing:**
```bash
python3 .claude/tools/validation/test_approval_gate.py
```

---

## Integration with Skills

This validation module works with skills in a **hybrid model**:

- **Skills** (`.claude/skills/`) - Document patterns and guide agents
- **Code** (this module) - Enforce rules and ensure consistency

**Skills updated:**
- `approval/SKILL.md` - References automatic validation
- `execution/SKILL.md` - Documents commit validation integration

**Division of responsibility:**
- **Skills guide:** Show examples, explain context, teach patterns
- **Code enforces:** Block invalid commits, log decisions, ensure compliance

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  config/git_standards.json (SSOT)                         │
│  - Conventional commit types                              │
│  - Forbidden footers                                      │
│  - Max lengths                                            │
└──────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│  hooks/modules/validation/ (Commit Validation)            │
│  └─ commit_validator.py                                   │
│     └─ Used by bash_validator.py only                     │
└──────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│  tools/validation/ (Approval Enforcement)                 │
│  └─ approval_gate.py                                      │
│     └─ Manages T3 approval workflow                       │
└──────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│  skills/ (Guidance)                              │
│  ├─ approval/SKILL.md                                     │
│  │  └─ How to present plans                               │
│  └─ execution/SKILL.md                                    │
│     └─ How to execute safely                              │
└──────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────┐
│  logs/ (Audit Trail)                                      │
│  ├─ commit-violations.jsonl                               │
│  └─ approvals.jsonl                                       │
└──────────────────────────────────────────────────────────┘
```

---

## Security Tiers

| Tier | Operations | Validation | Approval Gate |
|------|-----------|-----------|---------------|
| T0 | Read-only | No | No |
| T1 | Local changes | Yes | No |
| T2 | Reversible remote | Yes | No |
| T3 | Irreversible | **Yes** | **Yes** ⚠️ |

**T3 operations require:**
1. ✅ Commit message validation (enforced by commit_validator.py)
2. ✅ Approval gate (enforced by approval_gate.py)
3. ✅ Audit trail (logged automatically)

---

## Files

```
validation/
├── __init__.py                    # Module exports
├── README.md                      # This file
└── approval_gate.py               # T3 approval workflow

Note: commit_validator.py moved to hooks/modules/validation/
```

---

## Configuration

**Git Standards:** `.claude/config/git_standards.json`

Example:
```json
{
  "commit_message": {
    "type_allowed": ["feat", "fix", "refactor", "docs", "test", "chore"],
    "subject_max_length": 72,
    "footer_forbidden": ["Generated with Claude Code"]
  },
  "enforcement": {
    "enabled": true,
    "block_on_failure": true,
    "log_violations": true
  }
}
```

---

## Logs

**Commit Violations:** `.claude/logs/commit-violations.jsonl`

Example entry:
```json
{
  "timestamp": "2026-01-15T19:34:12.345678",
  "message": "Added new feature...",
  "errors": [{"type": "INVALID_FORMAT", "message": "..."}],
  "error_count": 1
}
```

**Approvals:** `.claude/logs/approvals.jsonl`

Example entry:
```json
{
  "timestamp": "2026-01-15T19:35:00.123456",
  "agent": "gitops-operator",
  "phase": "Phase 4",
  "approved": true,
  "user_response": "✅ Aprobar y ejecutar",
  "files_count": 2,
  "operations": "git push origin main",
  "git_commit": "feat(graphql): update image to v1.0.180"
}
```

---

## See Also

- `.claude/config/git_standards.json` - Git standards configuration
- `.claude/skills/request-approval/SKILL.md` - Approval-request workflow patterns
- `.claude/skills/execution/SKILL.md` - Execution workflow patterns
- `CLAUDE.md` - Orchestrator protocol with T3 workflow

---

**Version:** 4.2.0
**Last Updated:** 2026-03-11
**Maintained by:** gaia-ops validation team
