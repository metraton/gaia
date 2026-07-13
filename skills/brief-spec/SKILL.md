---
name: brief-spec
description: Use when the user wants to create a brief or spec for a feature before planning
metadata:
  user-invocable: false
  type: technique
---

# Brief Spec

Conversational brief creation. The orchestrator loads this inline to
co-create a brief with the user before dispatching to gaia-planner. The
brief you write here is the contract you will audit the plan against: **you
own the resulting plan -- its tasks and its acceptance criteria** -- and the
planner produces it *for you to check*, not to approve on your behalf.

## DB is the source of truth (read this first)

Briefs live in the Gaia substrate database (`~/.gaia/gaia.db`). They are
created and mutated through the `gaia brief` CLI -- never by writing files
on disk. The DB row IS the brief: there is no `brief.md`, no
`<status>_<slug>/` directory, no frontmatter on disk. When in doubt, there
is no file to write -- there is a CLI command to run.

If you find code, docs, or skills that still describe a filesystem layout
under `.claude/project-context/briefs/`, that is legacy: flag it in
`cross_layer_impacts` -- do not edit it as a side effect of a brief task.

## Cuando llegas aquí

El orquestador cargó esta skill porque la conversación entró en Cerrar:
el usuario y él han acordado varias cosas y es momento de materializarlas.
No estás aquí porque la petición superó un umbral de tamaño. Estás aquí
porque hay acuerdos que capturar.

Tu trabajo:
1. Resumir los acuerdos que ya emergieron en la conversación previa --
   no re-descubrirlos desde cero.
2. Preguntar sólo lo que falte para convertir los acuerdos en AC
   reproducibles (evidence types, surface type).
3. Materializar el brief en la DB con `gaia brief new --headless` y
   presentarlo al usuario para validar.

## Process

1. **Ask questions** -- Target gaps, not completeness:
   - **Surface type** (always, before AC): Is this a UI a human uses, an API,
     or a background job? Determines valid evidence types for the ACs.
   - What problem does this solve?
   - What constraints matter? (cloud, performance, security, timeline)
   - How will you verify each AC yourself? (reproduce steps, not just "it works")
   - What artifact do you want to review after execution?
     (log file, screenshot, JSON snapshot, HTTP response, diff)
   - If this failed silently, what symptom would you look for?
   - What is explicitly NOT in scope?

   One question per round via AskUserQuestion. Stop when each AC has
   a declared evidence type and every question above has an answer or
   an explicit "N/A".

2. **Create the brief in the DB (headless)** -- Run:

   ```bash
   gaia brief new --headless \
     --title="<human title>" \
     --status=draft \
     --surface-type=<ui|api|job|cli> \
     --objective="<1-3 sentences>" \
     --context="<project constraints>" \
     --approach="<high-level strategy, 3-5 sentences>" \
     --out-of-scope="<explicit non-goals>"
   ```

   The slug is derived from `--title` (kebab-case). The CLI writes a row to
   the `briefs` table and prints the slug back. **Do not write any file in
   `.claude/project-context/briefs/`.** No directory, no `brief.md`, no
   frontmatter on disk. The DB row IS the brief.

   `--status=draft` is the canonical entry point. Move it to `open` only when
   the user is ready to plan against it.

3. **Add Acceptance Criteria** -- ACs are rows in the `acceptance_criteria`
   table, added one at a time with `gaia brief ac add`:

   ```bash
   gaia brief ac add <slug> \
     --id=AC-1 \
     --description="<user observation>" \
     --evidence-type=<command|url|playwright|artifact|metric> \
     --evidence-shape='<free-form string or JSON>' \
     --artifact=evidence/AC-1.txt
   ```

   Remove one with `gaia brief ac remove <slug> --id=AC-1`. The shapes per
   evidence type are under "Evidence Types" below; the `## Acceptance
   Criteria` section that `gaia brief show` renders is the human summary of
   these rows.

4. **Confirm with the user** -- `gaia brief show <slug>` prints the full row.
   Read it back and ask: "Does this capture what you want?"
   When confirmed, suggest dispatching to gaia-planner.

## How to update a brief

For a single field, use the headless patch -- scriptable, no editor:

```bash
gaia brief edit <name> --headless \
  --field=<objective|context|approach|out_of_scope|description|title|surface_type> \
  --content="..."
```

Use `gaia brief edit <name>` (no `--headless`) to open the full body in
`$EDITOR` for interactive edits. Prefer the headless form in an agent
turn -- a subagent cannot drive an interactive `$EDITOR`.

## How to change status

Use `gaia brief set-status <name> <new-status>`. The CLI validates the
state machine and rejects illegal transitions:

```
draft -> open -> in-progress -> closed -> {archived, open}
```

Examples:

```bash
gaia brief set-status my-feature open          # ready to plan against
gaia brief set-status my-feature in-progress   # work has begun
gaia brief set-status my-feature closed        # AC verified
gaia brief set-status my-feature archived      # closed -> archived
gaia brief set-status my-feature open          # closed -> reopened
```

There is no "rename the directory" step. Status is a column.

## How to delete a brief

Use `gaia brief delete <name> --yes`. Hard delete with FK cascade across
acceptance_criteria, milestones, dependencies, plans, and tasks tied to the
brief. There is no undo today; soft-delete is on a separate future brief.

Prefer `gaia brief set-status <name> archived` over delete for anything you
might want to read later.

## How to read briefs

| Need | Command |
|------|---------|
| List | `gaia brief list [--status=...] [--workspace=<ws>] [--format=table\|json\|count]` |
| Show one | `gaia brief show <name> [--workspace=<ws>] [--json]` |
| FTS5 search | `gaia brief search <query>` |

