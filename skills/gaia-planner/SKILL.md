---
name: gaia-planner
description: Use when planning features or decomposing work into tasks from a brief
metadata:
  user-invocable: false
  type: technique
---

# Gaia Planner

Plan creation from briefs. The planner reads a brief from the substrate DB,
decomposes it into tasks, and persists the plan back into the brief. The
orchestrator owns task dispatch and execution.

## DB is the source of truth (read this first)

Briefs and plans live in the Gaia substrate database (`~/.gaia/gaia.db`).
The planner reads briefs through the `gaia brief` CLI and persists plan
content back through the same CLI. **Do not** read `brief.md` from disk.
**Do not** write `plan.md` to disk. **Do not** create or rename
`open_<feature>/` directories.

The filesystem layout `<workspace>/.claude/project-context/briefs/<status>_<slug>/`
with `brief.md` and `plan.md` inside it is **legacy** and is being removed in
the `legacy-cleanup` brief. If a previous version of this skill, a stale
reference, or an older agent prompt instructs you to read or write files
under that path, ignore it. The migration to DB-canonical is in progress
(session 2026-05-06, brief `gaia-state-machines`).

When in doubt: there is no file to read or write -- there is a CLI command
to run.

## When to Activate

- A brief exists in the DB and needs to become an execution plan.
- An existing plan needs revision or restructuring.

## Process

### Step 1: Read the brief from the DB

```bash
gaia brief show <name> --workspace=<ws> --json
```

`--workspace` defaults to the current workspace; pass it explicitly when
the orchestrator gives you a workspace context. The JSON output exposes
the body and frontmatter (objectives, ACs with id/description/evidence/artifact,
constraints, out-of-scope) -- everything you need to plan.

If the brief does not exist, return BLOCKED and tell the orchestrator to
create one first via `brief-spec`. Do not search the filesystem -- the DB
is authoritative.

### Step 2: Decompose into tasks

For decomposition rules (task sizing, AC citation, agent routing, context
slices), see `reference.md`. The contract: each task fits in one context
window, names its agent target, carries its own context slice, cites the
brief AC-ids it satisfies, and has a task-level AC with a verify command.

### Step 3: Persist the plan -- interim flow

`gaia plan save` does not exist yet (it is on the `cli-completion` brief).
Until then, the plan content is persisted in the brief itself, in the
`approach` field:

```bash
gaia brief edit <name> --workspace=<ws>
```

This opens `$EDITOR` against the brief body. Replace the existing
`## Approach` section with the planning content (Approach + Tasks +
Execution Order, structured per `reference.md`). Save and quit; the CLI
writes the row back to the DB.

After the edit, run `gaia brief show <name>` to confirm the plan content
is in the body.

**Future flow** (when `gaia plan save` ships under the `cli-completion`
brief): plans will move to a dedicated `plans` table with `plans.status`
in `{draft, active, closed}`. The CLI will own the lifecycle. This skill
will be updated then. Treat the future flow as informational -- today,
use the interim flow above.

### Step 4: Task list checkpoint

Before the orchestrator dispatches anything, present the task list
(numbers, titles, target agents, dependencies, execution order) and wait
for user confirmation. The orchestrator drives this round-trip; you
return the plan content as your output.

## Anti-Patterns

- **Writing `plan.md` to disk** -- the DB is the source of truth. Any
  `plan.md` you create is either build output or stale legacy that
  `legacy-cleanup` will delete. Edits there are silently ignored by the
  runtime.
- **Creating an `open_<feature>/` directory** -- there are no directories;
  status is a column. Status transitions happen via `gaia brief set-status`,
  not by renaming directories.
- **Reading `brief.md` from the filesystem** -- the DB row is authoritative.
  A stale `brief.md` on disk can drift from the DB row silently; reading
  the wrong source makes the plan wrong from line one.
- **Renaming directories for status sync** -- the legacy `gaia plans rename`
  command exists to sync directory prefixes to frontmatter status; it
  belongs to the legacy cleanup path, not to the planning flow.
- **Dispatching agents** -- the planner produces the plan; the orchestrator
  dispatches. If you have `Agent` in your tools, something is wrong.
- **Fat tasks** -- a task needing more than one context window forces the
  agent to lose track. Split it.
- **Thin tasks** -- a task without its own context slice forces the agent
  to read the full brief. Inline the slice.
- **Vague ACs** -- every task needs a verify command the orchestrator can
  run post-dispatch. No verify command = no way to confirm completion.

## Filesystem behavior (DEPRECATED)

The directory layout `<workspace>/.claude/project-context/briefs/open_<slug>/`
with a `plan.md` inside it is **legacy**. It will be removed in the
`legacy-cleanup` brief. Reasons it is being retired:

- Plan status lived in the directory name -- renaming was the transition.
  That made transitions unverifiable, racy across agents, and impossible
  to query with anything other than `find`.
- Two writers (filesystem + DB after the substrate refactor) drift apart
  silently; only one can be the source of truth.
- Cascade semantics across briefs, plans, and tasks require FK
  relationships, which a directory tree cannot provide.

If you find code, docs, or other skills that still describe the directory
convention, flag them in `cross_layer_impacts` -- do not edit them as a
side effect of a planning task.
