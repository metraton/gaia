# Success Metrics

Actionable metrics collection system for measuring workflow effectiveness and agent performance.

## What This Does

Collects, stores, and analyzes metrics at each workflow phase to measure system effectiveness with concrete, measurable data.

## Where This Fits

```
Phase Execution → **Metrics Collection** → Metrics Store → Dashboard/Analysis
```

Runs transparently during workflow execution to capture:
- Routing accuracy
- Delegation decisions
- Guard pass/fail rates
- Phase completion times
- Agent invocation success rates

## Components

| File | Purpose |
|------|---------|
| `metrics_collector.py` | Core metrics collection engine |
| `metrics_dashboard.py` | Metrics visualization and reporting |

## Usage

### Collect Metrics

```bash
# Record routing decision
python3 metrics_collector.py record routing \
  --request "Show GKE clusters" \
  --agent "cloud-troubleshooter" \
  --confidence 0.95

# Record phase completion
python3 metrics_collector.py record phase \
  --phase "context_loading" \
  --duration 234 \
  --status "success"
```

### View Dashboard

```bash
# Show last 7 days metrics
python3 metrics_dashboard.py --days 7

# Output:
# Routing Accuracy: 94% (47/50 correct)
# Avg Phase Duration: 1.2s
# T3 Approval Rate: 100% (12/12)
```

## Metric Types

| Type | What It Measures | Target |
|------|-----------------|--------|
| Routing Decision | Agent selection accuracy | >90% |
| Delegation Decision | Local vs. delegate correctness | >85% |
| Guard Execution | Pass rate by phase | >95% |
| Phase Completion | Duration per phase | <2s avg |
| Approval Gate | T3 approval rate | 100% |
| Agent Invocation | Agent task success rate | >80% |

## Key Metrics

### Routing Accuracy

Percentage of times the router selected the correct agent for a task.

```
Routing Accuracy = (Correct Selections / Total Selections) × 100
Target: >90%
```

### Phase Duration

Average time to complete each workflow phase.

```
Avg Duration = Σ(phase durations) / count
Target: <2s for Phase 0-3, <5s for Phase 4-6
```

### T3 Approval Rate

Percentage of T3 operations that received approval.

```
T3 Approval Rate = (Approved T3 / Requested T3) × 100
Target: 100% (all T3 operations must be approved)
```

## Data Storage

Metrics stored in `.claude/logs/metrics/`:

```
.claude/logs/metrics/
├── routing.jsonl          # Routing decisions
├── delegation.jsonl       # Delegation decisions
├── guards.jsonl           # Guard executions
├── phases.jsonl           # Phase completions
└── agents.jsonl           # Agent invocations
```

## Examples

### Routing Metric

```json
{
  "timestamp": "2026-01-08T17:45:23Z",
  "user_request": "Show GKE clusters",
  "selected_agent": "cloud-troubleshooter",
  "routing_confidence": 0.95,
  "routing_method": "semantic",
  "correct": true
}
```

### Phase Completion Metric

```json
{
  "timestamp": "2026-01-08T17:45:25Z",
  "phase": "context_loading",
  "duration_ms": 234,
  "status": "success"
}
```

## Dashboard Output Example

```
=== Gaia-Ops Metrics Dashboard ===
Period: Last 7 days

Routing Performance:
  Accuracy: 94% (47/50)
  Avg Confidence: 0.89
  Method Distribution:
    - Semantic: 32 (64%)
    - Task Metadata: 14 (28%)
    - Keyword: 4 (8%)

Phase Performance:
  Phase 0 (Clarification): 0.3s avg
  Phase 1 (Routing): 0.8s avg
  Phase 2 (Context): 1.2s avg
  Phase 3 (Planning): 2.1s avg
  Phase 4 (Approval): 0.5s avg
  Phase 5 (Execution): 4.2s avg
  Phase 6 (SSOT Update): 0.6s avg

Agent Performance:
  terraform-architect: 92% success (11/12)
  gitops-operator: 88% success (7/8)
  cloud-troubleshooter: 95% success (19/20)

Guard Performance:
  Overall Pass Rate: 98% (196/200)
  Failed Guards:
    - Phase 3 Planning: 3 failures
    - Phase 4 Approval: 1 failure
```

---

**Phase:** Post-execution | **Type:** Observability | **Targets:** See `config/metrics_targets.json`
