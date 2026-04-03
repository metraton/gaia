---
name: speckit-workflow
description: Use when planning features using the Spec-Kit framework (plan, tasks)
metadata:
  user-invocable: false
---

# Spec-Kit Workflow

Domain workflow for feature planning. Artifacts go to `{speckit_root}/{feature-name}/`.
Templates live at `{project-root}/speckit/templates/`. Read the template before generating any artifact.

**Path resolution:** `speckit_root` comes from project-context.json `paths.speckit_root`. Default: `specs/` relative to project root. The agent definition's Context Resolution section is authoritative for path resolution.

## Flow

```
Completed spec.md (from orchestrator)
        |
    Plan -> plan.md + research.md + data-model.md + contracts/
        |
    Tasks -> tasks.md (enriched, self-contained)
        |
    Return to orchestrator (routes tasks to agents)
```

## Phase 0: Governance Sync (MANDATORY before any phase)

1. Read `project-context.json`. Missing -> BLOCKED, ask user for `npx gaia-scan`.
2. Update `## Stack Definition` in `{speckit_root}/governance.md` from project-context.
   Missing governance.md -> create from template. Exists -> update Stack Definition only.
3. Read updated governance.md -- this is your working context for all phases.

---

## Phase 1: Plan

**Input:** Validated `spec.md` (no unresolved `[NEEDS CLARIFICATION]`).
**Output:** `plan.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`.
**Template:** Read `speckit/templates/plan-template.md` FIRST.

### Procedure

1. **Verify prerequisite.** Read spec.md. If missing -> BLOCKED, ask orchestrator to provide a completed spec. If unresolved
   `[NEEDS CLARIFICATION]` markers remain -> STOP, resolve them first.

2. **Initialize plan.md** from the plan template. If plan.md already exists, skip (do not overwrite).

3. **Clarify planning ambiguities** (max 5 questions):
   - Scan for: functional scope, data model gaps, non-functional targets,
     integration unknowns, edge cases, vague terminology.
   - Ask one question at a time. Integrate answers into spec.md immediately.

4. **Fill Technical Context** in plan.md:
   - Language/version, dependencies, storage, testing framework, target platform.
   - Ask user for anything you cannot infer.

5. **Run Constitution Check** against governance.md:
   - GitOps patterns enforced? HTTPS for external endpoints? Health checks?
     No `:latest` image tags? Scope boundaries respected?
   - Violations -> document in Complexity Tracking with justification.
   - Unjustifiable violations -> ERROR "Simplify approach first."

6. **Phase 0 -- Research.** For each unknown or technology choice:
   - Research best practices. Consolidate in `research.md` with:
     Decision, Rationale, Alternatives considered.

7. **Phase 1 -- Design.** From spec + research:
   - Extract entities -> `data-model.md` (fields, relationships, validations).
   - Generate API contracts from functional requirements -> `contracts/` directory.
   - Generate failing contract tests (one per endpoint).
   - Extract test scenarios from user stories -> `quickstart.md`.

8. **Re-run Constitution Check.** New violations -> refactor, return to step 7.

9. **Describe Phase 2 task approach** in plan.md -- do NOT create tasks.md.

10. **STOP.** Plan phase is complete. Suggest next step: `/speckit.tasks`.

---

## Phase 2: Tasks

**Input:** `plan.md` + available design docs (`data-model.md`, `contracts/`, `research.md`).
**Output:** `{feature-dir}/tasks.md`.
**Template:** Read `speckit/templates/tasks-template.md` FIRST.

### Procedure

1. **Verify prerequisite.** plan.md must exist. If missing -> ERROR.

2. **Load all available design documents:**
   - plan.md (REQUIRED) -- tech stack, architecture, file structure.
   - data-model.md (if exists) -- entities and relationships.
   - contracts/ (if exists) -- API specifications.
   - research.md (if exists) -- technical decisions.
   - quickstart.md (if exists) -- test scenarios.

3. **Read the tasks template** from `speckit/templates/tasks-template.md`.

4. **Generate tasks by category:**
   - **Setup:** Project init, dependencies, linting, config.
   - **Tests [P]:** One per contract, one per integration scenario (TDD -- tests first).
   - **Core:** One per entity model, one per service, one per endpoint/command.
   - **Integration:** DB connections, middleware, logging, external services.
   - **Polish [P]:** Unit tests, performance tests, docs, cleanup.

5. **Apply parallelism rules:**
   - Different files with no shared dependencies -> mark `[P]`.
   - Same file -> sequential (no `[P]`).

6. **Order by dependency:**
   Setup -> Tests -> Models -> Services -> Endpoints -> Integration -> Polish.

7. **Enrich EVERY task with inline metadata:**
   ```markdown
   - [ ] T001 Description
     - context: relevant plan slice (tech stack, architecture decisions)
     - files: expected file paths
     - depends-on: task IDs or `none`
     - exit-criteria: `command` expected outcome
     - suggested-agent: {agent}
     - tier: {T0|T1|T2|T3}
     <!-- Tags: #tag1 #tag2 -->
   ```
   - **Agent:** terraform keywords -> `terraform-architect`, kubectl/helm -> `gitops-operator`,
     code/test/build -> `developer`, logs/monitoring -> `cloud-troubleshooter`.
   - **Tier:** T0 (read), T1 (validate), T2 (simulate), T3 (mutate).
   - **Tags:** tech (#terraform, #kubernetes), domain (#database, #security), type (#setup, #test).
   - **T2/T3 tasks** get: `<!-- HIGH RISK: Analyze before execution -->`.

8. **CRITICAL -- every task MUST include:**
   - An `exit-criteria:` line with a command or observable outcome.
   - Enough context for the executing agent to work without loading multiple files.

9. **Generate dependency graph** in YAML at the bottom of tasks.md.

10. **Cross-artifact validation:**
    - All spec requirements covered by at least one task?
    - All contracts have test tasks? All entities have model tasks?
    - CRITICAL gaps -> pause, require user approval.
    - LOW/MEDIUM gaps -> add notes to Dependencies section.

11. **Report** with: task count, coverage percentage, any issues found.
    Tasks are returned to the orchestrator for execution.

---

## Task Enrichment Rules (applies to Phase 2)

Every task gets automatic metadata:
- **Agent:** detect from keywords (terraform -> `terraform-architect`, kubectl -> `gitops-operator`,
  code/test -> `developer`, monitoring/logs -> `cloud-troubleshooter`).
- **Security Tier:** classify using `security-tiers` skill.
- **Tags:** technology (#terraform, #kubernetes), domain (#database, #security), type (#setup, #test).
- **Exit criteria:** a command or observable outcome confirming completion.
- **Context slice:** relevant portion of the plan so the task is self-contained.

## Critical Rules

1. **Always read templates** from `speckit/templates/` before generating any artifact.
2. Every task MUST include: exit criteria + enough inline context to be self-contained.
3. Tasks must be **self-contained** -- executable by the assigned agent without SpecKit knowledge.
4. Each phase STOPS at its boundary. Do not auto-advance to the next phase.
5. Spec is WHAT (no implementation details). Plan is HOW. Tasks are DO.
