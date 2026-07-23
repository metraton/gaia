---
name: agent-response
description: Use when an agent returns an agent_contract_handoff response that needs to be interpreted and presented to the user
---

# Agent Response Protocol

The orchestrator loads this to interpret a returned `agent_contract_handoff` and decide the next action keyed on `plan_status` -- the consume side of the contract the subagent produced. For the field schema (every field, required/conditional status, triggers, the INPUT-vs-OUTPUT name collision), see `agent-contract-handoff`; this skill does not restate field meanings, it tells you what to do with them.

## State machine

`plan_status` is the first field read; it selects the branch. The six values are canonical in `VALID_PLAN_STATUSES` (`gaia.state`, re-exported by `response_contract.py`) -- their meanings live in `agent-contract-handoff`, not here.

```
read agent_status.plan_status  (from the agent's fenced block; or, mid-conversation, a gaia-operator dispatch running `gaia contract view` and relaying it back -- you have no shell of your own)
  |- COMPLETE            -> pedagogical summary (situation -> impact) + offer of available detail; verbatim only when imperative; surface verification, then close
  |- APPROVAL_REQUEST    -> split on approval_id (present: present-approval; absent: plan options)
  |- NEEDS_INPUT         -> AskUserQuestion, then SendMessage the answer
  |- NEEDS_VERIFICATION  -> MUST dispatch a verifier-role agent to validate the task's gates before it can reach COMPLETE (a verifier rejection bounces it to IN_PROGRESS); a proposal, never a completion (harness R2)
  |- BLOCKED             -> present open_gaps; new dispatch or accept the limitation
  +- IN_PROGRESS         -> SendMessage to resume (runtime caps consecutive retries at 2)
```

The agent builds its contract by-value with the `gaia contract` CLI across the turn (see `agent-protocol`); you have no shell, so reading the current draft mid-conversation -- between an agent's messages, without waiting for it to re-emit anything -- means dispatching a subagent (gaia-operator is the lightweight default) to run `gaia contract view` and relay the draft back to you. Before acting on a fenced-block turn, the contract must still parse. A block that fails `parse_contract` (`contract_validator.py`, the migration-only fallback path) is treated as missing -- see Error handling.

## Mandatory action per plan_status

| `plan_status` | Action |
|---|---|
| `COMPLETE` | Give the user a clear, pedagogical summary -- the real situation, then what it changes for them -- plus an explicit offer that the detail is available ("if you want to see X, I have it"). Build it from `user_facing_summary` when present on a single-agent turn, or from `key_outputs` when it is absent or N>1 (consolidation); either way the default is the landed synthesis, not the transcript. Show verbatim content only when it is imperative: (a) the contract obliges it -- an approval whose exact values / lock / ok the user must see (the `orchestrator-present-approval` iron law, unchanged) -- or (b) the user asked for the specific evidence. Either way, surface `verification.result` / `verification.details` -- that block is the proof the work landed, and relaying it is what lets the user trust the increment rather than take "done" on faith. Mention `cross_layer_impacts` and `open_gaps` when non-empty. |
| `APPROVAL_REQUEST` | Split on `approval_request.approval_id`: present -> load `Skill('orchestrator-present-approval')`; absent -> present the plan with options (execute / modify / cancel) and on execute/modify resume the SAME agent via `SendMessage`. It splits because a hook-issued `approval_id` carries a pending T3 grant that needs the structured consent flow, while an `APPROVAL_REQUEST` with no `approval_id` carries no grant and only needs a direction (execute / modify / cancel) back to the same agent. |
| `NEEDS_INPUT` | `AskUserQuestion` with the options in `next_action`, then `SendMessage` the answer back to resume. |
| `NEEDS_VERIFICATION` | Harness R2: the agent is proposing the increment is done, not asserting it. Do NOT treat this as `COMPLETE` -- the gate never accepts it as done regardless of a proposed `evidence_report.verification.result`. A `NEEDS_VERIFICATION` contract is a GUARANTEED dispatch, not a judgment call: the orchestrator MUST dispatch the seeded verifier-role agent (`gaia-verifier`, `verifier: true`) to validate the task's gates before the task can reach `COMPLETE`. A verifier rejection bounces the task to `IN_PROGRESS` (resume the original agent with the verifier's findings); a verifier pass is what actually promotes the contract to `COMPLETE`. The verifier registry (`gaia.state.permissions.verifier_fleet`) is populated today -- the gate is ARMED, so only that seeded verifier identity may set `COMPLETE` (`hooks/adapters/claude_code.py`, `_verifier_role_violation`); a non-verifier agent's own `COMPLETE` is rejected outright, and the dispatch to `gaia-verifier` is never skipped or held pending a judgment call. |
| `BLOCKED` | Present `open_gaps` to the user. If they give direction, dispatch a NEW agent addressing the blocker; if they accept the limitation, close the task as incomplete and move on. |
| `IN_PROGRESS` | `SendMessage` to resume the agent. The runtime caps consecutive `IN_PROGRESS` at 2 (`_MAX_IN_PROGRESS_RETRIES` in `state_tracker.py`) -- do not loop past that expecting progress; treat a third as a stall and escalate. The agent's draft persists across the resume by its own contract id, so, when you need to check progress before deciding whether to resume again or escalate, dispatch gaia-operator to run `gaia contract view` and relay it back -- you have no shell of your own, and you do not have to wait on prose to know what changed. |

