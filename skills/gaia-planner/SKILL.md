---
name: gaia-planner
description: Use when planning features, creating briefs, or decomposing work into tasks
metadata:
  user-invocable: false
  type: technique
---

# Gaia Planner

Two-phase planning: brief creation, then task dispatch. The brief captures
WHAT and WHY. Tasks capture DO. Each phase stops at its boundary so the
user can course-correct before work begins.

## When to Activate

- User describes a feature, change, or problem to plan
- User runs `/gaia-plan`
- An existing brief.md needs task decomposition

Skip when the user wants to execute tasks (that is agent work, not planning).

## Phase 1: Brief Creation

For the full process -- sizing, questions, template structure, and
acceptance criteria rules -- see `reference.md` in this directory.

**Quick path:** Read project-context.json -> size the work -> ask questions
(0 for S, 2-3 for M, 4-6 for L) -> write brief.md from template.

## Phase 2: Task Dispatch

For task decomposition rules, context slicing, and the verify gate,
see `reference.md` in this directory.

**Quick path:** Read brief.md -> decompose into Tasks -> each task carries
its own context slice + ACs + verify command -> dispatch via TaskCreate.

## Anti-Patterns

- **Skipping project-context** -- constraints discovered during execution waste agent time and user patience.
- **Interrogation** -- more than 6 questions exhausts the user. Capture first, clarify gaps.
- **Fat tasks** -- a task that needs more than one context window forces the agent to lose track. Split it.
- **Thin tasks** -- a task without its own context slice forces the agent to read the full brief. Inline the slice.
- **Auto-advancing** -- jumping from brief to tasks without stopping robs the user of review.
