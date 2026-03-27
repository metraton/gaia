---
name: execution
description: Use when the user has approved a T3 operation and execution is about to begin
metadata:
  user-invocable: false
  type: discipline
---

# Execution

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE.
Commands finishing is not success. Verification criteria passing is success.
```

## Mental Model

T3 operations modify live state. Live state changes can be
irreversible. You cannot claim COMPLETE until you have verified
the result — not assumed it. A command can exit 0 and leave the
system in a broken state.

## Pre-Execution Checklist

Before executing ANY approved operation:

- [ ] User approved via AskUserQuestion and ElicitationResult hook activated the grant
- [ ] Capture current state — know what you can roll back to
- [ ] Plan still valid — re-run dry-run if time has passed
- [ ] Commands will not prompt for interactive input

If ANY check fails → `BLOCKED`.

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

- COMPLETE without running verification criteria — the most common failure mode
- Execute on approximate approval — "user approved something like this" is not the canonical token
- Mutate without knowing rollback path — if you can't undo it, you're not ready to do it
