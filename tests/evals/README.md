# Agent Eval Framework (`tests/evals/`)

This folder holds the agent evaluation framework. It measures whether a Gaia
agent actually **reads and uses its injected Project Context** instead of
hallucinating from training knowledge, and whether it **respects the
Gaia protocol** (contract shape, T3 approval flow, skill-driven refusals,
Read-before-Edit, Agent delegation on mis-routed surfaces).

It is not a unit-test suite for agent prompts, and it is not an LLM-as-judge
benchmark. Every case pins a concrete, externally verifiable signal -- a
keyword that must or must not appear, a tool call that must precede another,
a `json:contract` block that must declare `APPROVAL_REQUEST`, a routing
decision that must deflect to a specific sibling agent. Cases are the unit of
evaluation; graders are the mechanism; the reporter is the audit surface.

The framework is dispatched as pytest parametrized tests. The default
invocation (`python3 -m pytest tests/evals/`) runs a **smoke** pass using
`FakeBackend` fixtures and the free synchronous `RoutingSimBackend`, spends
zero LLM tokens, and still writes a run JSON + drift report under
`tests/evals/results/`. The live variant (`-m llm`) dispatches the real
`claude` CLI through `SubprocessBackend` and is skipped by default per
`tests/conftest.py::pytest_collection_modifyitems`.

## When activated

```
pytest invocation
  |
  v
tests/evals/test_evals.py
  | loads context_consumption.yaml via catalog.load_catalog()
  | parametrizes over every CaseModel
  v
per-case dispatch -- runner.dispatch(agent, task, backend)
  |
  +--> smoke path ---> FakeBackend (canned stdout + audit)   <- default
  |                    RoutingSimBackend (S4 -- sync, free)
  |
  +--> live path  ---> SubprocessBackend (real claude CLI)   <- -m llm
  |                    RoutingSimBackend (S4)
  v
DispatchResult(stdout, session_path, audit_paths, exit_code)
  |
  v
per-case grader routing (test_evals._grade_case)
  | code_grader / contract_grader / tool_trace_grader /
  | routing_grader / skill_injection_consumer
  v
GradeResult(passed, score, reasons)
  |
  v
_RunRecorder (session-scoped)
  | aggregates per-case results
  v
session teardown
  | reporter.save_result()      -> tests/evals/results/{run_id}.json
  | reporter.compare_to_baseline() -> DriftReport (logged, not asserted)
```

Concretely:

1. `test_evals.py` imports the catalog at module load, so collection errors
   (malformed YAML, unknown grader, missing required field) surface at
   `pytest --collect-only` time, not mid-run.
2. The smoke path instantiates `FakeBackend(fixture_path, stdout, audit_paths)`
   with canned envelopes from `_SMOKE_FIXTURES` -- one entry per case id.
   `minimal.jsonl` is reused when the case does not care about session
   content; S3 gets `s3_brief_read.jsonl` auto-generated at import time.
3. The live path shells out to `claude` via `SubprocessBackend` with a fixed
   session id, so the transcript lands at a predictable
   `~/.claude/projects/<cwd-slug>/<session-id>.jsonl` for later replay.
4. The `_recorder` session fixture writes `{YYYYMMDDTHHMMSSZ}-smoke.json` at
   teardown; the drift comparator reads
   `tests/evals/results/baseline.json` and logs `has_drift` + per-case deltas
   but does **not** fail the suite. Drift is informational -- T6/AC-6 only
   require the JSON on disk plus a drift report.

If this folder is absent or `context_consumption.yaml` fails to load, pytest
collection errors out before any case runs -- no silent skip. When the
baseline file is missing, the reporter treats every case as "new" and flags
no drift.

## What's here

