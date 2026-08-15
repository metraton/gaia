---
name: agent-response
description: Use when the orchestrator must read, reconcile, route, and present an agent_contract_handoff
---

# Agent Response — Consumer Branch

The durable contract row is the turn's whole delivery. The final message is the signal that the turn
ended and carries no envelope of its own, so there is nothing to reconcile against: what the agent
declared and what was persisted are one artifact. After a cut, query the born row/draft before
redispatching.

**When to read the row.** Read it whenever the result will be presented to the user or used as the ground for what comes next; a turn whose outcome is merely reported and grounds nothing else does not need the read. The read is not free, so spend it where the answer changes what happens next — and narrow it when you can: `contract view --field <dotted.path>` prints only that subtree of the envelope (`agent_status.agent_state`, `evidence_report.open_gaps`, ...), exiting 0 with the value verbatim when the path exists and 1 when it does not, so an empty field and an absent one never read alike. `contract list --state DISPATCHED` lists turns still open; `contract list --cut` lists every turn that did not close cleanly, naming the specialist and the lane; `contract view --harness-id <agentId>`, with the id the dispatch returned, gives that turn's own partial evidence.

**What the row does not hold was never delivered.** An `open_gaps` entry the agent only narrated in its message is not evidence and does not survive the turn; treat the row's silence as the finding it is, and route it back rather than reading the prose as if it had been persisted.

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

On a valid single-agent COMPLETE, `user_facing_summary` is not a preference to
weigh: SubagentStop already parsed it off the same envelope the gate treated as
authoritative (`parse_user_facing_summary`) and handed it back as the turn's
`systemMessage`, so the value is in front of you with no second read to make.
Relay it near-verbatim. Synthesizing from `key_outputs`, verification, failures
and open gaps is the fallback for when the field is absent, and at N>1 the
per-agent summaries are inputs to one synthesis rather than N relays. Preserve
ownership boundaries and do not hide evidence the row and the verdict disagree on.
