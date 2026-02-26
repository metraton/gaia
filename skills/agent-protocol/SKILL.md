---
name: agent-protocol
description: Use when starting any agent task, reporting status, or handling errors — defines AGENT_STATUS format, state machine, and search protocol
user-invocable: false
---

# Agent Protocol

## Instantiation Model

You are instantiated with:
- **Identity** — your agent `.md`: who you are, domain, scope, output format
- **Skills** — injected procedural knowledge (the HOW)
- **Contracts** — project-context sections relevant to your domain: your trusted baseline
- **Request** — what the orchestrator needs you to do

Trust your contracts. Use them as search anchors — not documentation to read linearly.
When investigation reveals reality differs from contracts → emit `CONTEXT_UPDATE`.

## Search Protocol

```
CONTRACTS → LOCAL → LIVE → REPORT
```

**CONTRACTS** — Start here. Your injected project-context is your trusted baseline.
Use its values as search anchors: known path? target it. Known name/ID? search for it.
Broad globs are last resort.

**LOCAL** — Read code and config files. Compare against contracts.
- Match → confirmed, proceed.
- Mismatch → drift detected. Note for `CONTEXT_UPDATE`. May require LIVE verification.

**LIVE** — Only if drift is suspected or task explicitly requires live state.
Run `fast-queries` triage first (<15s). Deep-dive with domain CLIs only if triage flags issues.
Mismatch found → update your map, continue investigating with the new reality.
Only escalate (`BLOCKED`/`NEEDS_INPUT`) if the mismatch is CRITICAL to completing the task.

**REPORT** — Every response ends with AGENT_STATUS block.

For investigation methodology and pattern hierarchy, follow the `investigation` skill.

## AGENT_STATUS Format (MANDATORY)

Every response MUST end with this block:

```html
<!-- AGENT_STATUS -->
PLAN_STATUS: [INVESTIGATING|PLANNING|PENDING_APPROVAL|APPROVED_EXECUTING|FIXING|COMPLETE|BLOCKED|NEEDS_INPUT]
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
INVESTIGATING -> PLANNING -> PENDING_APPROVAL -> APPROVED_EXECUTING -> COMPLETE  (T3)
INVESTIGATING -> COMPLETE                                                        (T0/T1/T2)
APPROVED_EXECUTING -> FIXING (recoverable failure, max 2 cycles)
FIXING -> APPROVED_EXECUTING (retry after fix)
FIXING -> BLOCKED (after 2 cycles or non-recoverable error)
INVESTIGATING -> BLOCKED
INVESTIGATING -> NEEDS_INPUT
PLANNING -> NEEDS_INPUT
PENDING_APPROVAL -> PLANNING (user requests modifications)
```

## T3 Operation Workflow

**T3 only.** For T0/T1/T2, investigation leads directly to `COMPLETE`.

### Phase 1 — Investigate
Follow the `investigation` skill. Surface options when multiple approaches exist.

### Phase 2 — Plan
Set status: `PLANNING`. Simple (≤3 changes, clear scope) → inline plan. Complex (multi-service, architecture) → suggest speckit.

Follow the `approval` skill for plan format and presentation. Set status: `PENDING_APPROVAL`. Wait for orchestrator to resume with approval.

### Phase 3 — Execute & Verify
Read `.claude/skills/execution/SKILL.md`. Execute all plan steps, then run the Verification Criteria.
All pass → `COMPLETE`. Any fail → `FIXING` cycle (see State Flow above).

## Agent Handoff

When receiving context from another agent (team workflow): consume prior findings directly — no re-investigation of confirmed facts. If findings are incomplete or contradictory, investigate only the gap. Emit independent AGENT_STATUS.

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (FIXING state, max 2 cycles) | `FIXING` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options (A, B, C) | `NEEDS_INPUT` |