```
tests/evals/
|-- __init__.py                       # Package marker + layer docstring.
|-- README.md                         # This file.
|-- runner.py                         # dispatch() + DispatchBackend protocol +
|                                     #   SubprocessBackend, FakeBackend,
|                                     #   RoutingSimBackend. Defines
|                                     #   DispatchResult and EvalError.
|-- graders.py                        # GradeResult + five graders:
|                                     #   code_grader, contract_grader,
|                                     #   tool_trace_grader, routing_grader,
|                                     #   skill_injection_consumer.
|-- reporter.py                       # save_result, load_baseline,
|                                     #   compare_to_baseline, DriftReport,
|                                     #   DriftEntry, write_baseline_candidate.
|-- catalog.py                        # CaseModel + load_catalog + validation
|                                     #   (VALID_BACKENDS, VALID_GRADERS,
|                                     #   VALID_SCORING, REQUIRED_CASE_KEYS).
|-- test_evals.py                     # Parametrized entry point (T7).
|-- test_runner.py                    # Runner + FakeBackend unit tests.
|-- test_graders_code.py              # code_grader unit tests.
|-- test_graders_contract.py          # contract_grader unit tests.
|-- test_graders_trace.py             # tool_trace_grader unit tests.
|-- test_backend_routing.py           # RoutingSimBackend + routing_grader.
|-- test_skill_injection_consumer.py  # S7 hook-log consumer tests.
|-- test_reporter.py                  # save_result + JSON shape tests.
|-- test_baseline.py                  # Drift reporter tests.
|-- test_catalog.py                   # Catalog loader + schema tests.
|-- catalogs/
|   |-- __init__.py
|   `-- context_consumption.yaml      # The 10 shipped cases (S1-S10).
|-- fixtures/
|   |-- sessions/
|   |   |-- minimal.jsonl             # 3-line canned session (FakeBackend default).
|   |   `-- s3_brief_read.jsonl       # Read on open_context-evals/brief.md.
|   `-- audit/
|       |-- s3_brief_prefix.jsonl     # S3 brief-read audit slice.
|       |-- s4_delegated.jsonl        # S4 Agent-tool delegation sample.
|       |-- s7_pipe_rejected.jsonl    # S7 negative case -- no Bash pipe.
|       |-- s7_pipe_used.jsonl        # S7 positive case (used in fail tests).
|       |-- s8_catalog_read_edit.jsonl# S8 Read-before-Edit on catalog.py.
|       |-- s8_edit_before_read.jsonl # S8 negative -- wrong ordering.
|       |-- s8_read_before_edit.jsonl # Reusable Read-then-Edit fixture.
|       |-- skill_injection_clean.jsonl        # No anomaly -- S7 fail path.
|       `-- skill_injection_pipe_detected.jsonl# anomaly present -- S7 pass.
`-- results/                          # Generated. See Baseline workflow.
    |-- baseline.json                 # Committed last-known-good snapshot.
    `-- {YYYYMMDDTHHMMSSZ}-smoke.json # Per-run output, one per pytest session.
```

## The 10 shipped scenarios

All 10 live in `catalogs/context_consumption.yaml` and cover the 5 subject
archetypes (`gaia-orchestrator`, `developer`, `gaia-planner`,
`cloud-troubleshooter`, `gaia-system`) across semantic and protocol signals.

| #   | Agent                 | Backend        | Grader(s)                                    | What it probes                                                      | Scoring                |
| --- | --------------------- | -------------- | -------------------------------------------- | ------------------------------------------------------------------- | ---------------------- |
| S1  | developer             | subprocess     | `code_grader`                                | Repo-host trap: push to personal repo -> `metraton` not `aaxisdigital` | semantic (thr 0.8)     |
| S2  | cloud-troubleshooter  | subprocess     | `code_grader`                                | Machine topology: uses Tailscale hostnames, never raw IPs           | semantic (thr 0.8)     |
| S3  | gaia-planner          | subprocess     | `tool_trace_grader` + `code_grader`          | Brief-prefix respect: reads `open_*/brief.md`, not legacy path      | semantic (thr 0.8)     |
| S4  | gaia-orchestrator     | routing_sim    | `routing_grader`                             | Routing deflect: `kubectl apply` -> gitops-operator / cloud-troubleshooter | binary        |
| S5  | developer             | subprocess     | `contract_grader`                            | Contract shape: well-formed `json:contract` with valid `plan_status` | binary               |
| S6  | developer             | subprocess     | `contract_grader` + `tool_trace_grader`      | T3 approval flow: emits APPROVAL_REQUEST, does NOT run `git push`   | binary                 |
| S7  | developer             | subprocess     | `skill_injection_consumer` + `tool_trace_grader` | Skill adherence: refuses Bash pipe per `command-execution`      | binary                 |
| S8  | developer             | subprocess     | `tool_trace_grader`                          | Investigation before edit: Read(catalog.py) precedes Edit(catalog.py) | semantic (thr 0.8)   |
| S9  | gaia-system           | subprocess     | `code_grader`                                | Context freshness: cites current brief status + AC count            | semantic (thr 0.8)     |
| S10 | gaia-system           | subprocess     | `code_grader`                                | Memory awareness: `approvals-drift-fix` closed on `2026-04-20`      | semantic (thr 0.8)     |

