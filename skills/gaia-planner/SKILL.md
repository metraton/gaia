---
name: gaia-planner
description: Use when planning features or decomposing work into tasks from a brief
---

# Gaia Planner

Plan creation from briefs. The planner reads a brief from the substrate DB,
decomposes it into tasks defined by outcome and verification, and persists the
plan back through the `gaia plan` CLI as markdown AND as one task row per task
(`gaia task add`). The orchestrator owns task dispatch and execution.

## The brief is authoritative intent (read this first)

The brief is the settled output of investigation and conversation between the
user and the orchestrator. Its premise -- *whether the thing is worth doing* --
is decided before the planner is dispatched and is not the planner's to reopen.
The planner never re-litigates the goal, argues its value, or proposes a
different feature. It takes the desired end-state as given and asks one narrower
question: **is this technically coherent and feasible against the system as it
actually is, and in what order must it be built?**

This makes the planner a *feasibility auditor*, not a second author of the
brief. Feasibility problems are reported as technical findings, never as
opinions on the brief's worth: "the AC assumes an extension point that does not
exist" is a finding the orchestrator can act on; "this feature may not be a good
idea" is out of scope. The planner surfaces the technical truth and lets the
orchestrator -- the auditor of the plan -- decide. Because the orchestrator
audits the plan, the planner returns everything that audit needs: the
feasibility findings, the assumptions it made where the brief was silent, the
execution risks, and the rationale for the task ordering (see `reference.md`,
Plan Structure) -- not just the task list.

## The altitude principle

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
  actual implementation. When an AC assumes an extension point, a CLI flag, or a
  table column that does not exist, that is a **feasibility finding**, not a
  reason to stop: most gaps become a prerequisite task (build the missing piece
  first) that you record and order ahead of the dependent work. Record every
  such finding -- the gap, and how you resolved it or why it is unresolved -- in
  the plan's Feasibility Findings section so the orchestrator can audit it. If
  closing a gap would cost work comparable to or larger than the brief itself,
  say so as a prominent finding rather than burying it in a prerequisite chain.
  Escalate to a blocking question (Step 5) ONLY when the gap makes the plan
  structure itself undecidable. Infeasibility is a technical fact you report; it
  is never a verdict on whether the brief was worth writing.

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

### Step 4: Persist the plan (markdown, task rows, THEN their gates)

Persistence has three halves. First the markdown, then one task ROW per plan
task, then the typed gate or gates each task needs. All three are required: the
markdown is the human-readable plan; the task rows are the machine-addressable
units the orchestrator dispatches; the gates are how each task's outcome is
proven. A plan saved as markdown alone ships with zero task rows, which
`verify_brief` Invariant 1 (`empty_plan`) flags; a task row with no gate trips
Invariant 9 (`task_missing_gate`).

**Half 1 -- save the markdown:**

```bash
gaia plan save --brief=<name> --content="..." --workspace=<ws>
```

This upserts the plan row in the `plans` table: first call inserts (status
`draft`), later calls update `status` and `content` without touching child
tasks. It is the only supported writer. If the content is too large to pass
inline, source it from a file: `--content="$(cat /tmp/plan.md)"`.

**Half 2 -- materialize one task row per plan task:**

After the plan is saved, loop over the tasks you decomposed in Step 3 and
attach each as a row, once per task:

```bash
gaia task add <brief> --order=N --goal="... AC-<n> ..." --workspace=<ws>
```

Rules, in order:

- **HARD SEQUENCING: `gaia plan save` MUST precede every `gaia task add`.**
  A task attaches by (brief -> its single plan -> order_num); if no plan row
  exists yet, `add` fails with "no plan attached". Never add tasks before
  saving the plan.
- **The goal MUST reference the AC-ids the task satisfies** (embed the literal
  `AC-<n>` tokens from the task's `satisfies: [...]`, e.g.
  `--goal="Implement the list reader (AC-1)"`). `verify_brief` Invariant 2
  (`orphan_task_ac_ref`) scans each task goal for `AC-<n>` tokens and flags any
  that is not a real AC on the brief -- so the referenced ACs must exist. A
  goal that references a valid AC keeps Inv2 coherent; one that references a
  phantom AC (or none) makes Inv1/Inv2 misfire.
- **`order_num` is 1-based and unique within the plan.** Use the task's
  position from Step 3. A duplicate order_num within the same plan is rejected.
- **`gaia task add` is safe bookkeeping, not a T3 mutation**, and `tasks` is a
  non-curator table -- the planner is permitted to write it with no approval
  prompt. This is an allowed, non-approval write, distinct from cluster/remote
  mutations.
- **Re-planning a brief whose plan already has rows:** a repeated
  `gaia task add` at an existing order_num errors on the duplicate. When
  re-materializing, remove or reorder the stale rows first
  (`gaia task remove` / `gaia task reorder`), or add only the new order_nums.

**Half 3 -- author the typed gate or gates each task needs:**

After a task row exists, attach the gate or gates that prove its outcome, once
per gate:

