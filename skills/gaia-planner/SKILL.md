---
name: gaia-planner
description: Use when planning features or decomposing work into tasks from a brief
metadata:
  user-invocable: false
  type: technique
---

# Gaia Planner

Plan creation from briefs. The planner produces plan.md and returns it
to the orchestrator. The orchestrator owns task dispatch and execution.

## When to Activate

- A brief.md exists and needs to become an execution plan
- A plan.md needs revision or restructuring

## Create Plan

Read the brief.md. Decompose into tasks. Write plan.md using the
plan structure defined in `reference.md`. For the full decomposition
process and task rules, see `reference.md`.

**Quick path:** Read brief -> decompose into tasks -> write plan.md
-> return plan.md to orchestrator.

Each task in plan.md carries: goal, AC with verify command, agent
assignment, context slice, and dependencies. This gives the orchestrator
everything it needs to dispatch using its own goal+AC model.

## Anti-Patterns

- **Dispatching agents** -- the planner writes the plan; the orchestrator dispatches. If you have Agent() in your tools, something is wrong.
- **Fat tasks** -- a task needing more than one context window forces the agent to lose track. Split it.
- **Thin tasks** -- a task without its own context slice forces the agent to read the full brief. Inline the slice.
- **Vague ACs** -- every task needs a verify command the orchestrator can run post-dispatch. No verify command = no way to confirm completion.