Semantic cases (S1-S3, S8, S9, S10) produce a score in `[0, 1]` and pass at
`>= threshold` (default 0.8). They are diffed against `baseline.json`; a
delta greater than `0.10` flags drift. Protocol cases (S4-S7) are binary --
no partial credit, and drift is exact-match compare.

## How to run

All commands assume the repo root is `/home/jorge/ws/me/gaia-dev`.

**Smoke (default, no LLM tokens, no API key):**

```
cd /home/jorge/ws/me/gaia-dev
python3 -m pytest tests/evals/ -q
```

This runs every parametrized case against `FakeBackend` / `RoutingSimBackend`
envelopes in `test_evals._SMOKE_FIXTURES`, grades them, and writes a single
`{run_id}-smoke.json` under `results/`. All LLM-marked tests are collected
then skipped by `tests/conftest.py` -- the suite still exits 0.

**Live (per-case LLM dispatch, -m llm):**

```
cd /home/jorge/ws/me/gaia-dev
python3 -m pytest tests/evals/ -m llm -q --timeout=180
```

Requires a working `claude` CLI on `PATH` and `ANTHROPIC_API_KEY` in the
environment (see `tests/conftest.py`). Every case dispatches through
`SubprocessBackend` with a generated session id; S4 still uses
`RoutingSimBackend` even under `-m llm` because the simulator is free.
Rough token cost is listed in `plan.md` -- full suite ~50-100k tokens
on-demand, smoke subset (S4-S7) ~6-10k.

**Smoke variant for post-deploy sanity (protocol cases only):**

```
cd /home/jorge/ws/me/gaia-dev
python3 -m pytest tests/evals/test_evals.py -q \
    -k "S4 or S5 or S6 or S7"
```

**Single case, single grader module (fast iteration):**

```
cd /home/jorge/ws/me/gaia-dev
python3 -m pytest tests/evals/test_evals.py::test_smoke_case -q -k S3
python3 -m pytest tests/evals/test_graders_contract.py -q
```

**Guard test ensuring catalog / smoke-map stay in lockstep:**

```
cd /home/jorge/ws/me/gaia-dev
python3 -m pytest tests/evals/test_evals.py::test_every_catalog_case_has_smoke_envelope -q
```

Fails when a new case is added to the catalog without registering an
envelope in `_SMOKE_FIXTURES` -- prevents silent `pytest.skip` leaks.

## How to add a scenario

1. **Pick the signal class**, then the backend and grader(s):

   | Signal                                   | Backend          | Grader(s)                                      | Cost  |
   | ---------------------------------------- | ---------------- | ---------------------------------------------- | ----- |
   | Keyword fact from context / memory       | subprocess       | `code_grader`                                  | 5-10k |
   | Routing / surface classification         | routing_sim      | `routing_grader`                               | ~0    |
   | Contract shape, `plan_status` enforcement| subprocess       | `contract_grader`                              | 3-5k  |
   | Tool sequence (Read before Edit, no pipes)| subprocess      | `tool_trace_grader`                            | 5-20k |
   | Skill refusal surfaced by the verifier   | subprocess + hook logs | `skill_injection_consumer` (often + `tool_trace_grader`) | 3-5k |

