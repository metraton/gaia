---
name: approval
description: Use when a T3 operation is ready and needs to be presented to the user for approval before execution
metadata:
  user-invocable: false
  type: technique
---

# Approval

## Overview

The plan is a contract. The user approves the exact contract --
not a vague intent. Structure your plan in the `approval_request`
field of your `json:contract` so the orchestrator can present it
directly to the user.

## Approval Request

Include an `approval_request` object in your `json:contract` block
with these 6 fields:

```json
"approval_request": {
  "operation": "verb + target",
  "exact_content": "literal command, config, or file change",
  "scope": "files, resources, environments affected",
  "risk_level": "LOW | MEDIUM | HIGH | CRITICAL",
  "rollback": "how to undo if wrong",
  "verification": "how to confirm success after execution"
}
```

When a hook blocked your command with an `approval_id`, also include:
```json
"approval_id": "hex from hook deny response"
```

### Risk Levels

| Level | Criteria |
|-------|----------|
| LOW | Single resource, non-prod, no dependencies |
| MEDIUM | Multiple resources, non-prod, some dependencies |
| HIGH | Production, dependencies, potential downtime |
| CRITICAL | Irreversible, data loss possible |

## Which Status to Emit

- `REVIEW` -- presenting a plan before executing (no hook block)
- `REVIEW` with `approval_id` -- hook blocked your command with an approval_id

Both use `REVIEW` as the plan_status. The presence or absence of
`approval_id` in `approval_request` tells the orchestrator which
handling path to take.

## Hook Block Flow

When a hook blocks your command, it returns a deny with an approval_id:
```
[T3_BLOCKED] ... approval_id: <hex>
```

Include this approval_id in your `approval_request`. It is machine-readable --
the orchestrator extracts it silently.

If you lose the approval_id, re-attempt the command for a fresh one.

## Anti-Patterns

- Presenting approval without all 6 fields in `approval_request`
- Putting approval fields in text only without the JSON object
- Treating prior approvals as valid for new operations
- Fabricating or paraphrasing the approval_id token
