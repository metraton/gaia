---
name: agent-response
description: Use when an agent returns an agent_contract_handoff response that needs to be interpreted and presented to the user
metadata:
  user-invocable: false
  type: protocol
---

# Agent Response Protocol

The orchestrator loads this to interpret a returned `agent_contract_handoff` and decide the next action keyed on `plan_status` -- the consume side of the contract the subagent produced. For the field schema (every field, required/conditional status, triggers, the INPUT-vs-OUTPUT name collision), see `agent-contract-handoff`; this skill does not restate field meanings, it tells you what to do with them.

## State machine

`plan_status` is the first field read; it selects the branch. The five values are canonical in `VALID_PLAN_STATUSES` (`gaia.state`, re-exported by `response_contract.py`) -- their meanings live in `agent-contract-handoff`, not here.

```
parse_contract(agent_output)  ->  read agent_status.plan_status
  |- COMPLETE         -> relay user_facing_summary if present & N=1, else summarize key_outputs; surface verification, then close
  |- APPROVAL_REQUEST -> split on approval_id (present: present-approval; absent: plan options)
  |- NEEDS_INPUT      -> AskUserQuestion, then SendMessage the answer
  |- BLOCKED          -> present open_gaps; new dispatch or accept the limitation
  +- IN_PROGRESS      -> SendMessage to resume (runtime caps consecutive retries at 2)
```

Before any branch runs, the contract must parse. A block that fails `parse_contract` (`contract_validator.py`) is treated as missing -- see Error handling.

## Mandatory action per plan_status

| `plan_status` | Action |
|---|---|
| `COMPLETE` | If `user_facing_summary` is present AND this is a single-agent turn (N=1), relay it near-verbatim -- adapt only to the user's language, do not re-synthesize -- because the subagent already wrote the human-shaped summary and re-summarizing its `key_outputs` only spends tokens to restate what it said. If the field is absent, or N>1 (multiple agents being consolidated), summarize `key_outputs` in 3-5 bullets as before. Either way, surface `verification.result` / `verification.details` -- that block is the proof the work landed, and relaying it is what lets the user trust the increment rather than take "done" on faith. Mention `cross_layer_impacts` and `open_gaps` when non-empty. |
| `APPROVAL_REQUEST` | Split on `approval_request.approval_id`: present -> load `Skill('orchestrator-present-approval')`; absent -> present the plan with options (execute / modify / cancel) and on execute/modify resume the SAME agent via `SendMessage`. It splits because a hook-issued `approval_id` carries a pending T3 grant that needs the structured consent flow, while an `APPROVAL_REQUEST` with no `approval_id` carries no grant and only needs a direction (execute / modify / cancel) back to the same agent. |
| `NEEDS_INPUT` | `AskUserQuestion` with the options in `next_action`, then `SendMessage` the answer back to resume. |
| `BLOCKED` | Present `open_gaps` to the user. If they give direction, dispatch a NEW agent addressing the blocker; if they accept the limitation, close the task as incomplete and move on. |
| `IN_PROGRESS` | `SendMessage` to resume the agent. The runtime caps consecutive `IN_PROGRESS` at 2 (`_MAX_IN_PROGRESS_RETRIES` in `state_tracker.py`) -- do not loop past that expecting progress; treat a third as a stall and escalate. |

## The fields easy to drop

These ride alongside `plan_status` and carry signal the orchestrator loses if it reads only the status.

**`verification`** -- covered in COMPLETE above. It is required only on `COMPLETE` and its `result` must equal `"pass"` (`VERIFICATION_RESULT_MUST_BE_PASS`, `contract_validator.py`); surface `result` and `details` so the user sees the proof, never just the word "done."

**`user_facing_summary`** -- the one human-audience field (every other field is machine-audience for the orchestrator). On a single-agent `COMPLETE` it is what you relay to the user, near-verbatim and language-adapted, *instead of* re-synthesizing `key_outputs`; that is the whole point -- the subagent wrote the summary once, so re-summarizing duplicates work the user never sees value in. It is optional and additive: when absent, fall back to `key_outputs`; when multiple agents are in flight (N>1), ignore it and synthesize across them, because no single agent's summary speaks for the consolidated result.

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

## Handoffs

- `agent-contract-handoff` -- the full field schema, conditional triggers, sub-field tables, and `plan_status` enum.
- `agent-protocol` -- the produce side; how the subagent built the contract this skill consumes.
- `orchestrator-present-approval` -- the structured consent flow when `approval_id` is present.
- `memory` -- curation mechanics for `memorialize_suggestions` / `memory_suggestions`.