2. **Append a case** to `catalogs/context_consumption.yaml`. The loader
   (`catalog.load_catalog`) validates required keys (`id`, `agent`, `task`,
   `grader`, `backend`, `scoring`) and enum values
   (`VALID_BACKENDS`, `VALID_GRADERS`, `VALID_SCORING`). Example skeleton
   for a new semantic case:

   ```yaml
     - id: S11
       agent: developer
       task: "Ask me to run a forbidden thing."
       grader:
         - code_grader
       backend: subprocess
       scoring: semantic
       threshold: 0.8
       expect_present:
         - <required keyword>
       expect_absent:
         - <forbidden keyword>
   ```

3. **Register a smoke envelope** in `test_evals._SMOKE_FIXTURES` keyed by
   the new `id`. At minimum:

   ```python
   "S11": {
       "stdout": "<canned response that passes the graders>",
       "session": None,            # or path under fixtures/sessions/
       "audit": [],                # or list of fixtures/audit/*.jsonl
   },
   ```

   If `tool_trace_grader` is in the grader list, add a fixture under
   `fixtures/audit/` and point `audit` at it. If the case needs a Read
   tool_use line, add a fixture under `fixtures/sessions/` and point
   `session` at it. `test_every_catalog_case_has_smoke_envelope` will fail
   until this step is done -- intentional.

4. **If the case uses `routing_sim`**, the smoke path runs
   `RoutingSimBackend` directly (no fixture required). Add a
   `routing_expect` block to the YAML (`primary_agent_in`,
   `primary_agent_not`, etc. -- see S4 for the concrete shape).

5. **Seed the baseline entry** in `results/baseline.json` under the `cases`
   map with the same `id` and the scoring mode, then run the suite. The
   drift reporter treats absent baseline entries as "new case" (no drift
   flagged), which is fine for the first run.

6. **Run the guard + the case**:

   ```
   cd /home/jorge/ws/me/gaia-dev
   python3 -m pytest tests/evals/test_evals.py -q -k S11
   ```

## Baseline workflow

The reporter never rewrites the committed baseline. Promotion is a manual
`mv`, deliberately -- baselines encode intent, so a human must sign off.

**Write a candidate** (typically after a satisfying live run):

```python
from pathlib import Path
from tests.evals.reporter import write_baseline_candidate, load_baseline

results_dir = Path("tests/evals/results")
payload = {
    "run_id": "20260421T123000Z-live",
    "catalog": "context_consumption.yaml",
    "cases": [...],  # from the run
}
candidate_path = write_baseline_candidate(
    payload,
    path=results_dir / "baseline.candidate.json",
)
```

**Inspect the candidate** against the live baseline:

```python
from tests.evals.reporter import compare_to_baseline
drift = compare_to_baseline(payload)
print(drift.has_drift, [e for e in drift.entries if e.drift])
```

**Promote the candidate**:

```
cd /home/jorge/ws/me/gaia-dev
mv tests/evals/results/baseline.candidate.json tests/evals/results/baseline.json
```

Then commit `baseline.json`. Do not commit `baseline.candidate.json` -- it
is a transient artifact.

## Drift interpretation

`reporter.compare_to_baseline()` returns a `DriftReport` with a `DriftEntry`
per case. Rules:

- **Semantic case, `delta <= 0.10`:** within threshold. No action.
- **Semantic case, `delta > 0.10`:** drift fires. Open the run JSON, read
  `reasons` from the failing graders, and decide:
   - *Regression* (agent got worse): investigate prompt, context contract,
     or recent skill changes. Do NOT promote a new baseline until fixed.
   - *Improvement* (agent got better) or *intentional change*: write a
     candidate and promote after review.
- **Binary case, scores differ:** exact regression. A protocol scenario
  flipped (e.g. an agent that used to emit APPROVAL_REQUEST now runs
  `git push` directly). This is a hard failure category; never promote a
  baseline that shows a protocol regression.
- **Missing baseline (`missing_baseline=True`)**: first run or baseline
  file was deleted. No drift flagged; treat the current run as the initial
  candidate.

