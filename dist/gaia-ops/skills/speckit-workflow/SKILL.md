---
name: speckit-workflow
description: Use when planning features using the Spec-Kit framework (plan, tasks)
metadata:
  user-invocable: false
  type: technique
---

# Spec-Kit Workflow

Domain workflow for feature planning. Artifacts go to `{speckit_root}/{feature-name}/`.
Templates live at `{project-root}/speckit/templates/`. For artifact formats,
task metadata, and agent routing, see `reference.md`.

**Path resolution:** `speckit_root` from project-context.json `paths.speckit_root`.
Default: `specs/`. Agent definition's Context Resolution section is authoritative.

## Flow

```
spec.md (from orchestrator) -> Plan -> Tasks -> orchestrator (routes tasks)
```

**Spec is WHAT. Plan is HOW. Tasks are DO.** Each phase stops at its boundary
because auto-advancing skips the user's chance to catch design mistakes before
they become implementation mistakes.

## Phase 0: Governance Sync (before any phase)

1. Read `project-context.json`. Missing -> BLOCKED, ask user for `npx gaia-scan`.
2. Sync `## Stack Definition` in `{speckit_root}/governance.md` from project-context.
   Missing governance.md -> create from template. Exists -> update Stack Definition only.
3. Read governance.md -- this is your working context for all phases.

## Phase 1: Plan

**Input:** Validated `spec.md` (no unresolved `[NEEDS CLARIFICATION]`).
**Output:** `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`.

Read `speckit/templates/plan-template.md` before writing anything. Templates
encode hard-won structure decisions -- skipping them produces artifacts that
downstream phases cannot consume reliably.

1. **Verify prerequisite.** Read spec.md. Missing -> BLOCKED. Unresolved markers -> resolve first.
2. **Initialize plan.md** from template. Exists -> skip.
3. **Clarify ambiguities** (max 5 questions, one at a time). Integrate answers into spec.md.
4. **Fill Technical Context** from project-context. Ask user for unknowns.
5. **Constitution Check** against governance.md. Violations -> document with justification.
6. **Research.** For each unknown: best practices -> `research.md` (Decision, Rationale, Alternatives).
7. **Design.** Entities -> `data-model.md`, API contracts -> `contracts/`, test scenarios -> `quickstart.md`.
8. **Re-check constitution.** New violations -> refactor, return to step 7.
9. **Describe task approach** in plan.md -- do not create tasks.md yet.
10. **STOP.** Suggest: `/speckit.tasks`.

## Phase 2: Tasks

**Input:** `plan.md` + design docs.
**Output:** `{feature-dir}/tasks.md`.

Read `speckit/templates/tasks-template.md` first.

The executing agent receives a single task, not the full plan. If a task says "implement the auth service" without specifying the tech stack, file paths, and exit criteria, the agent guesses -- and guesses diverge from the plan. Every task carries its own context slice.

1. **Load all design documents** -- plan.md (required), others if they exist.
2. **Generate tasks by category:** Setup -> Tests (TDD) -> Core -> Integration -> Polish.
3. **Enrich every task** with inline metadata (see `reference.md` for format and agent routing).
4. **Generate dependency graph** in YAML at bottom of tasks.md.
5. **Cross-artifact validation:** every spec requirement covered? Every contract has a test task? Gaps -> pause for user approval.
6. **Report:** task count, coverage percentage, issues found.

## Anti-Patterns

- **Template-skipping** -- artifacts without templates produce structures downstream phases cannot parse.
- **Auto-advancing** -- jumping plan to tasks without stopping robs the user of design review.
- **Thin tasks** -- tasks without context slices force agents to guess at intent, producing drift.
- **Over-specifying parallelism** -- marking `[P]` when tasks share state causes race conditions.
