---
name: execution
description: Use when the user has approved a T3 operation and execution is about to begin
metadata:
  user-invocable: false
  type: discipline
---

# Execution

```
Commands finishing is not success.
Verification criteria passing is success.
```

## Mental Model

A command can exit 0 and leave the system in a broken state.
`terraform apply` can succeed while creating a misconfigured resource.
`kubectl apply` can succeed while a pod crash-loops. The only evidence
that matters is verification against the criteria from your plan —
not the exit code, not the absence of errors.

## Pre-Execution Checklist

Before executing an approved operation:

- [ ] Grant is active — the hook activated the nonce via user approval
- [ ] Current state captured — without a rollback baseline, partial failure is unrecoverable
- [ ] Plan still valid — state drifts between planning and execution; re-run dry-run if stale
- [ ] No interactive prompts — agent sessions cannot provide stdin; commands that prompt will hang

If a check fails → `BLOCKED` with which check and why.

## Execution Protocol

1. Run each step separately — verify exit code before next
2. On failure — classify: recoverable (`IN_PROGRESS`) or not (`BLOCKED`)
3. After all steps — run Verification Criteria from the plan

## Error Reporting

```
Error Type: [Transient | Validation | Permission | State conflict]
Error Message: [exact output]
Rollback Status: [what needs rollback if partial]
```

## Rollback

Know your rollback path BEFORE executing. This varies by domain:
your domain skill defines the specific rollback strategy.

## Traps

| If you're thinking... | The reality is... |
|---|---|
| "The plan just ran, no drift possible" | State can change between planning and execution |
| "Dry-run passed during planning" | Stale dry-run ≠ current state — re-run |
| "All commands exited 0, I'm done" | Exit 0 ≠ desired state — run verification criteria |
| "It's only dev, fewer checks needed" | Irreversibility is irreversibility regardless of env |

## Anti-Patterns

- **COMPLETE without verification** — the most common failure mode; exit 0 is not evidence
- **Execute on approximate approval** — "user approved something like this" does not activate the grant; the hook checks exact nonces
- **Mutate without a rollback path** — if you cannot describe how to undo it, partial failure becomes permanent damage