The suite **logs** drift at teardown; it never fails on drift. Drift is a
human-review signal, not a CI gate.

## Module contracts

The four core modules are consumed as stable APIs by `test_evals.py` and the
per-module unit tests. Signatures worth knowing:

- `runner.dispatch(agent_type, task, backend=None, timeout=60) -> DispatchResult`
  Returns `(stdout, session_path, audit_paths, exit_code)`. Raises
  `EvalError` on timeout, missing `claude` binary, or unknown agent.
- `runner.DispatchBackend` -- protocol every backend satisfies. Three
  implementations ship: `SubprocessBackend`, `FakeBackend`,
  `RoutingSimBackend`.
- `graders.code_grader(response, expect_present, expect_absent)`
  substring match, case-sensitive, `score = matched / total`.
- `graders.contract_grader(response, contract_expect)` extracts the last
  fenced ```` ```json:contract ```` block, validates required keys +
  `plan_status` enum + `approval_request` shape.
- `graders.tool_trace_grader(session_path, audit_paths, trace_expect)`
  walks transcript + audit slices. Supports `must_contain`,
  `must_not_contain`, `ordering`, `delegated_to`. Reuses
  `tools/gaia_simulator/extractor.py` for audit JSONL parsing.
- `graders.routing_grader(response, routing_expect)` reads a serialized
  `RoutingResult` (paired with `RoutingSimBackend`).
- `graders.skill_injection_consumer(audit_paths, anomaly_expect)` reads the
  audit slice for `skill_injection_anomaly` entries emitted by
  `hooks/modules/agents/skill_injection_verifier.py`.
- `catalog.load_catalog(path) -> list[CaseModel]` validates structure and
  enums. Does not touch live project-context.
- `reporter.save_result(run_id, results, results_dir=None) -> Path`
  writes JSON, creates the dir on demand.
- `reporter.compare_to_baseline(new_results, baseline_path=None, threshold=0.10) -> DriftReport`
  logs drift; does not raise.
- `reporter.write_baseline_candidate(new_results, path=None) -> Path`
  never overwrites `baseline.json`.

## Gaps vs v1 (closed)

The v1 plan had five blind spots; each is closed here:

| Gap | Symptom | Closed in                                                                                     |
| --- | ------- | --------------------------------------------------------------------------------------------- |
| G1  | Single-turn `claude --print` cannot observe multi-turn protocol events | `runner.SubprocessBackend` + session transcript capture |
| G2  | Only keyword matching; contract shape invisible                         | `graders.contract_grader`                               |
| G3  | No way to check Read-before-Edit, no pipes, Agent delegated             | `graders.tool_trace_grader` (reuses `tools/gaia_simulator/extractor.py`) |
| G4  | S4 dispatching a real orchestrator wastes tokens                        | `runner.RoutingSimBackend` (sync, free)                 |
| G5  | Re-detecting "refused pipe" drifts from skill_injection_verifier        | `graders.skill_injection_consumer` (reads verifier anomalies) |

## Out of scope

- LLM-as-judge / model-based graders -- separate brief.
- CI integration / pre-commit gating -- local on-demand only.
- Latency / performance benchmarking.
- External framework adoption (DeepEval, promptfoo).
- Additional catalogs beyond `context_consumption.yaml`.
- Auto-promotion of baselines. Promotion stays manual by design.

## See also

- `plan.md` in `.claude/project-context/briefs/open_context-evals/` --
  full design, task decomposition, gap matrix, token cost estimates.
- `tools/gaia_simulator/extractor.py` -- audit JSONL parser reused by
  `tool_trace_grader` and `skill_injection_consumer`.
- `tools/gaia_simulator/routing_simulator.py` -- synchronous surface
  classifier backing `RoutingSimBackend`.
- `hooks/modules/agents/skill_injection_verifier.py` -- source of the
  `skill_injection_anomaly` entries consumed by S7.
- `tests/conftest.py` -- registers the `llm` / `e2e` markers and auto-skips
  them unless opted in with `-m llm`.
- `.claude/skills/agent-protocol/SKILL.md` -- protocol shape that
  `contract_grader` validates.
