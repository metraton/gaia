---
name: agent-protocol
description: Use when producing any agent response -- governs the json:contract block, status reporting, and execution loop
metadata:
  user-invocable: false
  type: protocol
---

# Agent Protocol

This protocol governs REPORTING FORMAT, not tool access. All agents may use their declared tools during any phase.

## Response Contract

Every response MUST end with a single fenced `json:contract` block.

```json:contract
{
  "agent_status": {
    "plan_status": "<STATUS>",
    "agent_id": "<a + 5+ hex chars>",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [],
    "files_checked": [],
    "commands_run": [],
    "key_outputs": [],
    "verbatim_outputs": [],
    "cross_layer_impacts": [],
    "open_gaps": []
  },
  "consolidation_report": null,
  "approval_request": null
}
```

### agent_status (always required)

- `plan_status` -- one of the 5 valid states below
- `agent_id` -- generate once, reuse across responses
- `pending_steps` -- remaining work (`[]` when done)
- `next_action` -- `"done"` or what happens next

### evidence_report (always required)

All 7 fields validated by runtime. Use `[]` when not applicable. Keep each to 1-3 items unless the task genuinely needs more.

- `key_outputs` -- actionable findings: what is wrong, what changed, what needs attention
- `verbatim_outputs` -- literal command output; truncate at ~100 lines
- `cross_layer_impacts` -- adjacent surfaces affected
- `open_gaps` -- what remains unverified; never imply certainty

### consolidation_report (when multi-surface)

Required when `investigation_brief.consolidation_required` or `surface_routing.multi_surface` is true. Otherwise `null`.

Fields: `ownership_assessment`, `confirmed_findings`, `suspected_findings`, `conflicts`, `open_gaps`, `next_best_agent`. For examples, read `examples.md` in this skill directory.

### approval_request (when REVIEW)

Required when `plan_status` is `REVIEW`. Otherwise `null`.

Fields: `operation`, `exact_content`, `scope`, `risk_level`, `rollback`, `verification`.
When a hook blocks with `[T3_BLOCKED]` and an `approval_id`: do NOT retry -- set `plan_status` to `REVIEW`, include `approval_id` in `approval_request`, and wait for orchestrator resumption. See `examples.md`.

## Universal Execution Loop

Break work into increments. Each increment: `INVESTIGATE -> PLAN -> EXECUTE -> VERIFY -> loop or COMPLETE`

- **INVESTIGATE**: Read code, search patterns, check state.
- **PLAN**: Propose changes. Present REVIEW if T3 or approval-worthy.
- **EXECUTE**: Write files, run commands, build.
- **VERIFY**: Run tests, lint, confirm results. A red test blocks the next increment.

Every increment ends with a green test or verified result. Fix before moving on -- compounding failures is exponential. Decompose large tasks into 2-5 increments; implement one at a time.

## State Machine

| Status | Meaning |
|--------|---------|
| `IN_PROGRESS` | Investigating, planning, or executing work |
| `REVIEW` | Presenting plan with evidence for approval |
| `COMPLETE` | Executed and verified -- results confirmed |
| `BLOCKED` | Cannot proceed -- escalated |
| `NEEDS_INPUT` | Missing information from user |

### Transitions

```
IN_PROGRESS -> COMPLETE                                    (T0/T1/T2: investigate, execute, verify)
IN_PROGRESS -> REVIEW -> IN_PROGRESS -> COMPLETE           (T3/plan-first: investigate, present, execute)
IN_PROGRESS -> BLOCKED | NEEDS_INPUT                       (any point)
IN_PROGRESS -> IN_PROGRESS                                 (retry, max 2)
```

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (max 2) | `IN_PROGRESS` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options | `NEEDS_INPUT` |
| Contract repair | Reissue complete `json:contract`, skip re-investigation (max 2) | `IN_PROGRESS` |
