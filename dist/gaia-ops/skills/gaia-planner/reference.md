# Gaia Planner -- Reference

## Phase 1: Create Plan

### Step 1: Read the Brief

Read the brief.md provided by the orchestrator. Extract:
- Objectives and approach
- Acceptance criteria with verify commands
- Constraints from project-context
- Out of scope boundaries

No brief -> BLOCKED. Tell the orchestrator to create one first (brief-spec skill).

### Step 2: Decompose into Tasks

Each task MUST:
- **Fit in one context window.** If you need to say "see also", split it.
- **Name its agent target.** Route by domain: terraform keywords -> terraform-architect, k8s/helm -> gitops-operator, code/test/build -> developer, gaia internals -> gaia-system.
- **Carry its own context slice.** The agent receives the task description, not the brief. Inline relevant constraints, file paths, and tech stack.
- **Have ACs with verify commands.** Binary pass/fail.

Task sizing: aim for 2-5 minutes of agent work. A task that takes 15 minutes
is three tasks that should have been split.

### Step 3: Write plan.md

Use the structure below. Write to the same directory as the brief:
`.claude/project-context/briefs/{feature-name}/plan.md`

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
- AC: `{verify command}`
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
