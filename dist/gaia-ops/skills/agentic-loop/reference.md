# Agentic Loop -- Reference

Exact commands, schemas, templates, and a walkthrough. Read on-demand when executing the loop.

## Setup Phase Commands

```bash
# 1. Read files_in_scope (use Read tool, not cat)

# 2. Create branch
git checkout -b {branch}

# 3. Run eval and capture baseline
{eval_command}
# Look for line matching: METRIC {name}={number}

# 4. Write state.json (use Write tool)
# 5. Write worklog.md (use Write tool)

# 6. Commit baseline
git add state.json worklog.md
git commit -m "baseline: {metric} {value}"
```

## Eval Phase: Parsing the Metric

The eval_command must print a line matching this pattern to stdout:

```
METRIC accuracy=89.0
METRIC pass_rate=72
METRIC error_count=15
```

Format: `METRIC {name}={number}` -- one space after METRIC, no spaces around `=`.

Parse the number as a float. If the line is missing, the eval failed -- treat as a discard and log the error.

## Keep Phase Commands

```bash
git add -A
git commit -m "$(cat <<'EOF'
improve: {metric} {old}->{new}

{one-line description of what changed}
EOF
)"
```

## Discard Phase Commands

```bash
git checkout -- .
git clean -fd
```

Use `-fd`, never `-fdx`. The `-x` flag removes files matching `.gitignore` patterns, which can destroy config files, build caches, or virtual environments needed by the eval command.

## state.json Schema

```json
{
  "goal": "Improve test accuracy to 98%",
  "eval_command": "python run_tests.py --report",
  "metric": "accuracy",
  "direction": "higher",
  "threshold": 98,
  "baseline": 89.0,
  "current": 94.5,
  "best": 94.5,
  "iteration": 5,
  "max_iterations": 20,
  "consecutive_discards": 0,
  "pivot_count": 0,
  "status": "iterating",
  "branch": "improve/accuracy-apr13",
  "history": [
    {
      "iteration": 1,
      "metric_value": 89.0,
      "decision": "baseline",
      "description": "Initial measurement"
    },
    {
      "iteration": 2,
      "metric_value": 91.2,
      "decision": "keep",
      "description": "Normalize input features"
    },
    {
      "iteration": 3,
      "metric_value": 90.1,
      "decision": "discard",
      "description": "Add dropout layer -- hurt accuracy"
    }
  ],
  "timestamp": "2026-04-13T20:30:00Z"
}
```

### Field descriptions

| Field | Type | Description |
|-------|------|-------------|
| `goal` | string | Human-readable objective from orchestrator |
| `eval_command` | string | Exact command to run for evaluation |
| `metric` | string | Name of the metric to parse from eval output |
| `direction` | string | `"higher"` or `"lower"` -- which direction is better |
| `threshold` | number | Target value -- loop stops when reached |
| `baseline` | number | First measurement, never changes |
| `current` | number | Most recent measurement |
| `best` | number | Best value seen across all iterations |
| `iteration` | integer | Current iteration count |
| `max_iterations` | integer | Hard stop limit |
| `consecutive_discards` | integer | Reset to 0 on every keep |
| `pivot_count` | integer | Number of strategy pivots so far |
| `status` | string | One of: `iterating`, `threshold_reached`, `max_iterations`, `stopped` |
| `branch` | string | Git branch name |
| `history` | array | Record of every iteration |
| `timestamp` | string | ISO 8601 timestamp of last update |

### Metric comparison logic

```
if direction == "higher":
    improved = (new_value > current)
    threshold_reached = (new_value >= threshold)
elif direction == "lower":
    improved = (new_value < current)
    threshold_reached = (new_value <= threshold)

# Special case: equal metric with fewer lines of code = improved
if new_value == current and lines_removed > 0:
    improved = true
```

## worklog.md Template