## The fields easy to drop

These ride alongside `plan_status` and carry signal the orchestrator loses if it reads only the status.

**`verification`** -- covered in COMPLETE above. It is required only on `COMPLETE` and its `result` must equal `"pass"` (named code `VERIFICATION_RESULT` in `gaia/contract/validator.py`, relabeled to the legacy `VERIFICATION_RESULT_MUST_BE_PASS` token on the fence-fallback path); surface `result` and `details` so the user sees the proof, never just the word "done."

**Verified-via-finalize vs. hook-backstopped.** A row with `task_status == 'COMPLETE'` is NOT automatically a verified `COMPLETE` from the agent's own `finalize` -- check it does not also carry `degraded=true`. When the agent finalizes itself, the row is clean; when it does not (crash, truncation, forgotten call), the SubagentStop hook backstops a row and marks it `degraded=true`. Treat a `degraded` row's `COMPLETE` as NOT verified even though the nominal status reads COMPLETE -- surface it to the user as incomplete/unverified rather than presenting the pedagogical-summary path above, and consider resuming the same agent to actually finalize.

**`user_facing_summary`** -- the one human-audience field (every other field is machine-audience for the orchestrator). On a single-agent `COMPLETE` it is the raw material for the summary you land: keep its substance and language-adapt it, but shape the turn as situation -> impact plus an offer of the detail -- do not paste it as a second, redundant recap, and do not bury it under a re-synthesis that loses what the specialist actually said. It is optional and additive: when absent, fall back to `key_outputs`; when multiple agents are in flight (N>1), ignore it and synthesize across them, because no single agent's summary speaks for the consolidated result. The verbatim escape hatch stays narrow: contract-obliged values (approvals) or user-requested evidence.

**`memorialize_suggestions` / `memory_suggestions`** -- present each entry to the user before closing the turn and persist ONLY on consent. The orchestrator is the sole memory writer; subagents are blocked from curated writes by design so each entry enters the substrate as a named choice. For the curation mechanics -- how to triage, slug, and persist -- load `Skill('memory')` (combo decision 1: the HOW lives in `memory`).

**`ownership_assessment`** (in `consolidation_report`, enum `VALID_OWNERSHIP_ASSESSMENTS`) -- a ROUTING INPUT the orchestrator acts on silently, not a user-facing field. `owned_here` means the output is authoritative; `cross_surface_dependency` or `not_my_surface` means another dispatch may be needed to close the gap. Route on it; do not narrate it (combo decision 4).

**`loop_state`** -- when present and blocking (`iteration < max_iterations AND metric < threshold`, `_check_loop_state_blocking` in `contract_validator.py`), a `COMPLETE` is held and the loop resumes for another iteration. Treat the turn as not-yet-complete and resume rather than report success.

## Multiple agents

The multi-agent consolidation loop -- wait-for-all before responding, consolidate findings, route the next round on `conflicts` / `next_best_agent` -- is owned by `gaia-patterns` and the orchestrator identity. This skill points to it; it does not redefine it (combo decision 5). When several agents are in flight, hold the response until all return, then apply the per-status actions above to the consolidated result.

## Error handling

| Situation | Action |
|---|---|
| Contract malformed or missing (`parse_contract` returns `None`) | Resume the agent with repair instructions; the runtime caps repair at 2 retries (`_MAX_IN_PROGRESS_RETRIES`). Do not fabricate a status. |
| `COMPLETE` without a passing `verification` | Reject as malformed and resume via the same repair path -- COMPLETE without `result: "pass"` is a contradiction the runtime already blocks; do not present it as done. |
| `APPROVAL_REQUEST` missing `rollback` or `verification` | Reject and resume via the same repair path -- both are blocking fields (`agent-contract-handoff` -> approval_request), and an approval without them cannot be presented for informed consent. |
| Row carries `degraded=true` (hook-backstopped, the agent never finalized) | Treat as NOT verified regardless of `task_status`: the SubagentStop hook captured it (crash / truncation / forgotten `finalize`), it is not an agent-verified result. Surface it as incomplete and resume the agent to actually finalize, rather than presenting the `COMPLETE` summary path. |

## Handoffs

- `agent-contract-handoff` -- the full field schema, conditional triggers, sub-field tables, and `plan_status` enum.
- `agent-protocol` -- the produce side; how the subagent built the contract this skill consumes.
- `orchestrator-present-approval` -- the structured consent flow when `approval_id` is present.
- `memory` -- curation mechanics for `memorialize_suggestions` / `memory_suggestions`.
