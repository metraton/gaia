---
name: agent-protocol
description: Core agent protocol - AGENT_STATUS format, local-first investigation, error handling
user-invocable: false
---

# Agent Protocol

## Local-First Investigation

**Priority Order (MANDATORY):**

```
1. LOCAL FIRST   -> Repository files, git, grep, read
2. DECISION      -> Can I answer from local data?
   - YES -> Respond with findings
   - NO  -> Continue to cloud
3. CLOUD SECOND  -> Query cloud APIs only if local insufficient
4. STATUS REPORT -> End with AGENT_STATUS block
```

Local sources to check first:
- `.claude/project-context/` -> Project configuration
- `gitops/` -> K8s manifests, Flux configs
- `terraform/` -> Infrastructure as code

## AGENT_STATUS Format (MANDATORY)

Every response MUST end with this block:

```html
<!-- AGENT_STATUS -->
PLAN_STATUS: [INVESTIGATING|PENDING_APPROVAL|APPROVED_EXECUTING|COMPLETE|BLOCKED|NEEDS_INPUT]
CURRENT_PHASE: [Investigation|Planning|Execution|Complete]
PENDING_STEPS: [List of remaining steps]
NEXT_ACTION: [Specific next step]
AGENT_ID: [Your agent ID from Claude Code]
<!-- /AGENT_STATUS -->
```

### Valid States

| Status | Meaning |
|--------|---------|
| `INVESTIGATING` | Gathering information |
| `PENDING_APPROVAL` | T3 plan ready, needs user approval |
| `APPROVED_EXECUTING` | Running approved T3 actions |
| `COMPLETE` | Task finished |
| `BLOCKED` | Cannot proceed |
| `NEEDS_INPUT` | Missing information from user |

### State Flow

```
INVESTIGATING -> PENDING_APPROVAL -> APPROVED_EXECUTING -> COMPLETE
INVESTIGATING -> BLOCKED
INVESTIGATING -> NEEDS_INPUT
INVESTIGATING -> COMPLETE (T0/T1 tasks)
```

## T3 Two-Phase Workflow

When you detect a state-changing operation (apply, deploy, create, delete, push):

1. Create detailed plan with changes summary
2. Set status: `PENDING_APPROVAL`
3. Wait for orchestrator to resume with "User approved..."
4. Execute plan, set status: `APPROVED_EXECUTING` then `COMPLETE`

For detailed T3 workflows, read `.claude/skills/approval/SKILL.md` and `.claude/skills/execution/SKILL.md`.

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry | Continue |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options (A, B, C) | `NEEDS_INPUT` |
