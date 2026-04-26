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

## Cuando llegas aquí

El orquestador cargó esta skill porque la conversación entró en Cerrar:
el usuario y él han acordado varias cosas y es momento de materializarlas.
No estás aquí porque la petición superó un umbral de tamaño. Estás aquí
porque hay acuerdos que capturar.

Tu trabajo:
1. Resumir los acuerdos que ya emergieron en la conversación previa —
   no re-descubrirlos desde cero.
2. Preguntar sólo lo que falte para convertir los acuerdos en AC
   reproducibles (evidence types, surface type).
3. Escribir el brief y presentarlo al usuario para validar.

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

2. **Write brief.md** -- Use the structure below. Write to:
   `.claude/project-context/briefs/open_{feature-name}/brief.md`
   where `{feature-name}` is a kebab-case slug.

   **Directory prefix convention:**
   - `open_` -- draft or ready, no work started yet (this skill always creates with `open_`)
   - `in-progress_` -- work has begun
   - `closed_` -- complete, verified, or done

   Transitions between prefixes are done with `gaia plans rename`. This skill
   only ever creates with `open_`.

## Brief Structure

The frontmatter is the executable source of truth (orchestrator parses it
with `yaml.safe_load`). The body's `## Acceptance Criteria` section mirrors
it as a human summary.

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

## After Brief

Present the full brief. Ask: "Does this capture what you want?"
When confirmed, suggest dispatching to gaia-planner to create a plan.