`--workspace` defaults to the current workspace. Pass it explicitly when
reading from outside the workspace tree (e.g. cron, batch jobs).

## Brief Body Structure

The brief body (rendered by `gaia brief show`) follows this shape. The
frontmatter block is the executable source of truth (orchestrator parses
it with `yaml.safe_load`). The body's `## Acceptance Criteria` section
mirrors it as a human summary.

```markdown
---
status: draft
surface_type: ui | api | job | cli
acceptance_criteria:
  - id: AC-1
    description: "Login button visible on /login"
    evidence:
      type: url
      shape:
        method: GET
        url: http://localhost:3000/login
        expect:
          status: 200
          body_contains: "Sign in"
    artifact: evidence/AC-1.json
  - id: AC-2
    description: "pytest auth suite green"
    evidence:
      type: command
      shape:
        run: "pytest tests/auth/ -q"
        expect: "exit 0"
    artifact: evidence/AC-2.txt
---

# [Feature Name]

## Objective
[1-3 sentences: what problem, why now, who benefits]

## Context
[Project constraints relevant to this feature]

## Approach
[High-level strategy, not implementation details. 3-5 sentences max]

## Acceptance Criteria
Human-readable summary. Source of truth lives in frontmatter.
- AC-1: Login button visible on /login (evidence: url)
- AC-2: pytest auth suite green (evidence: command)

## Milestones (M/L features only)
- M1: [name] -- [what is shippable after this]
- M2: [name] -- [what is shippable after this]

## Out of Scope
[Explicit boundaries -- what this feature does NOT include]
```

## Acceptance Criteria Rules

- Every AC has a description (user observation) and an evidence block.
- Evidence must be reproducible by the user -- not only by the agent.
- Every AC declares an `artifact` path; the orchestrator persists the
  verification output there so the user can read it after completion.
- Vague ACs get pushed back: "Fast means what? Under 200ms p95?"
- Surface type restricts valid evidence types (see table).

### Evidence Types

The shapes below are frontmatter fragments under `acceptance_criteria:`.
The body's `## Acceptance Criteria` section mirrors them for human reading;
the frontmatter is the executable source of truth.

| type | shape | valid surface |
|------|-------|---------------|
| `command` | `run: "bash command"; expect: exit_code \| substring` | any |
| `url` | `method: GET\|POST; url; expect: {status, body_contains}` | ui, api |
| `playwright` | `url; steps: [...]; assert: "selector visible" \| screenshot` | ui |
| `artifact` | `path; kind: json\|log\|screenshot; assert: schema \| contains` | any |
| `metric` | `query; threshold: "p95 < 200ms"` | api, job |

Shape examples (frontmatter fragments):

```yaml
# command
evidence:
  type: command
  shape:
    run: "pytest tests/auth/ -q"
    expect: "exit 0"

# url
evidence:
  type: url
  shape:
    method: GET
    url: http://localhost:3000/health
    expect:
      status: 200
      body_contains: '"status":"ok"'

# playwright
evidence:
  type: playwright
  shape:
    url: http://localhost:3000/login
    steps:
      - fill: "#email with user@test.com"
      - click: "button[type=submit]"
    assert: "selector [data-testid=dashboard] visible"

# artifact
evidence:
  type: artifact
  shape:
    path: dist/build-report.json
    kind: json
    assert: ".summary.errors == 0"

# metric
evidence:
  type: metric
  shape:
    query: "curl -s http://localhost:3000/metrics | grep http_p95"
    threshold: "< 200"
```

## Why the DB, not a directory tree

The old `.claude/project-context/briefs/<status>_<slug>/` layout is gone
because a directory tree cannot be the source of truth for a brief:

- Status lived in the directory name -- renaming a directory was the
  status transition. That made transitions unverifiable, racy across
  agents, and impossible to query with anything other than `find`.
- Two writers (filesystem + DB) drift apart silently; only one can be
  authoritative.
- Cascade deletes across ACs, milestones, plans, and tasks require FK
  semantics, which a directory tree cannot provide.

## After Brief -- you own the plan the planner returns

`gaia brief show <slug>` prints the full brief. Present it. Ask:
"Does this capture what you want?" When confirmed, dispatch to
gaia-planner to create a plan.

The brief settles *whether* the work is worth doing -- that was agreed
here, with the user. The planner does not re-litigate that. What the
planner owes you back is everything you need to **audit** the plan it
produces, not just the task list:

- the **feasibility findings** it corroborated against the codebase
  (what already exists, what the brief assumed that does not),
- the **assumptions** it had to make and the **risks** it sees,
- the **rationale for task ordering** and parallelization.

Require those in the dispatch -- a plan you cannot audit is one you
cannot own. When you review it, escalate to the user only what is
**genuinely new or blocking** (a feasibility gap, a fork the planner
could not resolve). Never re-ask what the brief already settled.

## Anti-Patterns

- **Writing `brief.md` to disk** -- the DB is the source of truth; any file
  on disk is either build output or stale legacy that will be deleted.
- **Renaming directories to change status** -- there are no directories;
  status is a column. Use `gaia brief set-status`.
- **Skipping `--status=draft` on creation** -- creating directly in `open`
  bypasses the review window where the user confirms ACs.
- **Hard-deleting a brief that has plan history** -- prefer
  `set-status archived`. Delete is for genuinely abandoned drafts.
- **Accepting a plan you cannot audit** -- dispatching the planner without
  requiring its feasibility findings, assumptions, risks, and ordering
  rationale leaves you owning a plan you cannot check. Require the audit
  inputs in the dispatch.
- **Re-asking the user what the brief settled** -- the brief is the agreed
  contract. Escalate only genuinely new or blocking findings surfaced by
  the plan; questions the brief already answered are noise.