```markdown
# Worklog: {goal}

Branch: {branch}
Metric: {metric} ({direction} is better)
Threshold: {threshold}
Baseline: {baseline}

## What's Been Tried

(Updated every 10 iterations -- summary of strategies attempted)

---

### Run 1 (baseline): Initial measurement
- **Metric:** {metric}={value}
- **Decision:** BASELINE

### Run 2: Normalize input features
- **Metric:** accuracy=91.2 (was 89.0)
- **Decision:** KEEP
- **Insight:** Feature scaling was the low-hanging fruit
- **Next:** Try feature selection to reduce noise

### Run 3: Add dropout layer (rate=0.3)
- **Metric:** accuracy=90.1 (was 91.2)
- **Decision:** DISCARD
- **Insight:** Dropout hurts on this small dataset -- underfitting
- **Next:** Try regularization via weight decay instead

---

## Summary
(Written at termination)

Final: {metric} {baseline} -> {final} in {N} iterations
Keeps: X | Discards: Y | Pivots: Z
Key insight: ...
```

## continue.md Template

Written when the loop is paused or interrupted, so the next session can resume.

```markdown
# Continue: {goal}

## State
- Branch: {branch}
- Iteration: {N} of {max_iterations}
- Current: {metric}={value} (baseline={baseline}, best={best})
- Status: {status}

## Last Action
{what was done in the last iteration and its result}

## Next Hypothesis
{what to try next, based on worklog insights}

## Resume Steps
1. Read state.json to restore loop state
2. Read worklog.md for context on what's been tried
3. Continue from iteration {N+1}
```

## Contract Integration

Every response during the loop must include `loop_status` inside `agent_status`:

```json
{
  "agent_status": {
    "plan_status": "IN_PROGRESS",
    "agent_id": "a1b2c3",
    "pending_steps": ["continue iterating"],
    "next_action": "iteration 6",
    "loop_status": {
      "iteration": 5,
      "metric": 94.5,
      "best": 94.5,
      "baseline": 89.0,
      "threshold": 98,
      "status": "iterating"
    }
  },
  "evidence_report": {
    "patterns_checked": [],
    "files_checked": ["state.json"],
    "commands_run": ["{eval_command}"],
    "key_outputs": ["accuracy improved 93.2->94.5 (KEEP)"],
    "verbatim_outputs": ["METRIC accuracy=94.5"],
    "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": null
  },
  "consolidation_report": null,
  "approval_request": null
}
```

On loop completion, set `plan_status: "COMPLETE"` with verification:

```json
{
  "agent_status": {
    "plan_status": "COMPLETE",
    "loop_status": {
      "iteration": 12,
      "metric": 98.1,
      "best": 98.1,
      "baseline": 89.0,
      "threshold": 98,
      "status": "threshold_reached"
    }
  },
  "evidence_report": {
    "verification": {
      "method": "metric",
      "checks": ["accuracy >= 98 threshold"],
      "result": "pass",
      "details": "accuracy=98.1 (baseline=89.0) achieved in 12 iterations"
    }
  }
}
```

## Escalation Logic

```
consecutive_discards >= 3 -> REFINE
    Log: "REFINE: 3 consecutive discards. Adjusting within current strategy."
    Action: Same general approach, different parameters or targets

consecutive_discards >= 5 -> PIVOT
    Log: "PIVOT #{N}: 5 consecutive discards. Changing strategy entirely."
    Action: Structurally different approach. Reset consecutive_discards to 0.
    Increment pivot_count.

pivot_count >= 3 (without any keep since last reset) -> STOP
    Log: "STOP: 3 pivots without improvement. Reporting blockers."
    Action: Set status="stopped". Write summary. Return COMPLETE with
    verification.result="fail" and details explaining what was tried.
```

## Resume Logic

When starting an agentic-loop, check for existing state before beginning fresh:

### Decision Tree

1. **`continue.md` exists** → Read it, delete it, resume from the checkpoint described. The continue.md was written during context compaction or interruption.

2. **`state.json` exists AND timestamp < 24h** → Resume from saved state. Read iteration count, best metric, worklog. Continue the loop from where it left off.

