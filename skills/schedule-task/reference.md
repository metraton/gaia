# Schedule Task -- Reference

Prompt templates, parameter extraction examples, agent selection guide, and scheduling patterns. Read on-demand when dispatching tasks.

## Prompt Templates

### Agentic Loop

```
## Agentic Loop Task

Carga la skill agentic-loop y sigue su protocolo.

goal: {goal}
eval_command: {eval_command}
metric: {metric}
direction: {direction}
threshold: {threshold}
max_iterations: {max_iterations}
files_in_scope: {files}
branch: {branch_prefix}-{date}
```

The `Carga la skill agentic-loop` line triggers skill injection -- it must appear exactly as written. The agent receives the `agentic-loop` skill which governs the entire iteration protocol. Do not duplicate any loop instructions in this prompt.

### Simple Task

```
## Task

{goal}

Expected outcome: {what success looks like}
Files in scope: {files}
Verification: {how to confirm it worked -- test command, expected output, etc.}
```

No loop header, no iteration parameters. The agent executes once and verifies.

### Two-Phase

**Phase 1 prompt (create eval):**
```
## Create Evaluation Script

Goal: Create a repeatable eval command for measuring {metric}.
The command must print a line matching: METRIC {metric_name}={number}

Context: {what the metric measures and why}
Files to evaluate: {files}
Output: A script or command that can run repeatedly and produce consistent measurements.
```

**Phase 2 prompt (after Phase 1 completes):**
Use the agentic-loop template above, with `eval_command` set to whatever Phase 1 produced.

## Parameter Extraction Examples

Natural language contains the parameters -- extract them, do not invent them.

| User says | Extracted parameters |
|-----------|---------------------|
| "Mejora los tests hasta que pasen al 95%" | goal: improve test pass rate, metric: pass_rate, direction: higher, threshold: 95, eval_command: ASK |
| "Reduce el bundle size a menos de 200kb" | goal: reduce bundle size, metric: bundle_size_kb, direction: lower, threshold: 200, eval_command: ASK |
| "Optimiza el tiempo de respuesta del API" | goal: optimize API response time, metric: response_time_ms, direction: lower, threshold: ASK, eval_command: ASK |
| "Trabaja en esto hasta que no haya errores de lint" | goal: fix all lint errors, metric: error_count, direction: lower, threshold: 0, eval_command: ASK (likely `npm run lint`) |
| "Cada noche corre los tests y avísame si fallan" | type: simple-task + schedule, recurring: true, cron: nightly |
| "Itera sobre el CSS hasta que Lighthouse de 90+" | goal: improve Lighthouse score, metric: performance, direction: higher, threshold: 90, eval_command: ASK |

When `ASK` appears, the parameter must be confirmed with the user before dispatch.

### Confirming missing parameters

Present what you extracted and what is missing:

```
Entendido -- quieres mejorar el pass rate de tests hasta 95%.

Para armar el loop necesito:
- eval_command: el comando que corre los tests y reporta el porcentaje
  (e.g., `pytest tests/ --tb=short 2>&1 | python parse_results.py`)
- files_in_scope: que archivos puede modificar el agente?

Con eso lanzo el loop.
```

## Agent Selection Guide

Match by the FILES the agent will modify, not by the topic of conversation.

| If the agent needs to modify... | Route to |
|---------------------------------|----------|
| `hooks/`, `modules/`, `adapters/`, `skills/`, `agents/`, `config/surface-routing.json` | gaia-system |
| `src/`, `lib/`, `tests/`, `package.json`, `Dockerfile`, `.github/workflows/` | developer |
| Kubernetes manifests, `HelmRelease`, `Kustomization`, Flux configs | gitops-operator |
| `.tf`, `.hcl`, `terragrunt.hcl`, Terraform modules | terraform-architect |
| Nothing -- inspect logs, pods, cloud resources | cloud-troubleshooter |

When files span two domains (e.g., app code + K8s manifest), dispatch to both agents in parallel with separate prompts.

## CronCreate Examples

### Nightly test run

