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

When a hook blocks your command with `[T3_BLOCKED]`, it means the nonce
system has no active grant for that operation. The deny response includes
an `approval_id` — a one-time hex token tied to exactly this command.

The instinct is to retry. That is the wrong move: each retry generates a
fresh nonce, so the old `approval_id` becomes stale, and you enter an
infinite retry-block loop.

Instead: report `REVIEW` with `plan_status`, include the `approval_id`
in your `approval_request`, and let the orchestrator present the plan
to the user. When the user approves, the grant activates and you are
resumed to retry the command.

The deny message format:
```
[T3_BLOCKED] This command requires user approval.
Do NOT retry this command. Report REVIEW with this approval_id in your json:contract.
approval_id: <hex>
```

If you lose the `approval_id`, re-attempt the command once for a fresh one.

## Anti-Patterns

- **Retrying after T3_BLOCKED** -- each retry generates a new nonce, making the previous approval_id stale; this loops forever
- **Missing fields in approval_request** -- the orchestrator presents these fields directly to the user; missing fields mean the user approves blind
- **Approval fields in prose only** -- the orchestrator parses the JSON object, not your text; prose-only plans bypass the structured approval flow
- **Reusing prior approvals** -- grants are scoped to a specific nonce and command; a prior approval does not cover a new operation
- **Fabricating the approval_id** -- the hook validates against its nonce store; an invented token will never match
