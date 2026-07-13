# Gaia Planner -- Reference

## Scope

Briefs and plans live in the Gaia substrate database (`~/.gaia/gaia.db`) as
separate rows in the `briefs` and `plans` tables. This reference is the
decomposition manual: how to survey before planning, how to size a task by
outcome and verification, and what shape the plan content takes. The plan is
persisted through `gaia plan save`. There is no `plan.md` on disk and no
`open_<feature>/` directory -- status is the `plans.status` column. See `SKILL.md`
for the altitude principle and the CLI flow.

## Phase 0: Survey before decomposing

Run both surveys against the live codebase before writing a single task. They
are what keep the plan from re-building what exists or specifying what cannot
be built.

### Overlap detection

For each objective in the brief, locate what already exists. Grep the codebase
for the area the objective touches; read the component if it is there. Plan only
the **delta**: the change from current state to the brief's intended state. A
component that ships today needs no task to create it -- at most a task to
extend it. Record overlaps you found so the checkpoint can show the user what
was already done and therefore not planned.

### Technical feasibility

For each intended outcome, corroborate that the implementation can support it.
Does the extension point exist? Does the CLI expose the flag the AC assumes?
Does the table have the column the evidence needs? An outcome that depends on
something not in the codebase is not a task -- it is either a prerequisite task
(build the extension point first) or a question for the user when the gap
reflects a brief-vs-implementation divergence (see "Blocking ambiguity").

## Phase 1: Create Plan

### Step 1: Read the brief

```bash
gaia brief show <name> --workspace=<ws> --json
```

From the JSON, extract objectives, acceptance criteria (`id`, `description`,
`evidence{type, shape}`, `artifact`), constraints (the body's `## Context`
section), and out-of-scope boundaries.

Every task you write must cite which brief AC-id(s) it satisfies. A task citing
no AC satisfies nothing observable against the product goal; split or delete it.

If `gaia brief show <name>` errors with "not found" -> BLOCKED. Tell the
orchestrator to create the brief first via the `brief-spec` skill. Do not fall
back to the filesystem -- the DB is authoritative.

### Step 2: Decompose into tasks

Each task is a unit of change defined by **outcome + verification**, sized to
task altitude (see `SKILL.md`: not verbose, not micro-impossible). Each task MUST:

- **Be defined by outcome, not implementation nomenclature.** Reference the
  area it touches loosely; do not pin exact symbols or paths. The executing
  agent resolves the specific against the live codebase, so an execution
  discovery (an approval gate, a moved symbol) does not shatter the task.
- **Name its agent target.** Route by domain (see the routing table below).
- **Carry its own context slice.** The agent receives the task, not the brief.
  Inline relevant constraints, the loosely-referenced area, and tech stack.
- **Cite the brief AC-ids it satisfies** (`satisfies: [AC-1, AC-3]`).
  Unreferenced tasks get removed; uncovered ACs get new tasks.
- **State its blast radius.** Name what the change touches beyond its own
  outcome -- shared modules, routing/manifest entries, schema, sibling tasks'
  files. This is what the orchestrator sequences around.
- **Declare parallelizability** (`parallel: yes|no`). Yes when the task has no
  unfinished dependency and no blast-radius overlap with a concurrent task.
- **Have a verification.** A task-level AC the orchestrator can run
  post-dispatch: binary pass/fail (build green, test passes, command exits 0,
  artifact present).

Two AC levels, one per layer:

- **Brief AC (product):** what the user observes. Verified once, post-execution.
- **Task AC (technical):** what the agent must produce. Verified per task.

A feature is COMPLETE only when every task AC passes AND every brief AC's
evidence has been executed and persisted.

### Step 3: Blocking ambiguity -> questionnaire

When the brief and the implementation diverge in a way that determines the plan
shape and you cannot tell which the user intends, do not assume. Emit
`NEEDS_INPUT` carrying a **simple-selection questionnaire**: the decision as one
question with a short list of concrete options. Shape:

```
Decision needed: <one-line framing of the divergence>
Options:
  A) <concrete option, with what it implies for the plan>
  B) <concrete option, with what it implies for the plan>
Default if unspecified: <the safest option, or "none -- blocking">
```

The planner emits this; the orchestrator presents it to the user and returns
the choice. Reserve it for ambiguity that blocks the plan -- routine sizing and
routing are the planner's own calls.

### Step 4: Persist the plan

