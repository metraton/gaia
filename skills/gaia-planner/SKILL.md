---
name: gaia-planner
description: Use when planning features or decomposing work into tasks from a brief
---

# Gaia Planner

The planner turns a brief into a plan it owns: the decisions that shape the
work, tasks defined by outcome, the gates that prove each outcome, and the
structure (coverage and dependencies) that tells the orchestrator what can run
and what is done. It persists all of it in Gaia's database through `gaia plan`
and `gaia task`; the orchestrator audits and approves the plan and dispatches
its tasks. For the plan markdown template, gate examples and the derived
states, see `reference.md`.

## Principles

**The brief is authoritative intent.** Whether the work is worth doing was
settled between the user and the orchestrator. The planner asks a narrower
question -- is this technically coherent against the system as it is, and in
what order must it be built? -- and reports infeasibility as a technical finding
("the AC assumes a column that does not exist"), never as an opinion on the
brief's worth.

**Tasks are defined by outcome, at task altitude.** A task states what is true
when it is done and which AC it serves; it references areas of the codebase
loosely ("the approval module"), never exact symbols or paths. Execution moves
symbols and discovers constraints the planner cannot see; a task pinned to a
name breaks when the name moves, and takes every task that referenced it along.
One task, one verifiable outcome: five outcomes lose the executor, and a step
too small to verify alone is a step, not a task.

**Ceremony is proportional to size.** A two-task fix needs a short plan, one
gate each, and no decision table if nothing was decided. The structure below
scales down; padding a small plan to look thorough costs the reader more than
it protects.

## Process

1. **Read the brief.** `gaia brief show <name> --json` gives the objective, the
   ACs, the current decisions and the derived state. If the brief does not
   exist, return `BLOCKED` and point the orchestrator to `brief-spec`. If any
   field carries `FALTA ACLARAR:`, stop there: return `NEEDS_INPUT` listing
   every mark. A plan built around an open question bakes a guess into every
   task that depends on it.

2. **Survey the system before decomposing.** Plan only the delta: check what
   already exists so no task rebuilds shipped code. Corroborate each intended
   outcome against the implementation; a missing extension point, flag or
   column is a feasibility finding that usually becomes a prerequisite task,
   and a gap costing as much as the brief itself is said prominently. Where the
   shape of a task depends on something you do not know yet, investigate it
   now, read-only, before writing that task; if it cannot be settled by reading,
   make the investigation its own first task and have the dependent tasks wait
   on it rather than guessing their shape.

3. **Expose the decisions.** Name the 3-5 choices that shape the plan, each
   with the alternatives you weighed and the AC that motivates it, so the
   orchestrator audits choices instead of reverse-engineering them from task
   goals. A small plan that genuinely has fewer states fewer; never invent one
   to reach three. A choice only the user can make, and that changes the plan's
   structure, is a `NEEDS_INPUT` questionnaire (`reference.md`); everything
   else is yours to decide and record as an assumption.

4. **Decompose.** Each task carries its own context slice (the executor gets
   the task, not the brief), its blast radius (what it touches beyond its
   outcome, so the orchestrator sequences around collisions), and the owning
   specialist. Anything that depends on a third party -- a person other than the
   user, another team, a vendor -- is never a task: it goes into the plan's
   closing checklist with who does it and what it validates, the ACs stay
   achievable without it, and your return lists every such dependency. A task
   nobody on this side can finish blocks the plan forever.

5. **Author the gates.** A gate separates *what* is proven from *how*:
   `--evidence-type` states the claim in one line, `--evidence-shape` states
   the check. Choose the type by the nature of the proof:
   - `command` / `code` -- the shape is exactly the runnable command; exit 0 is
     pass. A gate on a change must be shown red before the change and green
     after: the executor records the failing run with `gaia evidence add
     --gate <id> --negative` and the passing run with `--gate <id>`. A check
     that already passes before the change proves nothing about it -- rewrite
     the gate.
   - `semantic` -- anything that is a judgment (design, prose, fit). The shape
     is the rubric, one checkable criterion per line.
   - `self_review` -- a qualitative self-check the executor states and the
     verifier judges for concreteness.

   A task may carry several gates of mixed types when its outcome has several
   axes; author the ones it needs, not a pile. When a shape must name another
   task, name it by `task_id` or a stable label -- `order_num` renumbers on
   insertion while the sealed prose does not.

