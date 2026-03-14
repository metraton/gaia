---
name: approval
description: Use when a T3 operation is ready and needs to be presented to the user for approval before execution
metadata:
  user-invocable: false
  type: technique
---

# Approval

## Overview

The plan is a contract. The user approves the exact contract —
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

For `AWAITING_APPROVAL` (hook-blocked), also include:
```json
"nonce": "hex from hook block response"
```

### Risk Levels

| Level | Criteria |
|-------|----------|
| LOW | Single resource, non-prod, no dependencies |
| MEDIUM | Multiple resources, non-prod, some dependencies |
| HIGH | Production, dependencies, potential downtime |
| CRITICAL | Irreversible, data loss possible |

## Which Status to Emit

- `REVIEW` — presenting a plan before executing (no hook block)
- `AWAITING_APPROVAL` — hook blocked your command with `NONCE:<hex>`

## Nonce Flow

When a hook blocks your command, it returns a nonce:
```
APPROVAL REQUIRED. ... NONCE:<hex>
```

Include this nonce in your `approval_request`. It is machine-readable —
the orchestrator extracts it silently.

If you lose the nonce, re-attempt the command for a fresh one.

## Anti-Patterns

- Presenting approval without all 6 fields in `approval_request`
- Putting approval fields in text only without the JSON object
- Treating prior approvals as valid for new operations
- Fabricating or paraphrasing the nonce token
