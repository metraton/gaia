---
name: agent-response
description: Use when the orchestrator must read, reconcile, route, and present an agent_contract_handoff
---

# Agent Response — Consumer Branch

Read the durable contract row before the fence. If a response fence is present, parse it as
compatibility input and reconcile it with the row; never prefer a divergent
fence over persisted managed data. If the fence is missing after a cut, query
the born row/draft before redispatching.

**When to read the row.** Reconcile row and fence whenever the result will be presented to the user or used as the ground for what comes next; a turn whose outcome is merely reported and grounds nothing else does not need the read. The read is never free — the CLI-only guard returns the full row with no filter — so it is spent where the answer changes what happens next. `contract list --state DISPATCHED` lists turns still open; `contract list --cut` lists every turn that did not close cleanly, naming the specialist and the lane; `contract view --harness-id <agentId>`, with the id the dispatch returned, gives that turn's own partial evidence.

**Neither side is authoritative alone.** The row is authoritative for what was persisted; the fence for what the agent declared. A fence can carry what the row lacks — an `open_gaps` entry written into the final message and never mirrored — so reconciliation reads both and prefers neither blindly.

**The lane decides what survived.** `cut_reason` is stamped at birth and cleared only by a clean finalize, so clean-versus-cut is recorded by design, not inferred. `never_finalized` means the turn never closed itself; `backstop_capture` and `reaped` are the stop hook's forensic cleanup; `salvaged_truncation` is rebuilt purely from the agent's incremental on-disk draft — which is why incremental writes are what survive a cut. A cut turn holds only what was checkpointed before it: resume from there, never from zero.

| `agent_state` | Route |
|---|---|
| `IN_PROGRESS` | Continue the same increment with its checkpoints. |
| `APPROVAL_REQUEST` | Load `orchestrator-present-approval`; do not execute. |
| `NEEDS_INPUT` | Ask the material decision with concrete options. |
| `BLOCKED` | Surface the obstacle and route only when another owner can resolve it. |
| `NEEDS_VERIFICATION` | Dispatch `gaia-verifier`, binding `parent_handoff_id` to this producer row. |
| `COMPLETE` | Terminal: relay/synthesize the verified result. |

Only `COMPLETE` is terminal. A finalized `IN_PROGRESS`, `APPROVAL_REQUEST`,
`BLOCKED`, `NEEDS_INPUT`, or `NEEDS_VERIFICATION` row is persisted, not complete.

For COMMAND_SET, reconcile `approval_request` with DB progress: exact ordered
commands, `next_index`, consumed indexes, failed index/reason, and grant status.
Do not infer that the set ran merely because it was approved. After a partial
failure, report completed and untouched indexes distinctly, treat `FAILED` as a
terminal/frozen grant, and route fresh investigation plus a new request/approval
for any retry or remainder.

Verifier binding is explicit: the verifier receives `parent_handoff_id` for the
producer contract and no producer `plan_task_id` of its own. A verifier pass may
promote the work to `COMPLETE`; a fail returns it to `IN_PROGRESS` with evidence.
Never accept a plan-task-bound producer's self-asserted COMPLETE as independent
verification.

On a valid single-agent COMPLETE, prefer `user_facing_summary`; otherwise
synthesize from `key_outputs`, verification, failures, and open gaps. Preserve
ownership boundaries and do not hide conflicting fence/DB evidence.
