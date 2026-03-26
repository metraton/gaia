---
name: agent-protocol
description: Defines json:contract response format, state machine, and evidence reporting
metadata:
  user-invocable: false
  type: protocol
---

# Agent Protocol

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

All 7 fields validated by runtime. Use `[]` when not applicable.
Keep each to 1-3 items unless the task genuinely needs more.

- `key_outputs` -- actionable findings, not descriptions of what you ran. Highlight what matters: what is wrong, what changed, what needs attention.
- `verbatim_outputs` -- literal command output; truncate at ~100 lines
- `cross_layer_impacts` -- adjacent surfaces affected
- `open_gaps` -- what remains unverified; never imply certainty

### consolidation_report (when multi-surface)

Required when `investigation_brief.consolidation_required` or
`surface_routing.multi_surface` is true. Otherwise `null`.

Fields: `ownership_assessment`, `confirmed_findings`,
`suspected_findings`, `conflicts`, `open_gaps`, `next_best_agent`.

For examples, read `examples.md` in this skill directory.

### approval_request (when REVIEW)

Required when `plan_status` is `REVIEW`. Otherwise `null`.

Fields: `operation`, `exact_content`, `scope`, `risk_level`, `rollback`, `verification`.
When a hook blocked a T3 command, also include `approval_id` (the hex identifier
from the hook's deny response).

## State Machine

| Status | Meaning |
|--------|---------|
| `IN_PROGRESS` | Active work: investigating, planning, executing, retrying (max 2 cycles) |
| `REVIEW` | Presenting plan or analysis for user feedback. May include `approval_id` when hook-blocked. |
| `COMPLETE` | Task finished |
| `BLOCKED` | Cannot proceed -- escalated |
| `NEEDS_INPUT` | Missing information from user |

### Transitions

```
IN_PROGRESS -> COMPLETE                                    (T0/T1/T2)
IN_PROGRESS -> REVIEW -> IN_PROGRESS -> COMPLETE           (plan-first or hook-blocked T3)
IN_PROGRESS -> BLOCKED | NEEDS_INPUT                       (any point)
IN_PROGRESS -> IN_PROGRESS                                 (retry, max 2)
```

## Error Handling

| Type | Action | Status |
|------|--------|--------|
| Recoverable | Fix and retry (max 2 cycles) | `IN_PROGRESS` |
| Blocker | Log details, list solutions | `BLOCKED` |
| Ambiguous | List options | `NEEDS_INPUT` |

## Contract Repair

If resumed with repair instructions, reissue a complete response
with `json:contract`. Do not rerun the full investigation.
Retries capped at 2.
