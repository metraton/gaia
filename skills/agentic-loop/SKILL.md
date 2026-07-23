---
name: agentic-loop
description: Use when the orchestrator injects "Carga la skill agentic-loop" with a goal, eval_command, metric, and threshold
---

# Agentic Loop

Iterative improvement through small, reversible changes evaluated against a single metric. Each iteration is one hypothesis, one edit, one eval, one decision. The metric decides -- not you.

## Parameters (from orchestrator prompt)

`goal`, `eval_command`, `metric`, `direction` (higher/lower), `threshold`, `max_iterations`, `files_in_scope`, `branch`

## Setup

1. Read every file in `files_in_scope` deeply -- understand before changing
2. `git checkout -b {branch}`
3. Run `eval_command` -- parse `METRIC {name}={number}` from stdout -- this is your baseline
4. Write `state.json` and `worklog.md` (schemas in `reference.md`)
5. `git commit -m "baseline: {metric} {value}"`

## Loop (repeat until threshold, max_iterations, or stop)

1. **HYPOTHESIZE** -- based on worklog insights and last failure. When stuck, re-read source files; thinking longer beats trying faster
2. **EDIT** -- one focused change. Smaller diffs are easier to evaluate and reverse
3. **EVALUATE** -- run `eval_command`, parse `METRIC {name}={number}`
4. **DECIDE** (mechanically, not judgment):
   - Improved (or equal with less code) -- KEEP -- `git add -A` then `git commit -m "improve: {metric} {old}->{new}"`
   - Same or worse -- DISCARD -- `git checkout -- .` then `git clean -fd`
5. **LOG** -- append to `worklog.md`: run number, what changed, result, insight, next idea
6. **UPDATE** -- write `state.json` with current values
7. **ESCALATE** if needed:
   - 3 consecutive discards -- REFINE (adjust within current strategy)
   - 5 consecutive discards -- PIVOT (structurally different approach)
   - 3 pivots without a keep -- STOP and report blockers
8. Every 10 iterations: re-read `files_in_scope`, review worklog "What's Been Tried", recalibrate

## Termination

- **Threshold reached** -- `git commit -m "final: {metric} {baseline}->{final} in N iterations"`, write summary
- **Max iterations** -- report best achieved vs threshold
- **Stop from escalation** -- report what was tried and what blocked progress
- All paths: finalize `state.json` (status: complete/stopped), write summary in `worklog.md`

## Contract Integration

Include `loop_state` as a TOP-LEVEL field of your `agent_contract_handoff` (a
sibling of `agent_status`/`evidence_report`, NOT nested inside `agent_status`)
on every response. This is the exact field name and shape
`parse_loop_state`/`_check_loop_state_blocking` read in
`hooks/modules/agents/contract_validator.py` -- a `loop_status` field, or one
nested under `agent_status`, is invisible to that check, so the loop's
COMPLETE-blocking safeguard (see `agent-contract-handoff` and
`agent-protocol/examples.md` #7) never engages:

```json
"loop_state": {
  "iteration": 5,
  "max_iterations": 10,
  "metric": 94.5,
  "threshold": 98
}
```

Do NOT return `agent_state: "COMPLETE"` until the loop finishes. The user may be away for hours.

## Rules

- **Loop forever.** Never ask "should I continue?" The metric and thresholds decide when to stop. The user may be away for hours.
- **One change per iteration.** Multiple changes make it impossible to isolate what helped.
- **Metric is king.** Personal judgment about code quality does not override the number.
- **Simpler wins ties.** Removing code for equal performance is a keep.
- **Think longer when stuck.** Re-read source files before trying faster. Fresh context beats more iterations.
- **Retreat, don't thrash.** Same idea reverting repeatedly means the approach is wrong -- pivot.

## Anti-Patterns

- Making multiple changes per iteration -- cannot isolate what helped or hurt
- Skipping eval after a change -- invisible regressions compound
- Continuing after 3 pivots without improvement -- diminishing returns; stop and report
- Using `git clean -fdx` instead of `-fd` -- destroys untracked config files needed by eval
- Editing state.json by hand instead of writing it atomically after each phase
