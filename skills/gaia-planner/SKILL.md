---
name: gaia-planner
description: Use when planning features or decomposing work into tasks from a brief
metadata:
  user-invocable: false
  type: technique
---

# Gaia Planner

Plan creation from briefs. The planner reads a brief from the substrate DB,
decomposes it into tasks defined by outcome and verification, and persists the
plan back through the `gaia plan` CLI. The orchestrator owns task dispatch and
execution.

## The altitude principle (read this first)

A plan defines each task by its **outcome plus how that outcome is verified** --
never by implementation nomenclature. Reference areas of the codebase loosely
("the brief CLI", "the approval module"); do not pin exact symbol names, file
paths, or function signatures inside a task.

This is deliberate. Execution surfaces discoveries the planner cannot see:
an approval gate fires and changes the command, byte-coding or a refactor moves
a symbol, a downstream task lands a file somewhere the plan did not predict. A
task that pins `hooks/modules/security/approval_grants.py:activate_db_pending_by_prefix`
breaks the moment that symbol moves -- and worse, every downstream task that
referenced the pinned name breaks with it. A task that says "the approval
grant activation path" survives the move, because the executing agent resolves
the specific against the live codebase.

The unit of planning is the **task with a testable outcome**, not the micro-step.
This diverges on purpose from "2-5 minute steps with exact content" patterns:
over-specifying the *how* at plan time transfers a guess into a contract the
downstream cannot keep. Plan the *what* and the *proof*; let execution own the
specifics.

## DB is the source of truth

Briefs and plans live in the Gaia substrate database (`~/.gaia/gaia.db`). The
planner reads briefs through `gaia brief show` and persists plan content through
`gaia plan save`. Briefs and plans are **separate rows in separate tables**
(`briefs`, `plans`); the `plans` row has `brief_id UNIQUE`, so there is exactly
one plan per brief. There is no `plan.md` on disk and no `open_<feature>/`
directory -- status is the `plans.status` column, not a directory name.

When in doubt: there is no file to read or write -- there is a CLI command to run.

## When to Activate

- A brief exists in the DB and needs to become an execution plan.
- An existing plan needs revision or restructuring.

## Process

### Step 1: Read the brief from the DB

```bash
gaia brief show <name> --workspace=<ws> --json
```

`--workspace` defaults to the current workspace; pass it explicitly when the
orchestrator gives you a workspace context. The JSON exposes objectives, ACs
(id/description/evidence/artifact), constraints, and out-of-scope.

If the brief does not exist, return BLOCKED and tell the orchestrator to create
one first via `brief-spec`. Do not search the filesystem -- the DB is authoritative.

### Step 2: Survey before you decompose

Two checks come before sizing tasks. Skipping them produces a plan that
re-builds what exists or specifies what cannot be built.

- **Overlap detection.** Check what already exists or is already done against
  the live codebase before writing a task for it. A task that re-creates a
  component that ships today is waste the orchestrator will dispatch in good
  faith. Plan only the delta between the brief and what is built.
- **Technical feasibility.** Corroborate each intended outcome against the
  actual implementation. A brief AC that assumes an extension point, a CLI flag,
  or a table column that does not exist is not plannable as written -- it is a
  question for the user (see Step 5), not a task.

### Step 3: Decompose into tasks

For sizing rules, AC citation, agent routing, and the plan structure, see
`reference.md`. The contract per task:

- **Defined by outcome + verification**, at task altitude (see the altitude
  principle): a single unit of change with a testable outcome and the evidence
  that proves it. Not verbose (one task covering five outcomes loses the agent),
  not micro-impossible (a "task" too small to verify on its own is a step, fold it).
- **Carries its own context slice.** The agent receives the task, not the brief.
  Inline the constraints and the loosely-referenced area it touches.
- **Cites the brief AC-ids it satisfies** (`satisfies: [AC-1, AC-3]`). A task
  citing nothing is unverifiable against the product goal -- split or delete it.
- **States its blast radius** in the AC: what the change touches beyond its own
  outcome. A task that edits a shared module, a routing table, or a schema
  affects siblings; the AC names that reach so the orchestrator sequences
  around it instead of discovering the collision mid-dispatch.
- **Carries a parallelizable label** (`parallel: yes|no`). A task with no
  unfinished dependency and no overlapping blast radius runs concurrently; one
  that does not, says so. This is what lets the orchestrator optimize dispatch
  instead of serializing defensively.

### Step 4: Persist the plan

```bash
gaia plan save --brief=<name> --content="..." --workspace=<ws>
```

This upserts the plan row in the `plans` table: first call inserts (status
`draft`), later calls update `status` and `content` without touching child
tasks. It is the only supported writer. If the content is too large to pass
inline, source it from a file: `--content="$(cat /tmp/plan.md)"`.

Lifecycle is `draft -> active -> closed` via `gaia plan set-status <name> <status>`.

Confirm with `gaia plan show <name>` that the content is stored.

### Step 5: Resolve blocking ambiguity before persisting

When the brief and the implementation diverge in a way that changes the plan --
the brief asks for X, the codebase already does Y, and you cannot tell which the
user wants -- do not assume. Emit `NEEDS_INPUT` with a **simple-selection
questionnaire**: the decision framed as a short list of concrete options. The
planner does not pick; the orchestrator presents the options to the user and
returns the choice. Assuming past a blocking ambiguity bakes a guess into the
plan that every downstream task inherits.

Reserve this for ambiguity that blocks the plan. Routine sizing or routing
calls are yours to make.

### Step 6: Task list checkpoint

Before the orchestrator dispatches anything, present the task list (numbers,
titles, target agents, dependencies, parallelizable labels, execution order)
and wait for user confirmation. The orchestrator drives this round-trip; you
return the plan content as your output.

## Anti-Patterns

- **Persisting via `gaia brief edit`** -- this writes the `briefs` table and
  opens `$EDITOR` interactively, which a subagent cannot drive. Content put
  there never appears in `gaia plan show` because plans and briefs are
  different rows in different tables. Persist with `gaia plan save`.
- **Pinning implementation nomenclature** -- a task that names exact symbols or
  paths breaks when execution discoveries move them, and takes its downstream
  tasks with it. Reference areas loosely; let the executing agent resolve the
  specific. This is the altitude principle, and it is the central one.
- **Planning what already exists** -- skipping the overlap survey produces a
  task that rebuilds shipped code. Diff the brief against the codebase first.
- **Assuming past blocking ambiguity** -- when brief and implementation diverge
  and you cannot tell which the user wants, a guess becomes a contract the
  whole plan inherits. Emit NEEDS_INPUT with options.
- **Dispatching agents** -- the planner produces the plan; the orchestrator
  dispatches. If you have `Agent` in your tools, something is wrong.
- **Fat or micro tasks** -- a task spanning many outcomes loses the agent; a
  "task" too small to verify on its own is a step. Size to one testable outcome.
- **Tasks without verification** -- an outcome with no evidence the orchestrator
  can check post-dispatch cannot be confirmed complete. Every task carries its proof.