3. **`state.json` exists AND timestamp > 24h** → Stale session. Archive: rename `state.json` → `state.json.prev`, `worklog.md` → `worklog.md.prev`. Start fresh.

4. **Neither exists** → Fresh start. Run setup phase from scratch.

### Resume Checklist

When resuming (cases 1 or 2):
- Read `state.json` for: iteration, current metric, best metric, threshold, consecutive_discards, pivot_count
- Read `worklog.md` for: what was tried, insights, what to try next
- Read `continue.md` (if exists) for: exact next action, decisions made, remaining steps
- Verify branch exists: `git branch --list {branch}`
- Run eval_command to confirm current metric matches state
- If metric diverged from state → update state, log discrepancy, continue

### Supporting Hooks

These hooks assist with resume:
- **UserPromptSubmit**: Detects active loop (state.json present, not stale) and injects "you are in agentic-loop mode" context on every turn
- **PreCompact**: Before context compaction, injects "write continue.md and update state.json NOW"
- **Protocol Fingerprint Check**: Every 10 iterations, verify you remember the loop rules

## Example Session Walkthrough

**Scenario:** Orchestrator asks to improve test pass rate from ~70% to 95%.

**Parameters received:**
- goal: "Improve test pass rate to 95%"
- eval_command: "pytest tests/ --tb=short 2>&1 | python parse_results.py"
- metric: "pass_rate"
- direction: "higher"
- threshold: 95
- max_iterations: 30
- files_in_scope: ["src/parser.py", "src/validator.py", "tests/"]
- branch: "improve/pass-rate-apr13"

**Setup:**
1. Read `src/parser.py`, `src/validator.py`, all test files
2. `git checkout -b improve/pass-rate-apr13`
3. Run eval: `pytest tests/ --tb=short 2>&1 | python parse_results.py`
   Output includes: `METRIC pass_rate=68.5`
4. Write state.json (baseline=68.5), write worklog.md
5. `git commit -m "baseline: pass_rate 68.5"`

**Iteration 1:**
- HYPOTHESIZE: Failing tests show TypeError on None input -- add null guard
- EDIT: Add `if value is None: return default` in `parser.py`
- EVALUATE: `METRIC pass_rate=74.2`
- DECIDE: 74.2 > 68.5 -> KEEP
- LOG: "Run 1: Add null guard in parser.py -- pass_rate=74.2 (KEEP)"
- Commit: `"improve: pass_rate 68.5->74.2"`

**Iteration 2:**
- HYPOTHESIZE: Remaining failures are in validator edge cases
- EDIT: Fix off-by-one in range check
- EVALUATE: `METRIC pass_rate=71.0`
- DECIDE: 71.0 < 74.2 -> DISCARD
- LOG: "Run 2: Fix off-by-one in validator range check -- pass_rate=71.0 (DISCARD)"
- `git checkout -- . && git clean -fd`

**Iteration 3:**
- HYPOTHESIZE: The range check fix broke other paths. Try fixing just the boundary condition.
- EDIT: Change `<` to `<=` in validator boundary
- EVALUATE: `METRIC pass_rate=79.8`
- DECIDE: 79.8 > 74.2 -> KEEP
- Commit: `"improve: pass_rate 74.2->79.8"`

... (continues until pass_rate >= 95 or 30 iterations)

**Termination (threshold reached at iteration 18):**
- `git commit -m "final: pass_rate 68.5->95.3 in 18 iterations"`
- Write final state.json (status: "threshold_reached")
- Write summary in worklog.md
- Return `plan_status: "COMPLETE"` with verification passing

## Protocol Fingerprint Check (every 10 iterations)

At iterations 10, 20, 30, etc., pause and:

1. Re-read all `files_in_scope` -- the code has changed since setup
2. Review the "What's Been Tried" section in worklog.md
3. Update "What's Been Tried" with a summary of strategies so far
4. Check: are you repeating approaches? Are there untried angles?
5. Log the check in worklog.md as a separate entry

This prevents tunnel vision. After 10 iterations of micro-optimizations, re-reading the source fresh often reveals a structural improvement that the incremental mindset missed.
