---
name: execution
description: Use when the user has approved a T3 operation and execution is about to begin
metadata:
  user-invocable: false
  type: discipline
---

# Execution

`execution` governs the post-approval phase of a T3 operation: how to verify the grant is still actionable, run the approved command, detect environment drift, and confirm the result against the plan's verification criteria before declaring success. Approval and execution are coupled: the user approving the T3 is itself the order to execute, so the orchestrator re-dispatches the verbatim command automatically -- this phase begins with that fresh dispatch, not a separate "should I run it" turn. For the approval handoff that precedes this phase, see `agent-approval-protocol`. For the re-dispatch decision that delivered this command, see `orchestrator-present-approval` Rule 5.

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

- [ ] Grant is active — the user selected an `Approve [P-xxxxxxxx]` label and `activate_db_pending_by_prefix` (in `hooks/modules/security/approval_grants.py`) wrote a `SCOPE_SEMANTIC_SIGNATURE` grant against the approved command. The runtime matches semantically — `matches_approval_signature` (in `hooks/modules/security/approval_scopes.py`) evaluates the approved signature (base_cmd, verb, dangerous flags, semantic tokens), not byte-for-byte string equality. **Even so, the discipline is verbatim**: relay and execute the approved `exact_content` as literal. Do not reason about what variations the runtime tolerates — wrappers, `cd` prefixes, redirects, or extra flags create a statement the grant may not cover. The semantic tolerance is a runtime safety net, not a license to deviate.
- [ ] Single-use awareness — the grant is consumed **at the match, before the command executes** (`consume_db_semantic_grant` in `gaia/store/writer.py`), and has a 5-minute TTL. A retry after the command executed and failed re-blocks (the grant is already gone); so does a drifted variant that never matched. The orchestrator must request a fresh approval, not loop. The one exception: if this dispatch dies before it ever reaches the command, the grant is still `PENDING` and a re-dispatch within the 5 minutes reuses it.
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
| "Approval-time evidence (plan, dry-run, preconditions) still holds at execution" | State drifts between approval and execution; every precondition you relied on must be re-verified against current state, snapshot or no snapshot |
| "All commands exited 0, I'm done" | Exit 0 ≠ desired state — run the verification criteria from the plan |
| "It's only dev, fewer checks needed" | Irreversibility is irreversibility regardless of env |
| "The grant matched once, I can retry the same shape" | The grant is consumed at the match, before execution (`consume_db_semantic_grant`); a second invocation -- or any retry after the command executed -- re-blocks even if the command looks identical. Only a dispatch that died before reaching the command can reuse the still-`PENDING` grant, within its 5-minute TTL |
| "Half the bundle ran, I can finish after a SendMessage resume" | `mode` dies on resume; if the remaining steps touch `.claude/` writes, CC native re-blocks. Emit BLOCKED, let orchestrator re-dispatch fresh with the same mode (`agents/gaia-orchestrator.md` -> "Dispatch -> Re-dispatch vs SendMessage") |

## Bundled Multi-Step Execution on Protected Paths

When the approved operation is a **bundle** of steps on `.claude/` paths (e.g.,
mv directory + 4 Edits across `.claude/project-context/`), execute every step in
the SAME turn the dispatch started -- `mode` is per-dispatch and dies on a
SendMessage resume, so split bundles re-block the later steps in `default` mode.
See `agents/gaia-orchestrator.md` -> "Dispatch -> Re-dispatch vs SendMessage".

If a hook blocks a step mid-bundle, emit BLOCKED and stop -- do NOT emit
APPROVAL_REQUEST mid-bundle hoping to resume. The orchestrator's correct recovery
is a fresh dispatch (same mode, bundle re-packed), not a SendMessage back in.
See `agents/gaia-orchestrator.md` -> "Dispatch -> Re-dispatch vs SendMessage" for
the dispatch-vs-resume decision that governs protected-path bundles.

## Anti-Patterns

- **COMPLETE without verification** — the most common failure mode; exit 0 is not evidence and the agent stops one step before the only evidence that matters
- **Execute on approximate approval** — the grant is keyed to the approved command's semantic signature (`matches_approval_signature` in `hooks/modules/security/approval_scopes.py`); a drifted argument, an added flag, or a wrapper CLI re-blocks. "Close enough" is rejected by the hook, not the agent
- **Mutate without a rollback path** — if you cannot describe how to undo it, partial failure becomes permanent damage
- **Looping on failed recovery instead of reporting after one attempt** — attempt local recovery once, then report; retry loops compound the broken state
- **Splitting a `.claude/` bundle across a SendMessage resume** — `mode` is per-dispatch; the resume runs in `default` and CC native re-blocks the remaining steps
