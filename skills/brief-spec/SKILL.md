---
name: brief-spec
description: Use when the user wants to create a brief or spec for a feature before planning
---

# Brief Spec

Brief Spec is how the orchestrator turns what it agreed with the user into a
brief: an objective, observable acceptance criteria, the decisions taken, and
the boundaries, ready for gaia-planner. The brief is the contract the plan is
audited against, so the orchestrator owns its content and owns checking the
plan that comes back.

## The database is the brief

A brief is a row in `briefs` plus its child rows (`acceptance_criteria`,
`brief_decisions`, `milestones`), written and read only through `gaia brief`.
What `gaia brief show` prints is a rendering of those rows: nothing on disk is
authoritative and there is no file to write. Code or docs that still describe
`.claude/project-context/briefs/` are legacy -- flag them in
`cross_layer_impacts` instead of editing them as a side effect.

**Execution follows authority.** The orchestrator co-creates the brief and may
use its trusted Gaia CLI lane for bounded reads and user-confirmed
`new`/headless `edit`/`set-status`/AC/decision writes. `gaia-operator` remains
the alternative for batching or operational separation. Destructive deletion is
outside the direct lane. The orchestrator owns the questions, confirmation,
and content whichever executor carries the command.

## Cuando llegas aquí

El orquestador cargó esta skill porque la conversación entró en Cerrar: el
usuario y él acordaron varias cosas y es momento de materializarlas. No estás
aquí porque la petición superó un umbral de tamaño, sino porque hay acuerdos
que capturar.

1. Resumir los acuerdos que ya emergieron -- no re-descubrirlos desde cero.
2. Preguntar sólo lo que falte para que cada AC sea observable, o dejarlo
   marcado como pendiente de aclarar.
3. Materializar el brief con `gaia brief new --headless` y presentarlo al
   usuario para validar.

## Process

1. **Ask only for the gaps**, one question per round via AskUserQuestion:
   surface type (ui, api, job, cli); the problem it solves; the constraints that
   matter; for each AC, what the user would see when it holds and what symptom
   would show it failing silently; what is explicitly out of scope. Stop when
   every AC is observable or explicitly marked unresolved (step 4).

2. **Create the brief:**

   ```bash
   gaia brief new --headless --title="<human title>" --status=draft \
     --surface-type=<ui|api|job|cli> --objective="<1-3 sentences>" \
     --context="<project constraints>" --approach="<high-level strategy>" \
     --out-of-scope="<explicit non-goals>"
   ```

   The slug is derived from the title. `draft` is the review window; move it
   to `open` only when the user is ready to plan against it.

3. **Add each acceptance criterion as an observation:**

   ```bash
   gaia brief ac add <slug> --id=AC-1 --description="<what the user observes>"
   ```

   An AC says what is true and visible when the work is done, precisely enough
   to disagree with ("p95 under 200ms on /search", not "fast"). It does not
   prescribe the proof: which test, command or rubric shows it is the planner's
   proposal, authored as gates. Fixing a mechanism here, before anyone has read
   the code, turns a guess into a requirement. Set `--evidence-type` /
   `--evidence-shape` only when the user asked to see the result a particular
   way ("a screenshot of the dashboard"). Evidence itself is recorded during
   execution with `gaia evidence add`, never as a path declared in advance.

4. **Mark what is not settled.** Where the user has not decided something the
   plan depends on, write `FALTA ACLARAR: <question>` into the field it affects
   (the AC description, the objective, the approach). The planner does not plan
   past such a mark: it returns `NEEDS_INPUT` with the list, and planning
   resumes once the answer replaces the mark (`gaia brief ac edit` or
   `gaia brief edit --headless`). `gaia brief verify` reports every field
   and AC still carrying the mark (`unresolved_clarification`, advisory like
   its other checks); it matches the literal text, so spell it exactly.

5. **Record decisions in their own field:**

   ```bash
   gaia brief decision add <slug> --text="<the decision>" --rationale="<why>" \
     [--supersedes=<id>]
   ```

   A decision that changes an earlier one names it with `--supersedes`, so
   `gaia brief show` lists the current answer apart from the one it replaced.
   Appending a new paragraph to `approach` instead leaves two answers to the
   same question with nothing saying which one holds.

