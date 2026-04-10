---
name: speckit-workflow
description: Use when planning features using the Spec-Kit framework (plan, tasks)
metadata:
  user-invocable: false
  type: technique
---

# Spec-Kit Workflow

Domain workflow for feature planning. Artifacts go to `{speckit_root}/{feature-name}/`.
Templates live at `{project-root}/speckit/templates/`. For artifact format summaries
and task metadata examples, see `reference.md`.

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

---

## Phase 1: Plan

**Input:** Validated `spec.md` (no unresolved `[NEEDS CLARIFICATION]`).
**Output:** `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`.

Read `speckit/templates/plan-template.md` before writing anything. Templates
encode hard-won structure decisions -- skipping them produces artifacts that
downstream phases cannot consume reliably.

### Procedure

1. **Verify prerequisite.** Read spec.md. Missing -> BLOCKED. Unresolved
   `[NEEDS CLARIFICATION]` markers -> resolve before proceeding (ambiguity
   in the spec becomes wrong assumptions in the plan).

2. **Initialize plan.md** from template. Exists -> skip (do not overwrite).

3. **Clarify planning ambiguities** (max 5 questions, one at a time).
   Scan for: scope gaps, data model unknowns, non-functional targets,
   integration points, vague terms. Integrate answers into spec.md.

4. **Fill Technical Context** -- language, dependencies, storage, testing,
   platform. Ask user for anything you cannot infer from project-context.

5. **Constitution Check** against governance.md. Violations -> document
   with justification. Unjustifiable violations -> ERROR "Simplify first."

6. **Research.** For each unknown: research best practices, consolidate in
   `research.md` (Decision, Rationale, Alternatives considered).

7. **Design.** From spec + research: entities -> `data-model.md`, API
   contracts -> `contracts/`, failing contract tests, test scenarios -> `quickstart.md`.

8. **Re-check constitution.** New violations -> refactor, return to step 7.

9. **Describe task approach** in plan.md -- do NOT create tasks.md yet.

10. **STOP.** Suggest: `/speckit.tasks`.

---

## Phase 2: Tasks

**Input:** `plan.md` + design docs (`data-model.md`, `contracts/`, `research.md`, `quickstart.md`).
**Output:** `{feature-dir}/tasks.md`.

Read `speckit/templates/tasks-template.md` first.

### Why tasks must be self-contained

The executing agent receives a single task, not the full plan. If a task
says "implement the auth service" without specifying the tech stack, file
paths, and exit criteria, the agent guesses -- and guesses diverge from the
plan. Every task carries its own context slice so the agent works from
evidence, not inference.

### Procedure

1. **Load all design documents** -- plan.md (required), others if they exist.

2. **Generate tasks by category:**
   Setup -> Tests (TDD) -> Core (models, services, endpoints) -> Integration -> Polish.

3. **Enrich every task** with inline metadata (see `reference.md` for format):
   context slice, files, depends-on, exit-criteria, suggested-agent, tier, tags.
   - **Agent routing:** terraform keywords -> `terraform-architect`, kubectl/helm -> `gitops-operator`, code/test -> `developer`, logs/monitoring -> `cloud-troubleshooter`.
   - **Tier:** classify using `security-tiers` skill. T2/T3 get `<!-- HIGH RISK -->`.
   - **Parallelism:** different files with no shared deps -> `[P]`. Same file -> sequential.

4. **Generate dependency graph** in YAML at bottom of tasks.md.

5. **Cross-artifact validation:** every spec requirement covered? Every contract
   has a test task? Every entity has a model task? Critical gaps -> pause for
   user approval. Minor gaps -> document in Dependencies section.

6. **Report:** task count, coverage percentage, issues found.

## Anti-Patterns

- **Template-skipping** -- generating artifacts without reading templates produces structures that downstream phases cannot parse, breaking the pipeline.
- **Auto-advancing** -- jumping from plan to tasks without stopping robs the user of design review, the cheapest place to catch architectural mistakes.
- **Thin tasks** -- tasks without context slices force executing agents to load multiple files and guess at intent, producing drift from the plan.
- **Over-specifying parallelism** -- marking tasks `[P]` when they share state causes race conditions that are harder to debug than sequential slowness.
