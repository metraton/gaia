---
name: speckit-workflow
description: Use when planning features using the Spec-Kit framework (specify, plan, tasks, implement)
user-invocable: false
---

# Spec-Kit Workflow

Domain workflow for feature planning. For artifact templates and task enrichment rules, see `reference.md`.

## Flow

```
Idea → /speckit.specify → spec.md
             ↓
      /speckit.plan → plan.md + research.md + data-model.md
             ↓
      /speckit.tasks → tasks.md (enriched)
             ↓
      /speckit.implement → Execution
```

All artifacts go to: `{speckit-root}/specs/{feature-name}/`

## Phase 0: Governance Sync (MANDATORY)

1. Read `project-context.json`
2. Update `## Stack Definition` in `{speckit-root}/governance.md` from project-context
   - Missing → create from template. Exists → update only Stack Definition
3. Read updated governance.md — this is your working context

If `project-context.json` missing → BLOCKED, ask user to run `npx gaia-init`.

## Phase 1: Specify

**Trigger:** User describes a feature idea
1. Parse description, ask clarifying questions for ambiguities
2. Generate spec.md following template in `reference.md`
3. Mark unresolved ambiguities with `[NEEDS CLARIFICATION: question]`
4. Present for user validation

## Phase 2: Plan

**Trigger:** User wants to plan implementation
**Prerequisite:** spec.md validated — no unresolved `[NEEDS CLARIFICATION]`
1. Load and analyze spec.md
2. Fill Technical Context (ask user if needed)
3. Execute Constitution Check
4. Generate research.md, data-model.md, contracts/
5. Complete plan.md
6. STOP — do NOT create tasks.md

## Phase 3: Tasks

**Trigger:** User wants to generate tasks
**Prerequisite:** plan.md exists
1. Load plan.md, data-model.md, contracts/
2. Generate tasks by category: Setup, Tests [P], Core, Integration, Polish [P]
3. Apply enrichment to EVERY task (agent, tier, tags, verify, parallel markers)
4. Add HIGH RISK warning to T2/T3 tasks

## Phase 4: Implement

**Trigger:** User wants to execute tasks
1. Load tasks.md
2. For each task: if HIGH RISK → auto-trigger analysis, ask confirmation
3. Mark as `[x]` when complete, report progress

## Task Enrichment Rules

Every task gets automatic metadata:
- **Agent:** detect from keywords (terraform → `terraform-architect`, kubectl → `gitops-operator`, code/test → `devops-developer`)
- **Security Tier:** classify using `security-tiers` skill
- **Tags:** technology (#terraform, #kubernetes), domain (#database, #security), type (#setup, #test)
- **Verify:** a command or observable outcome confirming completion
