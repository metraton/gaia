# Agent Eval Framework (`tests/evals/`)

This folder holds the agent evaluation framework. It measures whether a Gaia
agent actually **reads and uses its injected Project Context** instead of
hallucinating from training knowledge, and whether it **respects the
Gaia protocol** (contract shape, T3 approval flow, skill-driven refusals,
Read-before-Edit, Agent delegation on mis-routed surfaces).

It is not a unit-test suite for agent prompts, and it is not an LLM-as-judge
benchmark. Every case pins a concrete, externally verifiable signal -- a
keyword that must or must not appear, a tool call that must precede another,
a `agent_contract_handoff` block that must declare `APPROVAL_REQUEST`, a routing
decision that must deflect to a specific sibling agent. Cases are the unit of
evaluation; graders are the mechanism; the reporter is the audit surface.

The framework is dispatched as pytest parametrized tests. There is one
invocation (`python3 -m pytest tests/evals/`): it runs every case, spends
zero LLM tokens, and writes a run JSON + drift report under
`tests/evals/results/`.

## What this program measures, and what it declines to

**The unit of measurement is the flow rooted in the orchestrator, never an
agent in isolation.** A Gaia session begins at the orchestrator -- `gaia
install` writes `agent: gaia-orchestrator` into the workspace settings
(`bin/cli/_install_helpers.py`) -- and everything a specialist depends on is
produced by that route: the per-agent context filter, the dispatch identity
the hooks key on, the contract binding. A prompt is therefore the only honest
input to an eval case, and the flow from that prompt onward is the only
honest subject.