```bash
gaia plan save --brief=<name> --content="..." --workspace=<ws>
```

`gaia plan save` upserts the `plans` row: first call inserts (status `draft`),
later calls update `status` and `content` only -- it does not delete or reorder
child tasks, so it is safe to call repeatedly. Pass `--status=active` to set a
non-default status on save. If the content exceeds inline limits, source it from
a file: `--content="$(cat /tmp/plan.md)"`.

Confirm with `gaia plan show <name>` that the content is stored.

Do **not** persist plan content with `gaia brief edit`: it writes the `briefs`
table (not `plans`), opens `$EDITOR` interactively (a subagent cannot drive it),
and the content never surfaces in `gaia plan show`.

## Plan Structure

This is the markdown you pass to `gaia plan save --content`. It mirrors the
shape the orchestrator's dispatch logic reads.

```markdown
## Plan

### Approach
{Technical strategy -- 3-5 sentences. Areas referenced loosely, not pinned.}

### Feasibility Findings
{The audit surface. For each objective/AC: what the desired end-state requires
vs what the system provides today, and how the plan resolves the gap -- a
prerequisite task, an existing capability, or (if unresolved) a blocking
question raised separately. If closing a gap would cost work comparable to or
larger than the brief itself, say so here: that is a finding, not a silent
prerequisite chain. This is what the orchestrator reads to judge whether the
plan is technically coherent.}

### Assumptions
{Every judgment the brief did not settle and you did not ask about -- the
resolved side of each non-blocking ambiguity. A wrong assumption surfaced here
is cheap to correct; a hidden one is not.}

### Risks
{What could make a task fail or a sequence break -- shared blast radius, an
untested integration point, an external dependency. Execution risk, not
worth-of-the-brief risk.}

### Tasks

#### T1: {Task title -- the outcome}
- agent: {agent-type}
- status: pending
- satisfies: [AC-1, AC-2]   # brief AC-ids this task contributes to
- parallel: yes|no          # safe to dispatch concurrently?
- blast-radius: {what this touches beyond its own outcome}
- AC: `{verify command or observable check}`   # binary pass/fail
- blocked-by: none

**Context:** {Inline context slice -- constraints + the area touched, loosely referenced}
**Outcome:** {What is true when done -- not the exact files/symbols}

### Execution Order
{Dependency graph; group the parallel:yes tasks that share no blast radius}

### Ordering Rationale
{Why this order -- which dependency or blast-radius overlap forces each edge.
The orchestrator sequences dispatch from this; state the reason, not just the graph.}
```

Fill in: Approach (technical strategy), Feasibility Findings, Assumptions, and
Risks (the audit surface the orchestrator reads), Tasks (each with agent,
status, satisfies, parallel, blast-radius, AC, blocked-by, context, outcome),
Execution Order (dependency graph that surfaces the parallelizable groups), and
Ordering Rationale (why that order).

### Step 5: Task List Checkpoint

Before the orchestrator dispatches any tasks, present the complete task list and
wait for confirmation. The checkpoint must show:

- Task number, title, and target agent
- Dependencies (blocked-by) and parallelizable label
- Execution order
- Overlaps found during the survey (what was already built and therefore not planned)
- Feasibility findings, assumptions, and risks (the audit surface -- so the
  audit reaches the user, not only the orchestrator)

Ask: "Here are the tasks I plan to execute. Confirm to proceed, or suggest
changes." Do not let the orchestrator dispatch until the user confirms.

## Agent Routing Reference

Assign agent types by domain signal. The orchestrator uses these when dispatching.

| Domain Signal | Agent |
|---------------|-------|
| Terraform, IaC, cloud resources | `platform-architect` |
| Kubernetes, Helm, Flux, manifests | `gitops-operator` |
| Live cluster, pods, logs, diagnostics | `cloud-troubleshooter` |
| App code, tests, CI/CD, Docker | `developer` |
| Gaia hooks, skills, agents, routing | `gaia-system` |
| Workspace, memory, email, automation | `gaia-operator` |

## Plan status

Plans carry a `status` column (`plans.status`, CHECK-constrained) with the
lifecycle:

```
draft -> active -> closed
```

`gaia plan save` inserts at `draft`. Transitions run through
`gaia plan set-status <name> <status>`; status is never encoded in a directory
name. Closing a plan with unsatisfied ACs surfaces an advisory warning.
