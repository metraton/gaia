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

When a hook blocks your command with `[T3_BLOCKED]` and an `approval_id`:

1. **STOP** -- do NOT retry the command. Retrying generates a new nonce
   each time, creating an infinite loop.
2. **Report REVIEW** -- set `plan_status` to `REVIEW` in your `json:contract`.
3. **Include the approval_id** -- copy the hex identifier from the hook deny
   response into `approval_request.approval_id`.
4. **Wait** -- the orchestrator presents your plan to the user. When the user
   approves, the orchestrator resumes you with the grant activated.
5. **Then retry** -- only after the orchestrator resumes you, retry the command.

The hook deny message looks like:
```
[T3_BLOCKED] This command requires user approval.
Do NOT retry this command. Report REVIEW with this approval_id in your json:contract.
approval_id: <hex>
```

If you lose the approval_id, re-attempt the command once for a fresh one.

## Anti-Patterns

- **Retrying after T3_BLOCKED** -- generates a new nonce, causes infinite loop
- Presenting approval without all 6 fields in `approval_request`
- Putting approval fields in text only without the JSON object
- Treating prior approvals as valid for new operations
- Fabricating or paraphrasing the approval_id token