Two cases (S1, S6) used to dispatch `claude --print --agent <specialist>`
directly. That shape is not a cheaper version of the real thing -- it is a
different thing: it skips the orchestrator, so it skips the context injection
and the dispatch identity, and what it measures is the bypass rather than the
flow. Both were removed along with the `SubprocessBackend` that dispatched
them; see [Why cases were removed](#why-cases-were-removed).

**One case in this catalog can fail today: S4.** That is the real number, not
an aspiration. What remains is a deterministic layer -- the real router over
the real routing table (S4), the real PreToolUse hook replayed against the
golden security catalog, the production skill-injection detector over real
transcripts -- plus the grader/runner/reporter unit tests. That layer is
genuine and it is not a simulation of the components it exercises; the router
and the hook it runs ARE production code. What is missing is an
orchestrator-rooted end-to-end case: prompt in, routing plus dispatch plus
contract out. Nothing in the harness forbids one -- it needs a backend that
starts a session the way a session actually starts, which the deleted
`--agent` backend by construction could not.

**There is no canned-response path, deliberately.** The suite used to carry a
mode in which each case was graded against a `FakeBackend` stdout string
written by `test_evals.py` itself. That mode graded the fixture, not the
system: no behaviour whatsoever could make it fail, and it is how several
cases stayed green for months while asserting things that had stopped being
true. `test_evals.test_cases_run_real_machinery` now forbids reintroducing
it -- a case is either answered by production code, or the property it
measures belongs in a unit test.

## When activated

```
pytest invocation
  |
  v
tests/evals/test_evals.py
  | loads context_consumption.yaml via catalog.load_catalog()
  v
per-case dispatch -- runner.dispatch(agent, task, backend=...)
  |
  +--> RoutingSimBackend (sync, real router over the real table, 0 tokens)
  v
DispatchResult(stdout, session_path, audit_paths, exit_code)
  |
  v
per-case grader routing (test_evals._grade_case)
  | code_grader / tool_trace_grader /
  | routing_grader / skill_injection_consumer
  v
GradeResult(passed, score, reasons)
  |
  v
_RunRecorder (session-scoped)
  | aggregates per-case results
  v
session teardown
  | reporter.save_result()          -> results/{run_id}.json
  | reporter.compare_to_baseline()  -> DriftReport
  | reporter.enforce_no_regression()-> RAISES on a score below baseline
```

Concretely:

1. `test_evals.py` imports the catalog at module load, so collection errors
   (malformed YAML, unknown grader, missing required field) surface at
   `pytest --collect-only` time, not mid-run.
2. Dispatch builds a real `RoutingSimulator` over the DB-backed
   `surface_routing` table (seeded per-test by the module's autouse
   `_seeded_routing_db` fixture), so the case exercises production routing.
3. `runner.dispatch()` takes `backend` as a REQUIRED keyword argument. It has
   no default on purpose: the former default shelled out to `claude --print
   --agent <specialist>`, so a forgetful caller silently got the one dispatch
   shape this program does not measure.
4. The `_recorder` session fixture writes `{YYYYMMDDTHHMMSSZ}-evals.json` at
   teardown, diffs it against `tests/evals/results/baseline.json`, and
   **fails the run** when any case scored below its baseline. See
   [Drift interpretation](#drift-interpretation).

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
|                                     #   RoutingSimBackend, HookLogReplayBackend,
|                                     #   FakeBackend. Defines DispatchResult
|                                     #   and EvalError.
|-- graders.py                        # GradeResult + five graders:
|                                     #   code_grader, tool_trace_grader,
|                                     #   routing_grader, decision_grader,
|                                     #   skill_injection_consumer.
|-- reporter.py                       # save_result, load_baseline,
|                                     #   compare_to_baseline, DriftReport,
|                                     #   DriftEntry, write_baseline_candidate.
|-- catalog.py                        # CaseModel + load_catalog + validation
|                                     #   (VALID_BACKENDS, VALID_GRADERS,
|                                     #   VALID_SCORING, REQUIRED_CASE_KEYS).
|-- test_evals.py                     # Parametrized entry point + catalog guards.
|-- test_runner.py                    # Runner + FakeBackend unit tests.
|-- test_graders_code.py              # code_grader unit tests.
|-- test_graders_trace.py             # tool_trace_grader unit tests.
|-- test_graders_decision.py          # decision_grader unit tests.
|-- test_backend_routing.py           # RoutingSimBackend + routing_grader.
|-- test_security_golden.py           # security_decisions.yaml vs the real hook.
|-- test_skill_injection_consumer.py  # skill_injection_consumer unit tests.
|-- test_skill_injection_dispatch_reality.py  # real detector vs real transcripts.
|-- test_reporter.py                  # save_result + JSON shape tests.
|-- test_baseline.py                  # Drift reporter + regression gate tests.
|-- test_catalog.py                   # Catalog loader + schema tests.
|-- catalogs/
|   |-- __init__.py
|   |-- context_consumption.yaml      # Behavioural cases (S4).
|   `-- security_decisions.yaml       # Golden hook decisions (SEC-*).
|-- fixtures/
|   |-- sessions/
|   |   `-- minimal.jsonl             # 3-line canned session (test_runner.py).
|   |-- audit/                        # Inputs to the grader UNIT tests. No
|   |   |                             #   catalog case is graded against these.
|   |   |-- s3_brief_prefix.jsonl     # Read-only trace.
|   |   |-- s4_delegated.jsonl        # Agent-tool delegation sample.
|   |   |-- s7_pipe_rejected.jsonl    # Bash trace with no pipe.
|   |   |-- s7_pipe_used.jsonl        # Bash trace with a pipe.
|   |   |-- s8_edit_before_read.jsonl # Wrong ordering.
|   |   |-- s8_read_before_edit.jsonl # Read-then-Edit ordering.
|   |   |-- skill_injection_clean.jsonl        # No anomaly.
|   |   `-- skill_injection_pipe_detected.jsonl# Anomaly present.
|   `-- transcripts/                  # Real (trimmed) + synthetic transcripts
|                                     #   for test_skill_injection_dispatch_reality.
`-- results/                          # Generated. See Baseline workflow.
    |-- baseline.json                 # Committed expected scores. Gated.
    `-- {YYYYMMDDTHHMMSSZ}-evals.json # Per-run output, one per pytest session.
```

Mutation-testing configs (`mutation-*.toml`, `equivalents-*.skip`,
`mutkill_approval_grants.py`, `evidence/`) also live here; they are a
separate concern from the case catalogs -- see the anti-staleness section.

## The shipped scenarios

`catalogs/context_consumption.yaml` holds ONE case. It held ten.

| #  | Agent             | Backend     | Runs      | Grader(s)        | What it probes                                                            | Scoring |
| -- | ----------------- | ----------- | --------- | ---------------- | ------------------------------------------------------------------------- | ------- |
| S4 | gaia-orchestrator | routing_sim | every run | `routing_grader` | Routing deflect: `kubectl apply` -> gitops-operator / cloud-troubleshooter | binary  |

Ids are not renumbered when a case is deleted. An id is the key the baseline,
every historical run JSON, and the git log all address a case by; recycling
`S1` for something unrelated would make every past record ambiguous. The gaps
are the record.

### Why cases were removed

| #   | Removed because                                                                                                                     |
| --- | ----------------------------------------------------------------------------------------------------------------------------------- |
| S2  | False premise, and it punished the correct answer: the Tailscale contract is not in the sections `cloud-troubleshooter` reads, and the right command (`tailscale status`) prints the very `100.` prefix the case forbade. |
| S3  | Asserted a retired filesystem layout. Briefs live in the DB and `.claude/project-context/` is empty, so a correct planner issues no `Read` at all. |
| S5  | Obsolete schema (`plan_status`), and fully redundant with the runtime contract gate plus `tests/contract/`.                          |
| S7  | Superseded by `test_skill_injection_dispatch_reality.py`, which runs the production detector over real transcripts, free and on every run. |
| S8  | Tautological (the harness made the violation impossible) and hazardous: it pointed an edit-accepting agent at `tests/evals/catalog.py`, a file of the repo under test. |
| S9  | Inverted oracle -- it forbade `status: closed` for a brief that is closed.                                                          |
| S10 | The expected token `2026-04-20` appears nowhere in the DB, and the grader punished correctly-phrased answers.                       |
| S1  | False premise: it measured a specialist invoked WITHOUT the orchestrator. Ran live for the first time and failed -- an `--agent` session never receives the per-agent context load (built only on the real dispatch path, from `parameters["subagent_type"]`), and `tests/conftest.py::_isolate_gaia_data_dir` forces an empty data dir the subprocess inherits, so the value S1 asked for was unreachable from inside the session. |
| S6  | Same false premise. Additionally blocked by the delegation gate denying Bash to the dispatched session (fixed in `77ec082`, pending an install). Its security half -- the hook blocks `git push` -- was already asserted for free by `SEC-T3-git-push`. |

The first seven were each unable to fail or asserting something untrue. S1 and
S6 are a different class: no agent misbehaved and no grader was wrong -- the
premise under them was. Neither baseline was ever measured; both were seeded at
`1.0` by hand, and the measured ceiling for both was `0.5`.

Two properties those cases reached for were real and survive, moved to where
they can be measured deterministically and for free:

- **Workspace isolation** (S1's other half) --
  `tests/hooks/modules/context/test_context_injector.py` builds the context
  for the `me` workspace against a DB that also holds a `work` workspace, and
  asserts the full literal remote is present and the sibling workspace's
  identifiers are not.
- **The hook blocks `git push`** (S6's other half) -- `SEC-T3-git-push` in
  `catalogs/security_decisions.yaml`, replayed against the real PreToolUse
  hook by `test_security_golden.py`.

Semantic cases produce a score in `[0, 1]` and pass at `>= threshold`
(default 0.8). Binary cases are pass/fail with no partial credit. Both are
diffed against `baseline.json` on every run.

## Anti-staleness rule for cosmic-ray sessions (AC-10)

A cosmic-ray session (`.sqlite`) records the test-command results from when
`cosmic-ray init`/`exec` last ran. Changing test files or the source module
WITHOUT re-running `cosmic-ray init` means `cr-rate` will report a stale
kill_rate — based on old results, not the current tests. This caused the
hardening loop to believe approval_grants "had not improved" when +101
mutants had actually been killed by new tests (AC-10 incident).

**RE-INIT IS OBLIGATORY** after any change to:
- any test file referenced in `test-command` (in the per-module `.toml`)
- the source module under mutation
- `test-command`, `module-path`, or `excluded-modules` in the `.toml`

**The `mutkill_approval_grants.py` harness does NOT suffer from this**: it
re-runs `mutate_and_test` against the current tests on every invocation.
Use it (not `cr-rate`) for an up-to-date kill_rate during the hardening loop.

**Detect stale sessions** with `--check-stale` (exits 1 if stale, 0 if fresh):

```
uv run python tests/evals/mutkill_approval_grants.py \
    --session approval-grants.sqlite \
    --toml tests/evals/mutation-approval-grants.toml \
    --check-stale
```

Without `--check-stale`, the guard still runs and prints a WARNING to stderr
if the session is older than any tracked file — but execution continues.

## Equivalent-mutant skip files use STABLE ids (re-init-proof)

Equivalent mutants (no input can distinguish them from the original) are
excluded from the kill-rate denominator via per-module `equivalents-*.skip`
files. Each non-comment line is keyed on the mutant's STABLE identity:

```
operator_name | start_row:start_col-end_row:end_col | occurrence
```

NOT the `job_id`. `cosmic-ray init` regenerates a fresh uuid4 `job_id` for every
mutant on every run, so a job_id-keyed skip silently matches NOTHING after a
re-init — the documented equivalents re-enter the denominator as phantom
"killable" mutants and the rate floats up to a **false 100%**. The stable id is
derived from the source AST, which `cosmic-ray init` reproduces byte-for-byte,
so it survives re-init. The harness (`mutkill_approval_grants.py`:
`stable_id` / `parse_skip_file` / `compute_skip_jobids`) resolves stable ids to
the current session's job_ids and prints a WARNING for any token that matches no
mutant (a stale id, or a span that moved — re-key it). Legacy 32-hex job_ids are
still honored for backward compatibility but are fragile; emit stable ids.

Per-module configs and their skip files:

| Module | `.toml` | `.skip` |
|--------|---------|---------|
| `tiers.py` | `mutation-tiers.toml` | `equivalents-tiers.skip` |
| `mutative_verbs.py` | `mutation-mutative-verbs.toml` | `equivalents-mutative-verbs.skip` |
| `blocked_commands.py` | `mutation-blocked-commands.toml` | `equivalents-blocked-commands.skip` |
| `approval_grants.py` | `mutation-approval-grants.toml` | `equivalents-approval-grants.skip` |
| `inline_ast_analyzer.py` | `mutation-inline-ast.toml` | `equivalents-inline-ast.skip` |
| `bash_validator.py` | `mutation-bash-validator.toml` | _(none yet — survivors untriaged)_ |
| `claude_code.py` | `mutation-claude-code.toml` | _(none yet — survivors untriaged)_ |

Baselines for the two newest modules:
`evidence/mutation-bash-validator-claude-code-baseline.md`.

## How to run

All commands assume the repo root is `/home/jorge/ws/me/gaia`.

**The whole suite (no LLM tokens, no API key, no opt-in flag):**

```
cd /home/jorge/ws/me/gaia
python3 -m pytest tests/evals/ -q
```

Runs S4 via the real routing simulator, every grader / runner / reporter unit
test, the golden security catalog against the real hook, and the
skill-injection reality check. Writes one `{run_id}-evals.json` under
`results/` and applies the baseline gate.

**Token cost: zero.** There is no `-m llm` lane in this folder and no
API key is needed. The full live suite cost ~50-100k tokens when the catalog
held ten cases, and almost none of that spend bought a signal: three of those
cases could not fail, two asserted premises the system had abandoned, two were
covered for free elsewhere, and the last two measured a dispatch shape Gaia
does not use. (`-m llm` still exists as a pytest marker and is still honored by
`tests/conftest.py`, but it is now `tests/layer2_llm_evaluation/`'s marker
alone -- nothing under `tests/evals/` carries it.)

**Single case, single grader module (fast iteration):**

```
cd /home/jorge/ws/me/gaia
python3 -m pytest tests/evals/test_evals.py -q -k S4
python3 -m pytest tests/evals/test_graders_decision.py -q
```

A `-k`-narrowed run is safe against the baseline gate: the diff only walks
the cases the run actually recorded, so unrun cases are not reported as
regressions.

**Catalog guards:**

```
cd /home/jorge/ws/me/gaia
python3 -m pytest tests/evals/test_evals.py -q -k "test_cases_run or test_every_case or test_baseline_has_no"
```

Three structural checks, each closing a way a case could slip outside the
gate: `test_cases_run_real_machinery` (no case may be answered by anything
but production code), `test_every_case_has_a_baseline_entry` (a case with no
baseline entry is a case the drift gate cannot fail), and
`test_baseline_has_no_entries_for_deleted_cases` (a baseline entry with no
case is a gate that never fires). The last two are the pair that must BOTH be
green after any case deletion: they close the gate from opposite sides.

## How to add a scenario

0. **First, try not to.** Ask what would make the case FAIL. If the answer is
   a fact you could read out of the DB, a function you could call, or a file
   you could open, write a unit test instead -- it is free, deterministic,
   and cannot be satisfied by a paraphrase. A case earns a slot in this
   catalog only when the thing under test is the *behaviour of the flow*: what
   the system does with a prompt, given what it already knows. Seven of the
   original ten failed this question.

0b. **Then check the premise.** A case must be rooted where a real session is
   rooted -- in the orchestrator. If answering it requires invoking one
   specialist directly, the case is measuring a mode Gaia does not route
   through, and it will fail for reasons that are not about behaviour at all.
   That is what killed S1 and S6, after they had sat in the catalog for months
   with hand-seeded passing baselines.

1. **Pick the signal class**, then the backend and grader(s):

   | Signal                                    | Backend          | Grader(s)                                         | Cost |
   | ----------------------------------------- | ---------------- | ------------------------------------------------- | ---- |
   | Routing / surface classification          | routing_sim      | `routing_grader`                                  | ~0   |
   | Hook permission decision                  | hook_log_replay  | `decision_grader` (use `security_decisions.yaml`) | ~0   |

   Those are the two backends that exist. `code_grader` and
   `tool_trace_grader` are still valid `VALID_GRADERS` entries with their
   own unit tests, but no backend currently produces the response text or
   session transcript they read -- the one that did was the `--agent`
   dispatcher, and it is gone. Reaching for them means first building an
   orchestrator-rooted backend.

2. **Append a case** to `catalogs/context_consumption.yaml`. The loader
   (`catalog.load_catalog`) validates required keys (`id`, `agent`, `task`,
   `grader`, `backend`, `scoring`) and enum values (`VALID_BACKENDS`,
   `VALID_GRADERS`, `VALID_SCORING`). Use the next unused number -- never a
   gap left by a deleted case. Example skeleton:

   ```yaml
     - id: S11
       agent: gaia-orchestrator
       task: "<the prompt a user would actually type>"
       grader:
         - routing_grader
       backend: routing_sim
       scoring: binary
       routing_expect:
         primary_agent_in:
           - <expected specialist>
         primary_agent_not:
           - gaia-orchestrator
   ```

3. **The case must be answerable by production code.**
   `test_cases_run_real_machinery` rejects any backend this suite cannot
   dispatch, and answering a case with a canned string is the failure mode
   this framework was cleaned of. There is no `live_only` escape hatch any
   more -- the lane it exempted is gone.

4. **A `routing_sim` case** runs on every invocation with no fixture required.
   Add a `routing_expect` block to the YAML (`primary_agent_in`,
   `primary_agent_not`, ... -- see S4 for the shape).

5. **Seed the baseline entry** in `results/baseline.json` under the `cases`
   map: same `id`, the scoring mode, and the score you expect the case to
   hold (`1.0` for a case that should pass). This is mandatory, not a
   first-run convenience -- `test_every_case_has_a_baseline_entry` fails
   without it, because a case outside the baseline is a case the regression
   gate can never fail.

   Seed it from a MEASURED run, not from the score you hope for. S1 and S6
   were both seeded at `1.0` by hand and never ran; when they finally did, the
   real ceiling was `0.5`. A hand-seeded `1.0` does not gate anything -- it
   just makes the first honest run look like a regression.

6. **Run the guards + the case**:

   ```
   cd /home/jorge/ws/me/gaia
   python3 -m pytest tests/evals/test_evals.py -q
   python3 -m pytest tests/evals/ -q -k S11
   ```

## How to delete a scenario

The mirror of the list above, and it has one trap.

1. **Remove the catalog entry AND its `baseline.json` entry.**
   `test_every_case_has_a_baseline_entry` and
   `test_baseline_has_no_entries_for_deleted_cases` fail from opposite sides
   until both are done -- that pair is the gate, so both must end green. Do
   not re-point a deleted id at a new case.

2. **Then grep for the id.** A unit test may pin the *content* of the
   committed baseline by naming a case, so deleting the case breaks a test
   that has nothing to do with it. There is exactly one such site today:
   `test_baseline.py::test_uses_default_baseline_path_when_none` resolves the
   real `results/baseline.json` (that resolution IS the behaviour under test)
   and so needs an id that file actually carries. Re-point it at a surviving
   case. Where possible, assert over whatever the baseline holds instead of
   naming an id -- `test_seeded_baseline_records_the_expected_passing_score`,
   in that same file, iterates whatever entries exist and asserts each scores
   `1.0`, which is why it needed no edit.

3. **Remove what existed only to serve the case.** Keep whatever is shared
   with something that still works. Deleting S1 and S6 also removed the
   `--agent` dispatch backend, the `live_only` and `permission_mode` fields,
   and the nightly workflow that ran nothing else; it did NOT remove the
   `llm` marker (owned by `tests/layer2_llm_evaluation/`) or the graders,
   which keep their own unit tests.

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
cd /home/jorge/ws/me/gaia
mv tests/evals/results/baseline.candidate.json tests/evals/results/baseline.json
```

Then commit `baseline.json`. Do not commit `baseline.candidate.json` -- it
is a transient artifact.

## Drift interpretation

`reporter.compare_to_baseline()` returns a `DriftReport` with a `DriftEntry`
per case. Rules:

`compare_to_baseline()` classifies; `enforce_no_regression()` is what fails.

- **Semantic case, `delta <= 0.10`:** within threshold. No action.
- **Semantic case, drop `> 0.10`** or **binary flip downward:**
  `regression=True`. **The run fails.** Either the agent got worse or the
  case's premise stopped being true; both need a human. Never promote a
  baseline over a regression.
- **Any upward drift:** reported, does not fail. The recorder writes
  `results/baseline.candidate.json` so promoting is one `mv`.
- **Missing baseline entry, or missing baseline file:** treated as "new", no
  drift, no failure. A case cannot live in that blind spot for long --
  `test_every_case_has_a_baseline_entry` requires the entry.

### Why the gate is asymmetric, and why the threshold is 0.10

Three rot events -- a brief that closed, a filesystem path that was retired,
a contract field that was renamed -- each passed through this reporter while
it printed `has_drift=True` and returned 0. Drift that nobody must act on is
a log line. So one direction now fails.

**Only regressions fail.** Every one of those three events showed up as a
score going *down* once the case was honestly graded, so the downward half is
where the protection is needed. Failing on improvements too would go red on
good news, and a gate that cries on good news gets switched off -- at which
point it protects nothing at all. Improvements are instead made cheap to
absorb: the candidate baseline is written for you.

**The threshold is 0.10 and it is not a noise knob.** Eval scores here are
quantized. `code_grader` returns `matched/total` over at most two keyword
groups; every other grader returns exactly `0.0` or `1.0`; a case's score is
the mean over its graders. The smallest change any run can produce is `0.25`,
and the common ones are `0.5` and `1.0`. **No possible run lands a delta
inside `(0, 0.10]`**, so the gate cannot fire on jitter -- there is nothing
down there to fire on. Meanwhile a real half-failure sits five times above
it. Raising the threshold toward `0.5` would begin hiding genuine
half-failures; lowering it changes nothing observable. It is set where it is
because nothing real lives underneath it.

**What this gate cannot catch, stated plainly.** A case whose oracle is
*inverted* -- S9 forbade `status: closed` for a brief that was closed --
scores 1.0 before and after the world changed under it. The score never
moves, so no threshold in any direction would have caught it. That class of
rot is caught by reading the case against reality (`gaia-audit`), not by
diffing scores. The gate defends against behaviour changing; it does not
defend against a case that was wrong the day it was written.

## Module contracts

The four core modules are consumed as stable APIs by `test_evals.py` and the
per-module unit tests. Signatures worth knowing:

- `runner.dispatch(agent_type, task, timeout=60, *, backend) -> DispatchResult`
  Returns `(stdout, session_path, audit_paths, exit_code)`. Raises
  `EvalError` on timeout, missing fixture, or unknown agent. `backend` is
  REQUIRED and keyword-only -- there is deliberately no default.
- `runner.DispatchBackend` -- protocol every backend satisfies. Three
  implementations ship: `RoutingSimBackend`, `HookLogReplayBackend`,
  `FakeBackend` (unit tests only, never a catalog case).
- `graders.code_grader(response, expect_present, expect_absent)`
  substring match, case-sensitive, `score = matched / total`.
- `graders.decision_grader(response, expected_decision)` parses the
  `hook_log_replay` decision payload and compares the observed
  allow/ask/deny to the catalog's curated oracle.
- `graders.tool_trace_grader(session_path, audit_paths, trace_expect)`
  walks transcript + audit slices. Supports `must_contain`,
  `must_not_contain`, `at_most`, `ordering`, `delegated_to`. Reuses
  `tools/gaia_simulator/extractor.py` for audit JSONL parsing.
  `at_most` (`[{tool, command_matches, count}]`) is the "did not retry"
  operator: `must_not_contain` cannot express it, because an agent only
  discovers a blocked command by attempting it once, so forbidding the call
  outright fails the compliant agent for the attempt that produced the block.
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
  classifies drift and its direction; never raises. `DriftReport` carries
  `has_drift`, `has_regression`, and `regressions()`.
- `reporter.enforce_no_regression(report) -> None` raises
  `BaselineRegression` (an `AssertionError`) naming every case that scored
  below baseline. This is the gate; the suite calls it at session teardown.
- `reporter.write_baseline_candidate(new_results, path=None) -> Path`
  never overwrites `baseline.json`.
- `catalog.CaseModel` no longer carries `live_only` or `permission_mode`. Both
  existed solely for the `--agent` dispatch lane: `live_only` gated it out of
  the default run and `permission_mode` overrode the CLI's `acceptEdits`
  default so a case could observe a refusal. With the lane gone they had no
  consumer left, so they were removed rather than left as fields nothing
  reads.

## Gaps vs v1

The v1 plan had five blind spots. Four are closed; G1 is REOPENED, and by a
better understanding of the problem than the one that closed it:

| Gap | Symptom | Status                                                                                        |
| --- | ------- | --------------------------------------------------------------------------------------------- |
| G1  | Single-turn `claude --print` cannot observe multi-turn protocol events | **REOPENED.** `SubprocessBackend` "closed" it by dispatching `--agent <specialist>` -- a session that observes no protocol events worth having, because it is not the route Gaia dispatches through. Closing it for real needs an orchestrator-rooted session. |
| G2  | Only keyword matching; contract shape invisible                         | **REOPENED.** `graders.contract_grader` closed it by parsing a fenced `agent_contract_handoff` block out of a captured response. It was removed once the CLI became the canonical contract channel: no backend here dispatches an agent turn, so nothing produced such a block and the grader had no subject. Closing it for real needs a backend that runs an agent and a grader that reads the persisted contract row. |
| G3  | No way to check Read-before-Edit, no pipes, Agent delegated             | `graders.tool_trace_grader` (reuses `tools/gaia_simulator/extractor.py`) |
| G4  | S4 dispatching a real orchestrator wastes tokens                        | `runner.RoutingSimBackend` (sync, free)                 |
| G5  | Re-detecting "refused pipe" drifts from skill_injection_verifier        | `graders.skill_injection_consumer` (reads verifier anomalies) |

## Out of scope

- LLM-as-judge / model-based graders -- separate brief.
- Latency / performance benchmarking.
- External framework adoption (DeepEval, promptfoo).
- Auto-promotion of baselines. A candidate is written for you on an
  improvement; the `mv` stays manual by design, because a baseline encodes
  intent and a human has to sign off on a change of intent.

Two former entries no longer belong here:

- **"Additional catalogs beyond `context_consumption.yaml`"** -- there is a
  second catalog and there has been for a while.
  `catalogs/security_decisions.yaml` pairs each `(tool, command)` with a
  human-curated `expected_decision` and replays it through the real
  PreToolUse hook (`runner.HookLogReplayBackend` + `graders.decision_grader`,
  driven by `test_security_golden.py`). It is deterministic, free, and runs
  on every invocation.
- **"CI integration / gating -- local on-demand only"** -- this suite now
  gates, in full. The routing case, the golden security catalog, the
  skill-injection reality check and every unit test run in the ordinary
  `pytest tests/evals/` pass (in CI via `ci.yml`'s `test-python` job) and fail
  the build, and a score below baseline fails it too. Nothing here is
  on-demand any more and nothing here needs an API key.

One entry belongs here that did not before:

- **An orchestrator-rooted end-to-end case.** Prompt in; routing, dispatch and
  contract observed on the way out. It is the thing this catalog cannot do
  today and the only way G1 closes honestly. It is out of scope as work, not
  as an idea -- it needs a backend that starts a session the way `gaia install`
  configures one, which is a different object from the `--agent` dispatcher
  that was removed.

## See also

- `tools/gaia_simulator/extractor.py` -- audit JSONL parser reused by
  `tool_trace_grader` and `skill_injection_consumer`.
- `tools/gaia_simulator/routing_simulator.py` -- synchronous surface
  classifier backing `RoutingSimBackend`.
- `tools/gaia_simulator/runner.py` -- `HookRunner`, the subprocess driver
  behind `HookLogReplayBackend`.
- `hooks/modules/agents/skill_injection_verifier.py` -- the production
  skill/artifact detector, exercised directly against real transcripts by
  `test_skill_injection_dispatch_reality.py`.
- `hooks/modules/context/context_injector.py` -- `build_project_context()`,
  whose workspace-isolation behaviour is measured by
  `tests/hooks/modules/context/test_context_injector.py`.
- `tests/conftest.py` -- registers the `llm` / `e2e` markers and auto-skips
  them unless opted in with `-m llm`.
- `skills/agent-protocol/SKILL.md` -- the contract protocol the eval
  catalogs describe when a case exercises an agent turn.
