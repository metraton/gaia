---
name: schedule-task
description: Reference material for dispatch parameter extraction and prompt templates. The orchestrator's dispatch execution section covers the core principles -- load this skill for detailed templates and examples.
metadata:
  user-invocable: false
  type: reference
---

# Schedule Task

On-demand reference for parameter extraction, prompt templates, and task
classification details. The orchestrator's "Dispatch execution" identity
section covers when and how to dispatch. Load this skill when you need
the exact templates or extraction patterns.

## Task classification

| Measurable? | Improvable? | Type | Action |
|-------------|-------------|------|--------|
| Yes | Yes (iterative) | agentic-loop | Build loop prompt with all params |
| Yes | No (pass/fail) | simple-task | Build focused prompt, no loop |
| Creatable | Yes | two-phase | Phase 1: create eval. Phase 2: agentic-loop |
| No | N/A | manual-review | Warn user, offer alternatives |

## Parameter extraction (agentic-loop only)

Required: `goal`, `eval_command`, `metric`, `direction`, `threshold`
Optional: `max_iterations` (default 20), `files_in_scope`, `branch` prefix

If any required param is missing -- ASK the user. Do not guess eval commands
or thresholds. See `reference.md` for extraction examples and confirmation
patterns.

## Prompt templates

For agentic-loop tasks, use the template in `reference.md`. The prompt MUST
include the `Carga la skill agentic-loop` header -- this triggers skill
injection in the agent.

For simple tasks, build a focused objective prompt without the loop header.
For two-phase tasks, dispatch Phase 1 first (create eval), then Phase 2 (loop).

## Scheduling with CronCreate

When the user wants recurring execution ("cada noche", "cron", "schedule"):
- Use `CronCreate` with the built prompt
- One-shot: `recurring=false`
- Recurring: `recurring=true` -- warn about 7-day limit
- See `reference.md` for cron expression examples

## Reading loop_status

When an agent returns `loop_status` in its `json:contract`:
- `"iterating"` -- agent still working, wait
- `"threshold_reached"` / `"complete"` -- present: baseline -> final in N iterations
- `"stopped"` -- present what was achieved + why it stopped
- `"blocked"` -- present blocker, ask user

## Anti-Patterns

- Dispatching a loop without `eval_command` -- the agent cannot measure progress
- Including loop protocol details in the dispatch prompt -- `agentic-loop` skill handles that
- Micromanaging the agent's iterations -- dispatch the goal, not the steps
- Guessing thresholds the user did not provide
