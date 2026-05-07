# Gaia Planner -- Reference

## DB is the source of truth

Briefs and plans live in the Gaia substrate database (`~/.gaia/gaia.db`).
This reference describes how to decompose a brief into tasks and what shape
the plan content takes. The plan content is persisted into the brief's
`approach` field (interim flow) via `gaia brief edit <name>`. There is no
`plan.md` on disk and no `open_<feature>/` directory -- those are legacy
and will be removed in the `legacy-cleanup` brief. See `SKILL.md` for the
CLI flow; this file is the decomposition manual.

## Phase 1: Create Plan

### Step 1: Read the brief

Read the brief through the DB:

```bash
gaia brief show <name> --workspace=<ws> --json
```

From the JSON, extract:

- Objectives and approach
- Acceptance criteria: `id`, `description`, `evidence{type, shape}`, `artifact`
- Constraints from project-context (the body's `## Context` section)
- Out of scope boundaries

Every task you write must cite which brief AC-id(s) it satisfies. A task
with no AC-id satisfies nothing observable; split or delete it.

If `gaia brief show <name>` errors with "not found" -> BLOCKED. Tell the
orchestrator to create the brief first via the `brief-spec` skill. Do not
fall back to reading `<workspace>/.claude/project-context/briefs/...` --
the DB is authoritative; a file there is either build output or stale
legacy.

### Step 2: Decompose into tasks

Each task MUST:

- **Fit in one context window.** If you need to say "see also", split it.
- **Name its agent target.** Route by domain: terraform keywords ->
  `terraform-architect`, k8s/helm -> `gitops-operator`, code/test/build ->
  `developer`, gaia internals -> `gaia-system`.
- **Carry its own context slice.** The agent receives the task description,
  not the brief. Inline relevant constraints, file paths, and tech stack.
- **Cite the brief AC-ids it satisfies.** Every task lists
  `satisfies: [AC-1, AC-3]`. Unreferenced tasks get removed; uncovered ACs
  get new tasks.
- **Have a task-level AC with a command.** Binary pass/fail, internal to
  the task (build green, test passes, file exists).
- **Inherit the evidence slot from the brief AC.** The task AC is the
  technical proof (e.g. `pytest tests/auth/ -q` exits 0); the brief AC
  (e.g. login URL flow) is verified separately by the orchestrator
  post-dispatch.

Two AC levels, one per layer:

- **Brief AC (product):** what the user observes. Verified once,
  post-execution.
- **Task AC (technical):** what the agent must produce. Verified per task.

A feature is COMPLETE only when every task AC passes AND every brief AC's
evidence has been executed and persisted.

Task sizing: aim for 2-5 minutes of agent work. A task that takes 15
minutes is three tasks that should have been split.

### Step 3: Persist the plan content into the brief

`gaia plan save` / `gaia plan new` do **not** exist yet. They are scoped
to the `cli-completion` brief and will land with their own lifecycle on
the future `plans` table (`plans.status` in `{draft, active, closed}`).
Until then, persist the plan in the brief itself:

```bash
gaia brief edit <name> --workspace=<ws>
```

This opens the brief body in `$EDITOR`. Replace or extend the
`## Approach` section with the plan structure below (Approach paragraph +
Tasks list + Execution Order). Save and quit; the CLI writes the updated
row back to the DB.

Confirm with `gaia brief show <name>` that the plan content is now
present in the body.

**Do not write a `plan.md` file.** Do not create
`<workspace>/.claude/project-context/briefs/open_<feature>/plan.md` or any
variant of it. That path is legacy and is being removed.

## Plan Structure

The structure below is what you write into the brief's `approach` section
(or a successor section, e.g. `## Plan`) during `gaia brief edit`. The
shape mirrors the legacy `plan.md` shape so the orchestrator's task
dispatch logic continues to read it; what changed is the storage backend,
not the structure.

```markdown
## Plan

### Approach
{Technical strategy -- 3-5 sentences}

### Tasks

#### T1: {Task title}
- agent: {agent-type}
- status: pending
- satisfies: [AC-1, AC-2]   # brief AC-ids this task contributes to
- AC: `{verify command}`    # task-level technical proof, binary pass/fail
- blocked-by: none

**Context:** {Inline context slice}
**Change:** {Exact files + what changes}

### Execution Order
{Dependency graph}
```

Fill in:

- Approach (technical strategy, 3-5 sentences)
- Tasks with agent, status, AC, blocked-by, context, and change description
- Execution order (dependency graph)

When `gaia plan save` ships, the same structure will live in a dedicated
`plans` row with frontmatter for `status`, `brief_id`, and `created`.
This skill will be updated to use the new CLI then.

### Step 4: Task List Checkpoint

Before the orchestrator dispatches any tasks, present the complete task
list and wait for confirmation. The checkpoint must show:

- Task number, title, and target agent
- Dependencies (blocked-by relationships)
- Execution order

Ask: "Here are the tasks I plan to execute. Confirm to proceed, or suggest
changes." Do not let the orchestrator dispatch until the user confirms.

## Agent Routing Reference

Use this table to assign agent types to tasks. The orchestrator uses these
assignments when dispatching.

| Domain Signal | Agent |
|---------------|-------|
| Terraform, IaC, cloud resources | `terraform-architect` |
| Kubernetes, Helm, Flux, manifests | `gitops-operator` |
| Live cluster, pods, logs, diagnostics | `cloud-troubleshooter` |
| App code, tests, CI/CD, Docker | `developer` |
| Gaia hooks, skills, agents, routing | `gaia-system` |
| Workspace, memory, email, automation | `gaia-operator` |

## Plan status (informational)

When `gaia plan save` ships (brief `cli-completion`), plans will carry a
`status` column with the following lifecycle:

```
draft -> active -> closed
```

This aligns with the `gaia-state-machines` brief currently being
implemented. The transition will be driven by the CLI (e.g.
`gaia plan set-status <id> active`); status is never encoded in a
directory name. Today, the plan content lives in the brief's body and the
brief's status column governs the workflow -- there is no separate plan
status to track yet.