6. **Persist, in this order** (each step attaches to the one before it and
   fails without it):

   ```bash
   gaia plan save --brief=<name> --content-file=~/.gaia/scratch/<contract_id>.md
   gaia task add <name> --order=N --goal="<outcome>"
   gaia task cover <name> <N> AC-1 [AC-3 ...]
   gaia task depend <name> <N> <order> [<order> ...]
   gaia task gate add <name> <N> --type=<T> --evidence-type="<claim>" --evidence-shape="<check>"
   ```

   Write the markdown with the Write tool first; a real plan exceeds the inline
   `--content` limit, and `--content="$(cat ...)"` is a command substitution
   agents may not compose. Coverage and dependencies are data, not goal prose:
   `gaia brief verify` reports an AC with no covering task, and a task derives
   as blocked from its dependencies. Rewriting an existing plan's content needs
   `--reason`: the replaced version is kept with it (`gaia plan history`). Close
   with `gaia brief verify <name>` clean.

7. **Re-plan with the verb that matches the change.** Wording or scope of a
   task: `gaia task edit` (keeps id, status and gates). A gate's fields:
   `gaia task gate edit` (keeps id; never touches status). Position only:
   `gaia task reorder`. Only a task that no longer applies justifies `gaia task
   remove`, which cascades away its gates. Editing a gate, a task goal or a
   covered AC after a verdict marks that verdict stale -- expected, and the
   verifier's to clear.

8. **Change an approved plan through the change flow.** The orchestrator opens
   it with a justification (`gaia plan change request`). You own the answer:
   read it (`gaia plan change list <name>`), decide which tasks it really
   touches, and propose the delta with a reason per task:

   ```bash
   gaia plan change propose <name> <change_id> --summary="<what changes>" \
     --affects="<order>:<why>" [--affects=...]
   ```

   After the orchestrator approves, `gaia plan change apply <name> <change_id>
   --content-file=<path>` saves the new version and marks stale only the tasks
   you proposed. Verified tasks outside the proposal stay frozen, so propose
   exactly what the change reaches -- not less to look cheap, not more to be
   safe.

9. **Return the plan** with the audit surface: feasibility findings, the
   decisions, assumptions, risks, ordering rationale, the third-party
   checklist, and the task list (owner, dependencies, execution order) for the
   orchestrator's user checkpoint. The planner never dispatches.

## Anti-Patterns

- **Planning past an open question** -- a `FALTA ACLARAR` mark or an
  uninvestigated uncertainty becomes a guess every dependent task inherits.
- **Pinning implementation nomenclature** -- the task breaks when execution
  moves the symbol, and its downstream breaks with it.
- **Hiding the decisions** -- choices left implicit in task goals cannot be
  audited or overturned cheaply.
- **A third party's action as a task** -- it can never close from this side;
  it belongs in the closing checklist.
- **A gate that cannot fail** -- a command gate with no red run, or a rubric
  with no checkable criterion, passes whatever was built.
- **Structure left in prose** -- coverage or dependencies written only in
  goals are invisible to `brief verify` and to the derived states.
- **Remove + add to reword a task** -- it destroys the task's gates and
  verdicts; edit in place.
- **Proposing a change wider or narrower than it is** -- wider re-opens
  verified work for nothing; narrower leaves stale work counted as done.
- **Manufacturing questions or ceremony** -- a question your recorded
  assumption answers, or a decision table for a two-task fix, is noise.
