# Gaia Planner -- Reference

## Phase 1: Create Plan

### Step 1: Read the Brief

Read the brief.md provided by the orchestrator. Extract:
- Objectives and approach
- Acceptance criteria: id, description, evidence{type, shape}, artifact
- Constraints from project-context
- Out of scope boundaries

Every task you write must cite which brief AC-id(s) it satisfies. A task
with no AC-id satisfies nothing observable; split or delete it.

No brief -> BLOCKED. Tell the orchestrator to create one first (brief-spec skill).

### Step 2: Decompose into Tasks

Each task MUST:
- **Fit in one context window.** If you need to say "see also", split it.
- **Name its agent target.** Route by domain: terraform keywords -> terraform-architect, k8s/helm -> gitops-operator, code/test/build -> developer, gaia internals -> gaia-system.
- **Carry its own context slice.** The agent receives the task description, not the brief. Inline relevant constraints, file paths, and tech stack.
- **Cite the brief AC-ids it satisfies.** Every task lists `satisfies: [AC-1, AC-3]`. Unreferenced tasks get removed; uncovered ACs get new tasks.
- **Have a task-level AC with a command.** Binary pass/fail, internal to the task (build green, test passes, file exists).
- **Inherit the evidence slot from the brief AC.** The task AC is the technical proof (e.g. `pytest tests/auth/ -q` exits 0); the brief AC (e.g. login URL flow) is verified separately by the orchestrator post-dispatch.

Two AC levels, one per layer:
- **Brief AC (product):** what the user observes. Verified once, post-execution.
- **Task AC (technical):** what the agent must produce. Verified per task.

A feature is COMPLETE only when every task AC passes AND every brief AC's
evidence has been executed and persisted.

Task sizing: aim for 2-5 minutes of agent work. A task that takes 15 minutes
is three tasks that should have been split.

### Step 3: Write plan.md

Use the structure below. Write plan.md to the same directory as the brief.
Do not reconstruct the path from the feature name -- read the brief's actual
directory path (which may have any prefix: `open_`, `in-progress_`, `closed_`)
and write plan.md there. This keeps the skill prefix-tolerant.

If the directory does not exist yet, default to:
`.claude/project-context/briefs/open_{feature-name}/plan.md`

## Plan Structure

```markdown
---
status: draft
brief: ./brief.md
created: {date}
---

# Plan: {Feature Name}

## Approach
{Technical strategy -- 3-5 sentences}

## Tasks

### T1: {Task title}
- agent: {agent-type}
- status: pending
- satisfies: [AC-1, AC-2]   # brief AC-ids this task contributes to
- AC: `{verify command}`    # task-level technical proof, binary pass/fail
- blocked-by: none

**Context:** {Inline context slice}
**Change:** {Exact files + what changes}

## Execution Order
{Dependency graph}
```

Fill in:
- Approach (technical strategy, 3-5 sentences)
- Tasks with agent, status, AC, blocked-by, context, and change description
- Execution order (dependency graph)

### Step 4: Task List Checkpoint

Before executing any tasks, present the complete task list and wait for
confirmation. The checkpoint must show:

- Task number, title, and target agent
- Dependencies (blocked-by relationships)
- Execution order

Ask: "Here are the tasks I plan to execute. Confirm to proceed, or
suggest changes." Do not dispatch until the user confirms.

## Agent Routing Reference

Use this table to assign agent types to tasks in plan.md. The orchestrator
uses these assignments when dispatching.

| Domain Signal | Agent |
|---------------|-------|
| Terraform, IaC, cloud resources | `terraform-architect` |
| Kubernetes, Helm, Flux, manifests | `gitops-operator` |
| Live cluster, pods, logs, diagnostics | `cloud-troubleshooter` |
| App code, tests, CI/CD, Docker | `developer` |
| Gaia hooks, skills, agents, routing | `gaia-system` |
| Workspace, memory, email, automation | `gaia-operator` |
