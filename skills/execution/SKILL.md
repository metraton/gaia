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

- [ ] Grant is active — the hook activated the nonce via `APPROVE:<nonce>` user approval
- [ ] Current state captured — without a rollback baseline, partial failure is unrecoverable
- [ ] Plan still valid — state drifts between planning and execution; re-run dry-run if stale
- [ ] No interactive prompts — agent sessions cannot provide stdin; commands that prompt will hang

If a check fails → `BLOCKED` with which check and why.

## Precondition Verification

Before executing any approved command, verify that the preconditions for success still hold. Use domain knowledge to determine what to check -- this is not a lookup table, it is a judgment call.

The world changes between approval and execution. A command approved 5 minutes ago may fail because the environment moved. Checking first avoids a wasted failure cycle.

**Principle**: If the command depends on external state, verify that state before executing.

**Recovery**: If a precondition fails and the fix is local (pull --rebase, state refresh, resource re-fetch), attempt it ONCE, then retry the original command. If recovery also fails, report the situation -- do not loop.

**Boundary**: Recovery actions must only modify LOCAL state. Never attempt remote-mutating recovery (force push, remote delete, state import) without explicit user approval.

## Environment Drift Detection

When the pending file includes an `environment` snapshot (captured when the command was originally blocked), compare current state against it before executing.

If drift is detected (e.g., remote HEAD has moved, resource version changed), surface the drift to the user before proceeding. The user decides whether to continue or abort.

When no snapshot is available, verify observable state regardless -- the absence of a snapshot does not exempt the agent from precondition checks.

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
| "Preconditions held during planning" | State changes between approval and execution -- verify again |
| "No environment snapshot, no drift check" | Verify observable state regardless of whether a snapshot exists |
| "Half the bundle ran, I can finish after a SendMessage resume" | `mode` dies on resume; if the remaining steps touch `.claude/` writes, CC native re-blocks. Emit BLOCKED, let orchestrator re-dispatch fresh with the same mode. |

## Bundled Multi-Step Execution on Protected Paths

When the approved operation is a **bundle** of steps on `.claude/` paths (e.g., mv directory + 4 Edits across `.claude/project-context/`), execute every step in the SAME turn the dispatch started. Splitting the bundle across dispatch + SendMessage resume fails because `mode` is per-dispatch and does not survive a SendMessage resume -- CC native re-blocks the later Edits in `default` mode.

If a hook blocks a step mid-bundle, emit BLOCKED and stop. Do NOT emit APPROVAL_REQUEST mid-bundle hoping to continue after resume. The orchestrator's correct recovery is a fresh dispatch (same mode, bundle re-packed) after any required approval, not a SendMessage back into the same subagent.

## Anti-Patterns

- **COMPLETE without verification** — the most common failure mode; exit 0 is not evidence
- **Execute on approximate approval** — "user approved something like this" does not activate the grant; the hook checks exact nonces
- **Mutate without a rollback path** — if you cannot describe how to undo it, partial failure becomes permanent damage
- **Skipping precondition verification because the user already approved** — approval reflects state at approval time; state may have changed
- **Looping on failed recovery instead of reporting after one attempt** — attempt recovery once, then report; do not retry in a loop
- **Splitting a `.claude/` bundle across a SendMessage resume** — `mode` is per-dispatch; the resume runs in `default` and CC native re-blocks the remaining steps
