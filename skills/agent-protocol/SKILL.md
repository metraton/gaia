---
name: agent-protocol
description: Core agent protocol - AGENT_STATUS format, local-first investigation, error handling
user-invocable: false
---

# Agent Protocol

## Local-First (MANDATORY)

Always: local repo → read-only validation → live (only if local data insufficient).

```
1. LOCAL    → Use project-context paths and values for targeted searches.
              Known path? Target it. Known name/ID? Search for it. Broad globs last resort.
2. VALIDATE → Read-only state checks (never mutating):
              Infrastructure:    terraform plan, terraform state show
              Kubernetes/GitOps: kubectl apply --dry-run, helm diff, flux diff
              Cloud:             gcloud describe, gsutil stat, fast-queries scripts
3. LIVE     → Query live APIs only if validation shows drift or task requires it
4. REPORT   → End every response with AGENT_STATUS block
```

For investigation methodology, follow the `investigation` skill.

## AGENT_STATUS Format (MANDATORY)

Every response MUST end with this block:

```html
<!-- AGENT_STATUS -->
PLAN_STATUS: [INVESTIGATING|PLANNING|PENDING_APPROVAL|APPROVED_EXECUTING|FIXING|COMPLETE|BLOCKED|NEEDS_INPUT]
CURRENT_PHASE: [Investigation|Planning|Execution|Complete]
PENDING_STEPS: [List of remaining steps]
NEXT_ACTION: [Specific next step]
AGENT_ID: [Your agent ID from Claude Code]
<!-- /AGENT_STATUS -->
```

### Valid States

| Status | Meaning |
|--------|---------|
| `INVESTIGATING` | Gathering information and evidence |
| `PLANNING` | Creating and validating the execution plan |
| `PENDING_APPROVAL` | T3 plan ready, awaiting user approval |
| `APPROVED_EXECUTING` | Running approved T3 actions |
| `FIXING` | Applying fixes after failed verification (max 2 cycles) |
| `COMPLETE` | Task finished, verification criteria passed |
| `BLOCKED` | Cannot proceed — escalated to user |
| `NEEDS_INPUT` | Missing information from user |

### State Flow

```
INVESTIGATING -> PLANNING -> PENDING_APPROVAL -> APPROVED_EXECUTING -> COMPLETE
APPROVED_EXECUTING -> FIXING (recoverable failure, max 2 cycles)
FIXING -> APPROVED_EXECUTING (retry after fix)
FIXING -> BLOCKED (after 2 cycles or non-recoverable error)
INVESTIGATING -> COMPLETE (T0/T1 tasks)
INVESTIGATING -> BLOCKED
INVESTIGATING -> NEEDS_INPUT
PLANNING -> NEEDS_INPUT
```

## State-Changing Operation Workflow

Triggered when the task requires approval (see `security-tiers`: apply, deploy, create, delete, push, commit).

### Phase 1 — Investigate
Follow the `investigation` skill. Surface options when multiple approaches exist.

### Phase 2 — Plan
Complexity: Simple (≤3 changes, clear scope) → inline plan. Complex (multi-service, architecture) → suggest speckit.

Read `.claude/skills/approval/SKILL.md` for plan format and presentation requirements.
Set status: `PENDING_APPROVAL`. Wait for orchestrator to resume with "User approved..."

### Phase 3 — Execute & Verify
Read `.claude/skills/execution/SKILL.md`. Execute all plan steps, then run the Verification Criteria.
All pass → `COMPLETE`. Any fail → `FIXING` cycle (see State Flow above).

## Agent Handoff

When receiving context from another agent (team workflow): consume prior findings directly — no re-investigation of confirmed facts. Emit independent AGENT_STATUS.
## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (FIXING state, max 2 cycles) | `FIXING` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options (A, B, C) | `NEEDS_INPUT` |