```bash
gaia task gate add <brief> <order> --type=<T> --evidence-shape="..." --workspace=<ws>
```

- **Choose `--type` by the task's nature.** `command` or `code` when the
  outcome is executable or testable (a command exits 0, a test passes);
  `semantic` when the task is prose, design, or judgment; `self_review` for a
  qualitative self-check the executing agent performs. The four values are the
  only valid ones (`VALID_VERIFICATION_TYPES`).
- **Pair a non-empty `--evidence-shape` with EVERY gate.** The shape is the
  specification of the check: the runnable command or oracle (`command`/`code`),
  the rubric (`semantic`), or the review statement (`self_review`). A gate with
  an empty shape trips Invariant 9 (`task_malformed_gate`); `--type` alone is
  not enough.
- **A task MAY carry more than one gate, of mixed types.** `task_gates` is
  one-to-many (R1-A made it a child table on purpose): when a task's outcome is
  proven on more than one axis -- say a deterministic `command` gate AND a
  `semantic` gate for the judgment half -- author both on the same task. Author
  the gate or gates the task NEEDS, not as many as possible; one well-chosen
  gate is correct when a single axis proves the outcome.
- **Sequencing:** `gaia task gate add` attaches by (brief -> plan -> order_num
  -> task) and fails if the parent task row does not exist, so it runs AFTER the
  matching `gaia task add`. Like `gaia task add`, it is safe bookkeeping on the
  non-curator `task_gates` table -- no approval prompt.

Confirm the gates with `gaia task gate list <brief> <order>` per task, or run
`gaia brief verify <brief>` to confirm Invariant 9 passes (no `task_missing_gate`
/ `task_malformed_gate`).

Lifecycle is `draft -> active -> closed` via `gaia plan set-status <name> <status>`.

Confirm the markdown with `gaia plan show <name>` and the rows with
`gaia task list <name> --format=count` (the count should equal the number of
tasks you decomposed).

### Step 5: Resolve blocking ambiguity before persisting

When the brief and the implementation diverge in a way that changes the plan --
the brief asks for X, the codebase already does Y, and you cannot tell which the
user wants -- do not assume. Emit `NEEDS_INPUT` with a **simple-selection
questionnaire**: the decision framed as a short list of concrete options. The
planner does not pick; the orchestrator presents the options to the user and
returns the choice. Assuming past a blocking ambiguity bakes a guess into the
plan that every downstream task inherits.

Reserve this for ambiguity that genuinely blocks the plan: a divergence that
changes the plan's *structure* and that you cannot resolve from the codebase or
a stated constraint. Do not manufacture questions -- an absent blocker means you
proceed. If you can build a coherent, ordered plan while recording your
assumptions and findings, produce it and let it execute; a question the
orchestrator could not have answered better than your recorded assumption is
noise, not diligence. Routine sizing and routing calls are yours to make.

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
- **Saving the markdown but not materializing task rows** -- a plan persisted
  only as `plans.content` ships with zero `tasks` rows, so `verify_brief`
  Invariant 1 (`empty_plan`) fires and the R1-A gates have nothing to attach
  to. Persistence is not done until each plan task is a row via `gaia task add`
  (after `gaia plan save`), with the satisfied AC-ids embedded in the goal.
- **Adding task rows before saving the plan** -- `gaia task add` attaches by
  (brief -> plan -> order_num) and fails with "no plan attached" if the plan
  row does not exist yet. Always `gaia plan save` first, then loop the adds.
- **Pinning implementation nomenclature** -- a task that names exact symbols or
  paths breaks when execution discoveries move them, and takes its downstream
  tasks with it. Reference areas loosely; let the executing agent resolve the
  specific. This is the altitude principle, and it is the central one.
- **Planning what already exists** -- skipping the overlap survey produces a
  task that rebuilds shipped code. Diff the brief against the codebase first.
- **Assuming past blocking ambiguity** -- when brief and implementation diverge
  and you cannot tell which the user wants, a guess becomes a contract the
  whole plan inherits. Emit NEEDS_INPUT with options.
- **Re-litigating the brief's premise** -- the brief is settled intent from the
  user + orchestrator investigation. Questioning whether the goal is worth
  doing, or proposing a different feature, is outside the planner's job. Audit
  feasibility, not worth.
- **Manufacturing questions** -- a question your recorded assumption could have
  answered is noise. Ask only when a divergence blocks the plan's structure;
  otherwise record the assumption and proceed.
- **Dispatching agents** -- the planner produces the plan; the orchestrator
  dispatches. If you have `Agent` in your tools, something is wrong.
- **Fat or micro tasks** -- a task spanning many outcomes loses the agent; a
  "task" too small to verify on its own is a step. Size to one testable outcome.
- **Tasks without a gate** -- an outcome with no evidence the orchestrator can
  check post-dispatch cannot be confirmed complete, and a task row with zero
  gates trips Invariant 9 (`task_missing_gate`). Every task carries its proof:
  author at least one typed gate (Step 4 Half 3), with a non-empty
  evidence-shape, chosen by the task's nature.
