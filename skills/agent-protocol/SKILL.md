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

**CONTRACTS** — Start here. If you have injected project-context, use its values as
search anchors: known path? target it. Known name/ID? search for it.
If no project-context was injected, start directly at LOCAL.
Broad globs are last resort.
Before running LIVE commands, check if contracts indicate resource accessibility
(e.g., `kubectl_accessible`, endpoint reachability). If a resource is marked
unreachable, skip live commands for it and note the limitation in your report.

**LOCAL** — Read code and config files. Compare against contracts.
- Match → confirmed, proceed.
- Mismatch → drift detected. Note for `CONTEXT_UPDATE`. May require LIVE verification.

**LIVE** — Only if drift is suspected or task explicitly requires live state.
If you have the `fast-queries` skill, run triage first (<15s). Otherwise, use
domain-appropriate read-only commands.
If your identity restricts you to local/code-only operations, skip LIVE entirely
— report what you found in LOCAL.
Mismatch found → update your map, continue investigating with the new reality.
Only escalate (`BLOCKED`/`NEEDS_INPUT`) if the mismatch is CRITICAL to completing the task.

**REPORT** — Every response ends with AGENT_STATUS. When you investigated, validated, reviewed, or gathered live/local evidence, include an `EVIDENCE_REPORT` block before AGENT_STATUS.

For investigation methodology and pattern hierarchy, follow the `investigation` skill.

## EVIDENCE_REPORT Format (MANDATORY WHEN EVIDENCE EXISTS)

Use this block whenever your response is based on code reading, config reading, command execution, review findings, or other concrete evidence. This applies to most `INVESTIGATING`, `PLANNING`, `BLOCKED`, `NEEDS_INPUT`, and evidence-backed `COMPLETE` responses.
It is also required for `PENDING_APPROVAL` and `FIXING`, because those states still need to show the evidence that justified the plan or the fix attempt.

If a field does not apply, write `- none` or `- not run` instead of omitting the field.

```html
<!-- EVIDENCE_REPORT -->
PATTERNS_CHECKED:
- [existing pattern or convention you compared against]
FILES_CHECKED:
- [file or path]
COMMANDS_RUN:
- `[exact command]` -> [concise result, or `not run`]
KEY_OUTPUTS:
- [short output excerpt or evidence summary]
CROSS_LAYER_IMPACTS:
- [affected adjacent surface, contract, or subsystem]
OPEN_GAPS:
- [remaining unknown, validation gap, or `none`]
<!-- /EVIDENCE_REPORT -->
```

Rules:
- Keep each field to 1-3 bullets unless the task genuinely needs more.
- `COMMANDS_RUN` must contain the exact command when a command was executed.
- `KEY_OUTPUTS` must summarize what mattered, not paste walls of output.
- `CROSS_LAYER_IMPACTS` is required for review, debugging, or multi-surface tasks.
- `OPEN_GAPS` must be explicit. Do not imply certainty when uncertainty remains.

## AGENT_STATUS Format (MANDATORY)

Every response MUST end with this block, after any `EVIDENCE_REPORT` and optional `CONTEXT_UPDATE`:

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

## T3 Operations

**T3 only.** If your identity restricts you to read-only operations (T0/T1/T2),
skip this section entirely -- investigation leads directly to `COMPLETE`.

For T3 operations, read `.claude/skills/approval/SKILL.md` and follow the workflow there.
Post-approval execution protocol is in `.claude/skills/execution/SKILL.md`.

## Self-Review Gate

Before setting PLAN_STATUS to `COMPLETE`, verify your own output:

1. **Re-read the original request.** Does your output answer what was asked?
2. **Check completeness.** Are all requested items addressed? Any silent omissions?
3. **Validate accuracy.** Do file paths, resource names, and commands match what you found in code?
4. **Verify format.** Does your output follow the `output-format` skill? Are the `EVIDENCE_REPORT` and AGENT_STATUS blocks present and correct when required?

If any check fails, fix before emitting COMPLETE. Do not flag self-review to the user -- just do it.

## Contract Repair

If runtime resumes you with instructions to repair your previous response contract, treat that as a structural fix request:

- Reissue a complete response with the required `EVIDENCE_REPORT`, optional `CONTEXT_UPDATE`, and `AGENT_STATUS`
- Do not restart the full investigation unless missing evidence truly requires one more command or file read
- Preserve the task's real status; contract repair is not a license to fabricate evidence or mark work complete prematurely

Runtime auto-repair retries are capped at 2. If your response is still structurally invalid after that, the orchestrator will escalate instead of silently retrying forever.

## Agent Handoff

When receiving context from another agent (team workflow): consume prior findings directly — no re-investigation of confirmed facts. If findings are incomplete or contradictory, investigate only the gap. Emit independent AGENT_STATUS.

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (FIXING state, max 2 cycles) | `FIXING` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options (A, B, C) | `NEEDS_INPUT` |