```
CronCreate:
  schedule: "0 3 * * *"        # 3 AM daily
  prompt: "Run pytest on /path/to/project and report results. If any tests fail, summarize the failures."
  recurring: true
```

### Weekly optimization loop

```
CronCreate:
  schedule: "0 2 * * 1"        # 2 AM every Monday
  prompt: |
    ## Agentic Loop Task

    Carga la skill agentic-loop y sigue su protocolo.

    goal: Improve test coverage
    eval_command: pytest --cov=src --cov-report=term | python parse_coverage.py
    metric: coverage_pct
    direction: higher
    threshold: 90
    max_iterations: 15
    files_in_scope: src/, tests/
    branch: improve/coverage-{date}
  recurring: true
```

### One-shot scheduled task

```
CronCreate:
  schedule: "30 22 * * *"      # Tonight at 10:30 PM
  prompt: "Run the full integration test suite and create a summary report."
  recurring: false
```

### Common cron expressions

| Expression | Meaning |
|-----------|---------|
| `0 3 * * *` | Every day at 3 AM |
| `0 2 * * 1` | Every Monday at 2 AM |
| `0 */6 * * *` | Every 6 hours |
| `0 9 * * 1-5` | Weekdays at 9 AM |
| `0 0 1 * *` | First of each month at midnight |

**7-day limit warning:** Recurring cron jobs have a 7-day maximum lifetime. After 7 days, the schedule expires and must be recreated. Always inform the user of this constraint when setting up recurring tasks.

## Example Conversations

### Agentic loop dispatch

**User:** "Mejora la cobertura de tests hasta 90%"

**Classification:**
- Measurable? Yes (coverage tools exist)
- Improvable? Yes (can add tests iteratively)
- Type: agentic-loop

**Missing params:** eval_command, files_in_scope

**Orchestrator response:**
"Para lanzar el loop de mejora necesito saber:
1. El comando que mide cobertura (e.g., `pytest --cov=src`)
2. Que archivos puede tocar el agente?
Con eso arranco."

**User:** "pytest --cov=src --cov-report=term, puede tocar src/ y tests/"

**Dispatch:** Agent(developer) with agentic-loop prompt, all params filled.

### Simple task dispatch

**User:** "Corre los tests y dime si pasan"

**Classification:**
- Measurable? Yes
- Improvable? No (pass/fail, no iteration)
- Type: simple-task

**Dispatch:** Agent(developer) with simple-task prompt: "Run the test suite and report results."

### Two-phase dispatch

**User:** "Quiero que el API responda en menos de 200ms pero no tengo benchmarks"

**Classification:**
- Measurable? Creatable (need to build a benchmark first)
- Improvable? Yes
- Type: two-phase

**Phase 1 dispatch:** Agent(developer) -- "Create a benchmark script that measures API response time and outputs METRIC response_time_ms={number}"

**Phase 2 dispatch (after Phase 1):** Agent(developer) with agentic-loop prompt using the eval_command from Phase 1.

### Manual review warning

**User:** "Mejora la UX del dashboard"

**Classification:**
- Measurable? No (subjective, no automated metric)
- Type: manual-review

**Orchestrator response:**
"La UX no se puede medir automaticamente -- un loop no puede evaluar mejoras subjetivas. Alternativas:
1. Si hay metricas proxy (Lighthouse accessibility, load time), puedo iterar sobre esas
2. Puedo hacer cambios puntuales y presentartelos para review manual
3. Si defines criterios especificos (e.g., 'reduce clicks to complete X'), puedo medir eso

Cual prefieres?"

### Scheduled task

**User:** "Cada noche corre los tests y avísame si hay fallos"

**Classification:**
- Type: simple-task + schedule
- Recurring: true

**Dispatch:**
```
CronCreate:
  schedule: "0 3 * * *"
  prompt: "Run pytest on /path/to/project. Report any failures with file, test name, and error message."
  recurring: true
```

**Orchestrator confirms:** "Listo -- programado para las 3 AM cada noche. Recuerda que los cron jobs expiran a los 7 dias y hay que renovarlos."
