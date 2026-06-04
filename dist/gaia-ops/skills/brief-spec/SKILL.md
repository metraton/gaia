---
name: brief-spec
description: Use when the user wants to create a brief or spec for a feature before planning
metadata:
  user-invocable: false
  type: technique
---

# Brief Spec

Conversational brief creation. The orchestrator loads this inline to
co-create a brief with the user before dispatching to gaia-planner.

## DB is the source of truth (read this first)

Briefs live in the Gaia substrate database (`~/.gaia/gaia.db`). They are
created and mutated through the `gaia brief` CLI -- never by writing files
into `.claude/project-context/briefs/`. That filesystem layout is **legacy**
and is being removed in a follow-up cleanup.

If a previous version of this skill or a stale doc tells you to write
`brief.md` to a `<status>_<slug>/` directory, ignore it. The migration to
DB-canonical is in progress (session 2026-05-06, brief `gaia-state-machines`).
When in doubt: there is no file to write -- there is a CLI command to run.

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

3. **Add Acceptance Criteria** -- ACs live in the brief's
   `acceptance_criteria` field (currently a YAML/markdown block stored in the
   row body). Until `gaia brief add-ac` ships, capture them in the same
   round-trip:
   - If you have all ACs at creation time, include them in the
     `--approach` or a follow-up `gaia brief edit <slug>` where the editor
     opens the body for in-place editing.
   - The body's `## Acceptance Criteria` section uses the format under
     "Brief Body Structure" below. Frontmatter is the executable source of
     truth; the human summary mirrors it.

4. **Confirm with the user** -- `gaia brief show <slug>` prints the full row.
   Read it back and ask: "Does this capture what you want?"
   When confirmed, suggest dispatching to gaia-planner.

## How to update a brief

Use `gaia brief edit <name>` for the full body in `$EDITOR`. This is the
current path while finer-grained CLI commands are pending.

When `gaia brief set-field <name> --field=<context|approach|...>
--content="..."` ships (pending, scope of brief `cli-completion`), prefer it
for single-field updates -- no editor, scriptable, headless-friendly.

**Do not edit files in `.claude/project-context/briefs/`.** Any directory
you may see there is legacy and will be deleted; edits there are silently
ignored by the runtime.

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

## Filesystem behavior (DEPRECATED)

The directory layout `.claude/project-context/briefs/<status>_<slug>/`
with a `brief.md` inside it is **legacy**. It will be removed in the
`legacy-cleanup` brief. Reasons it is being retired:

- Status lived in the directory name -- renaming a directory was the
  status transition. That made transitions unverifiable, racy across
  agents, and impossible to query with anything other than `find`.
- Two writers (filesystem + DB after the substrate refactor) drift apart
  silently; only one can be the source of truth.
- Cascade deletes across ACs, milestones, plans, and tasks require FK
  semantics, which a directory tree cannot provide.

If you find code, docs, or skills that still describe the directory
convention, flag them in `cross_layer_impacts` -- do not edit them as a
side effect of a brief task.

## After Brief

`gaia brief show <slug>` prints the full brief. Present it. Ask:
"Does this capture what you want?" When confirmed, suggest dispatching
to gaia-planner to create a plan.

## Anti-Patterns

- **Writing `brief.md` to disk** -- the DB is the source of truth; any file
  on disk is either build output or stale legacy that will be deleted.
- **Renaming directories to change status** -- there are no directories;
  status is a column. Use `gaia brief set-status`.
- **Skipping `--status=draft` on creation** -- creating directly in `open`
  bypasses the review window where the user confirms ACs.
- **Hard-deleting a brief that has plan history** -- prefer
  `set-status archived`. Delete is for genuinely abandoned drafts.
