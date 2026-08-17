---
name: agent-response
description: Use when the orchestrator must read, reconcile, route, and present an agent_contract_handoff
---

# Agent Response — Consumer Branch

The durable contract row is the turn's whole delivery. The final message is the signal that the turn
ended and carries no envelope of its own, so there is nothing to reconcile against: what the agent
declared and what was persisted are one artifact.

**The read is not free, and that is the economics that decides whether it happens at all.**
`contract view --field <dotted.path>` prints only that subtree of the envelope
(`agent_status.agent_state`, `evidence_report.open_gaps`, ...), exiting 0 with the value verbatim when
the path exists and 1 when it does not — so "searched and found nothing" and "did not search" are
distinguishable without opening anything else. Without that distinction the only choice is between the
WHOLE row (expensive) and nothing (free), and free wins every time; the narrow `--field` read is what
makes reading the row cheaper than skipping it. `contract list --state DISPATCHED` lists turns still
open; `contract list --cut` lists every turn that did not close cleanly, naming the specialist and the
lane; `contract view --harness-id <agentId>`, with the id the dispatch returned, gives that turn's own
partial evidence.

## Reading order

**Phase 0 — triage, always, cheap.** Read `agent_status.agent_state`, then `cut_reason`, then
`continues_contract_id`, in that order. `agent_state` routes everything downstream. `cut_reason` says
whether that state is honest: it is stamped at birth and cleared only by a clean finalize, so
clean-versus-cut is recorded by design, not inferred. `never_finalized` means the turn never closed
itself; `backstop_capture` and `reaped` are the stop hook's forensic cleanup; `salvaged_truncation` is
rebuilt purely from the agent's incremental on-disk draft — which is why incremental writes are what
survive a cut. `continues_contract_id` says whether "empty" means anything: a continuation row is born
empty by design (Trap 3), and reading this before judging any field as empty is what keeps that design
from reading as a failure. What the row does not hold was never delivered — an `open_gaps` entry the
agent only narrated in its message did not survive the turn; treat the row's silence as the finding it
is rather than reading the prose as if it had been persisted.

**Phase 1 — load what that state demands, one block.**

| `agent_state` | Load |
|---|---|
| `APPROVAL_REQUEST` | `approval_request`, and nothing else matters until the user signs. Load `orchestrator-present-approval`; do not execute. |
| `NEEDS_INPUT` | `pending_steps` and `next_action`: the turn is stuck in the ORCHESTRATOR, not the user, and resuming the SAME agent keeps its state. |
| `BLOCKED` | `failure_report` and `open_gaps`, read with the rule proven true today: the block is usually a stale premise in the dispatch itself — information about the dispatch, not about the agent. Route only when another owner can resolve it. |
| `NEEDS_VERIFICATION` | Not "done": a plan-task-bound turn cannot seal itself. Dispatch `gaia-verifier`, binding `parent_handoff_id` to this producer row. A verifier pass may promote the work to `COMPLETE`; a fail returns it to `IN_PROGRESS` with evidence. Never accept a plan-task-bound producer's self-asserted `COMPLETE` as independent verification. |
| `COMPLETE` | `evidence_report.verification` first, then `open_gaps`. Terminal: relay/synthesize the verified result. |

Only `COMPLETE` is terminal. A finalized `IN_PROGRESS`, `APPROVAL_REQUEST`, `BLOCKED`, `NEEDS_INPUT`,
or `NEEDS_VERIFICATION` row is persisted, not complete.

For COMMAND_SET, reconcile `approval_request` with DB progress: exact ordered commands, `next_index`,
consumed indexes, failed index/reason, and grant status. Do not infer that the set ran merely because
it was approved. After a partial failure, report completed and untouched indexes distinctly, treat `FAILED` as a
terminal/frozen grant, and route fresh investigation plus a new request/approval for any
retry or remainder.

**Phase 2 — the findings.** `key_outputs`, and `report_prose` when the why is needed or when this
row's coordinate is about to be handed to another agent.

**Phase 3 — evidence on demand, only for the claim about to be made.** `commands_run` and
`verbatim_outputs` for a positive; `patterns_checked` for a NEGATIVE; `cross_layer_impacts` before
touching anything adjacent.

**Phase 4 — handoff.** Pass the COORDINATE, not the narrative.

## Three traps, all measured today on real rows

1. **`evidence_report.verification.result == "pass"` means the check RAN, not that the artifact is
   good.** A real row closed `pass` over a document rejected for three blocking defects. A reader who
   SWEEPS rows looking for `pass` reports the opposite of what happened. The row is read, not swept.
2. **`patterns_checked` is the only field a zero-result search survives in.** If the claim about to be
   made is that something does NOT exist, this is the only possible evidence for it.
3. **A continuation row is born empty by design.** That is exactly why `cut_reason` and
   `continues_contract_id` are read in Phase 0, before any field is judged empty — and why
   completeness is judged over the CHAIN, never the last link alone.

## Relaying to the user

On a valid single-agent `COMPLETE`, SubagentStop parses `user_facing_summary` off the same envelope the
gate treated as authoritative (`parse_user_facing_summary`) and emits it as the turn's `systemMessage`
-- a channel that reaches the USER's display and does NOT continue your conversation (measured by
suppression; see the comment over the relay in `hooks/adapters/claude_code.py`). Nothing arrived in
front of you: the field lives on the row, and the one read that fetches it is `contract view --field
user_facing_summary --harness-id <agentId>`. Read it when the user-facing report needs it and relay it
near-verbatim. Synthesizing from `key_outputs`, verification, failures and open gaps is the fallback for
when the field is absent, and at N>1 the per-agent summaries are inputs to one synthesis rather than N
relays. Preserve ownership boundaries and do not hide evidence the row and the verdict disagree on.
