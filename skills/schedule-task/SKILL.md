---
name: schedule-task
description: Use when the user wants to dispatch work to an agent -- "mejorar", "iterar", "loop", "schedule", "cron", "cada noche", "hasta que", "trabaja en esto", "optimiza", "programa"
metadata:
  user-invocable: false
  type: technique
---

# Schedule Task

Classify the user's request, extract parameters, build the right prompt, and dispatch to a specialist. The orchestrator dispatches the goal -- it never executes the loop or micromanages iterations.

## Step 1: Is it measurable?

| Condition | Classification |
|-----------|---------------|
| A test/eval script exists | measurable |
| One can be created (tests, benchmarks, health checks) | creatable |
| No way to measure automatically | not measurable |

## Step 2: What type of task?

| Measurable? | Improvable? | Type | Action |
|-------------|-------------|------|--------|
| Yes | Yes (iterative) | agentic-loop | Build loop prompt with all params |
| Yes | No (pass/fail) | simple-task | Build focused prompt, no loop |
| Creatable | Yes | two-phase | Phase 1: create eval. Phase 2: agentic-loop |
| No | N/A | manual-review | Warn user, offer alternatives |

## Step 3: Extract parameters (agentic-loop only)

Required: `goal`, `eval_command`, `metric`, `direction`, `threshold`
Optional: `max_iterations` (default 20), `files_in_scope`, `branch` prefix

If any required param is missing -- ASK the user. Do not guess eval commands or thresholds.

## Step 4: Build the prompt

For agentic-loop tasks, use the template in `reference.md`. The prompt MUST include the `Carga la skill agentic-loop` header -- this triggers skill injection in the agent.

For simple tasks, build a focused objective prompt without the loop header.
For two-phase tasks, dispatch Phase 1 first (create eval), then Phase 2 (loop).

## Step 5: Choose agent

| Domain | Agent |
|--------|-------|
| Hooks, agents, skills, routing, gaia internals | gaia-system |
| App code, tests, CI/CD, packages | developer |
| K8s manifests, Helm, Flux | gitops-operator |
| IaC, Terraform, Terragrunt | terraform-architect |
| Live diagnostics, logs, pods | cloud-troubleshooter |

## Step 6: Schedule (if requested)

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
- Scheduling without confirming measurability first
- Guessing thresholds the user did not provide
