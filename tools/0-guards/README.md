# Workflow Guards

Binary enforcement system for workflow rules. Guards execute BEFORE phases run to ensure preconditions are met.

## What This Does

Enforces workflow rules with binary pass/fail decisions. If a guard fails, the workflow stops immediately with a clear reason.

## Where This Fits

```
User Request → **Guards Check** → Phase Execution (if pass) OR Stop (if fail)
```

Guards run at each phase boundary to verify:
- Required inputs present
- Security tier appropriate
- Dependencies satisfied
- Approval gates passed

## Components

| File | Purpose |
|------|---------|
| `workflow_enforcer.py` | Core guard execution engine |
| `delegation_matrix.py` | Delegation decision guards |
| `guards_config.json` | Guard rules configuration |

## Usage

```bash
# Check guards for Phase 3 (Planning)
python3 workflow_enforcer.py --phase 3 --input request.json

# Output:
# ✅ PASS: All guards satisfied
# OR
# ❌ FAIL: Missing required context section 'terraform_infrastructure'
```

## Guard Types

| Guard | When | Checks |
|-------|------|--------|
| Phase 0 | Before clarification | User request not empty |
| Phase 1 | Before routing | Task metadata valid |
| Phase 2 | Before context | Agent name valid |
| Phase 3 | Before planning | Context complete |
| Phase 4 | Before approval | T3 operations have plan |
| Phase 5 | Before execution | Approval granted (if T3) |
| Phase 6 | Before SSOT update | Execution succeeded |

## Design Principles

1. **Binary decisions** - Pass or fail, no ambiguity
2. **Fail fast** - Stop immediately on violation
3. **Clear reasons** - Explicit error messages
4. **Stateless** - Each check independent
5. **Configurable** - Rules in JSON, not code

## Examples

### Guard Pass

```json
{
  "guard": "phase_2_context",
  "status": "pass",
  "duration_ms": 12
}
```

### Guard Fail

```json
{
  "guard": "phase_3_planning",
  "status": "fail",
  "reason": "Missing contract section: terraform_infrastructure",
  "blocked_phase": "planning"
}
```

---

**Phase:** 0 (Pre-execution) | **Type:** Binary enforcement