6. **Confirm.** Run `gaia brief show <slug>`, read it back, ask "Does this
   capture what you want?", and when confirmed dispatch gaia-planner.

## Maintaining a brief

| Need | Command |
|------|---------|
| Patch one field | `gaia brief edit <name> --headless --field=<objective\|context\|approach\|out_of_scope\|description\|title\|surface_type> --content="..."` |
| Edit an AC in place | `gaia brief ac edit <name> --id=AC-1 --description="..."` |
| Change status | `gaia brief set-status <name> <status>` (`draft -> open -> in-progress -> closed -> {archived, open}`; illegal transitions are refused) |
| Read | `gaia brief list`, `gaia brief show <name> [--json]`, `gaia brief search <query>`, `gaia brief decision list <name>` |
| Delete | dispatch gaia-operator for `gaia brief delete <name> --yes` (cascades to ACs, plan, tasks; no undo) -- prefer `archived` |

The interactive `gaia brief edit <name>` opens `$EDITOR` and needs a human at a
terminal; dispatch only the headless form. Editing an AC's description marks
stale the gate verdicts of the tasks that cover it: they stop counting until a
verifier confirms them again.

## Evidence

The `evidence` table accepts exactly five types (`gaia evidence add --type`):
`text`, `file`, `command_output`, `url`, `screenshot`. Evidence is positive by
default; `--negative` records a refutation and never accepts an AC; `--gate
<id>` ties it to the gate that produced it. An AC is done only when every task
covering it is done and it has at least one positive evidence row
(`gaia/briefs/store.py::derive_brief_state`), which is why an AC with no
reachable observation cannot close.

## After the brief -- you own the plan

The brief settles *whether* the work is worth doing; the planner does not
re-open that. It owes you what you need to audit its plan: feasibility
findings, assumptions, risks, the 3-5 decisions that shape the plan with their
alternatives and the AC motivating each, the ordering rationale, each task's
gates, and the checklist of what depends on third parties. Require those in the
dispatch -- a plan you cannot audit is one you cannot own. Escalate to the user
only what is genuinely new or blocking; never re-ask what the brief settled.

**Judge that each gate proves its task's intent.** Well-formedness is checked
for you (`gaia brief verify`: a task without gates, an empty shape, an AC no
task covers). What no check can judge is fit: a `command` gate on a design
judgment, a `semantic` rubric on something a test could decide, a code gate
with no failing run before the change. Flag a mismatch back to the planner
rather than accept it.

**Before dispatching, run `gaia brief verify <slug>`.** It is the cheap
coverage check: an `uncovered_ac` means some AC has no task that could ever
make it done.

**Changing an approved plan goes through the planner.** Request it with the
justification (`gaia plan change request <slug> --reason="..."`); the planner
proposes which tasks the change touches and why; review the proposal
(`gaia plan change list <slug>`) and approve it (`gaia plan change approve`),
taking it to the user first when it changes *what* is delivered. Verified tasks
the change does not touch stay as they are. To stop dispatch without changing
the plan, `gaia plan pause <slug> --reason="..."`, then `gaia plan resume`.

## Anti-Patterns

- **Prescribing the proof in the AC** -- the brief is written before anyone
  reads the code; a mechanism fixed there binds the plan to a guess. State the
  observation and let the planner propose the gate.
- **Planning past a `FALTA ACLARAR` mark** -- the plan inherits the guess and
  every task built on it. Resolve it with the user first.
- **Burying a changed decision in `approach`** -- the old and new answers both
  survive with nothing saying which holds. Use `brief decision add
  --supersedes`.
- **Skipping `--status=draft`** -- creating directly in `open` bypasses the
  window where the user confirms the ACs.
- **Accepting a plan you cannot audit, or a gate that misses its task's
  intent** -- you own the result either way; require the audit inputs and
  send mismatches back.
- **Re-asking what the brief settled** -- escalate only what the plan surfaced
  as new or blocking.
