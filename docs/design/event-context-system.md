# Event Context System -- Design Document

**Status:** DESIGN
**Date:** 2026-03-25
**Author:** gaia-system (meta-agent)

---

## 1. Problem Statement

GAIA agents operate in isolated sessions. No agent knows what happened before it
ran. The orchestrator dispatches agents but has no memory of prior outcomes.
Recurring tasks (check email, monitor drift) have no mechanism to schedule or
track themselves. The result: agents re-discover context that previous agents
already found, and no scheduled automation exists.

**Goal:** A lightweight event log that records what happened, when, and by whom
-- readable by agents, writable by hooks and external triggers, stored as plain
files, and agnostic to Claude Code.

---

## 2. Current Systems Analysis

### 2.1 Session Events (session_context_writer.py + session_event_injector.py)

**What it does:**
- PostToolUse hook detects git commits and pushes via `event_detector.py`
- `SessionContextWriter` appends events to `.claude/session/active/context.json`
- `session_event_injector` reads events, filters by agent domain, injects into
  `additionalContext` during SubagentStart
- 24-hour retention policy with file locking

**What it tracks:**
- `git_commit` (hash, message, command)
- `git_push` (branch, command)
- `file_modifications` (count) -- detected but writer not wired
- `infrastructure_change` -- in filter map but no detector exists

**Storage format:** Single JSON file with `critical_events` array.

**Limitations:**
- Session-scoped: dies when session ends
- Only 4 event types, only 2 actually detected
- No external writers (cron, loops)
- No cross-session persistence

### 2.2 Episodic Memory (episode_writer.py + episodic.py)

**What it does:**
- SubagentStop hook captures completed agent workflows as "episodes"
- Stores individual JSON files + JSONL index under
  `.claude/project-context/episodic-memory/`
- Enriches episodes with session events, anomalies, anchor hits
- Supports outcome tracking (success/partial/failed) and relationships

**What it captures:**
- Complete workflow metadata: agent, task, duration, commands, plan_status
- Session events (git commits, pushes, file mods, speckit milestones)
- Anomalies detected during workflow audit
- Context anchor hit rates

**Relationship to Event Context:** Episodic memory is a higher-level
abstraction. One episode = one agent workflow. Event Context is lower-level:
one event = one discrete thing that happened.

### 2.3 Workflow Recorder (workflow_recorder.py)

**What it does:**
- Captures per-workflow metrics to `run-snapshots.jsonl` and `metrics.jsonl`
- Records agent skill snapshots to `agent-skills.jsonl`
- Telemetry focus: tokens, duration, compliance scores

**Relationship to Event Context:** Workflow recorder is metrics/telemetry.
Event Context is operational log. Different purposes, no overlap.

### 2.4 Audit Logger (logger.py)

**What it does:**
- Logs every tool execution to daily audit files
- Tracks: tool name, parameters, success/failure, duration

**Relationship to Event Context:** Audit logger is low-level tool execution
trace. Event Context is high-level operational events. Different granularity.

---

## 3. Event Context Schema

### 3.1 JSONL Record Format

```jsonl
{"ts":"2026-03-25T14:30:00Z","type":"agent.complete","source":"hook:subagent_stop","agent":"terraform-architect","result":"COMPLETE","summary":"Applied 12 resources to staging","meta":{"plan_status":"COMPLETE","tier":"T3","episode_id":"ep_abc123"}}
{"ts":"2026-03-25T14:35:00Z","type":"trigger.scheduled","source":"cron:drift-check","agent":"","result":"fired","summary":"Drift check cron triggered","meta":{"schedule":"0 9 * * *","trigger_id":"tr_001"}}
{"ts":"2026-03-25T14:36:00Z","type":"git.commit","source":"hook:post_tool_use","agent":"developer","result":"ok","summary":"fix: resolve login redirect loop","meta":{"hash":"a1b2c3d","branch":"fix/login"}}
```

### 3.2 Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `ts` | ISO 8601 string | yes | When the event occurred (UTC) |
| `type` | dotted string | yes | Event category (see 3.3) |
| `source` | string | yes | Who wrote the event (see 3.4) |
| `agent` | string | no | Agent involved, empty for non-agent events |
| `result` | string | yes | Outcome: ok, error, fired, COMPLETE, BLOCKED, etc. |
| `summary` | string | yes | Human-readable one-line description |
| `meta` | object | no | Type-specific structured data |

### 3.3 Event Types (Hierarchical)

```
agent.start          -- Agent dispatched by orchestrator
agent.complete       -- Agent finished (any plan_status)
agent.blocked        -- Agent returned BLOCKED or NEEDS_INPUT

git.commit           -- Successful git commit detected
git.push             -- Successful git push detected

trigger.scheduled    -- Cron/loop/schedule fired a task
trigger.manual       -- User said "remember this" or "run X"

system.session_start -- New GAIA session began
system.session_end   -- Session ended (stop hook)
system.anomaly       -- Workflow auditor detected anomaly

context.updated      -- project-context.json was enriched by an agent
context.stale        -- Scanner detected staleness

infra.change         -- Infrastructure mutation detected
infra.drift          -- Drift detected between desired and actual state
```

### 3.4 Source Conventions

Sources use a `namespace:identifier` format:

| Source | Example |
|--------|---------|
| Hook-generated | `hook:post_tool_use`, `hook:subagent_stop`, `hook:stop` |
| Cron-generated | `cron:drift-check`, `cron:email-scan` |
| Loop-generated | `loop:monitor-30m` |
| Agent self-report | `agent:terraform-architect` |
| CLI/manual | `cli:user`, `cli:gaia-events` |
| System | `system:scanner`, `system:session` |

---

## 4. Architecture

### 4.1 Data Flow

```
WRITERS                          STORAGE                    READERS
=======                          =======                    =======

hook:subagent_stop ──┐                                 ┌── Context injection
hook:post_tool_use ──┤                                 │   (SubagentStart hook)
hook:stop ───────────┤     .claude/events/             │
                     ├────> events.jsonl ──────────────┼── CLI: gaia-events
cron:* ──────────────┤     (append-only, JSONL)        │   (list, search, tail)
loop:* ──────────────┤                                 │
                     │                                 ├── Heartbeat loop
cli:user ────────────┤                                 │   (reads to decide action)
agent:* ─────────────┘                                 │
                                                       └── Episodic memory
                                                           (enrichment at write time)
```

### 4.2 Storage Location

```
.claude/
  events/
    events.jsonl          -- Primary event log (append-only)
    events.jsonl.lock     -- File lock for concurrent writes
```

**Why `.claude/events/` and not inside session or project-context:**

1. Events span sessions -- they must survive session end
2. Events are operational, not project knowledge -- separate from project-context
3. JSONL is append-only, simple, and greppable -- no need for JSON structure
4. Under `.claude/` so it is included in plugin data scope

**Retention:** 7-day rolling window. A daily pruning pass removes events older
than 7 days. Configurable via `GAIA_EVENT_RETENTION_DAYS` env var.

### 4.3 File Location Resolution

Uses the existing `get_plugin_data_dir()` from `hooks/modules/core/paths.py`:

```python
def get_events_dir() -> Path:
    events_dir = get_plugin_data_dir() / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    return events_dir
```

This follows the exact pattern of `get_logs_dir()`, `get_metrics_dir()`,
`get_memory_dir()`, and `get_session_dir()` already in `paths.py`.

---

## 5. Writers

### 5.1 Hook Writers (Automatic)

**SubagentStop hook** -- writes `agent.complete` or `agent.blocked`:

```python
# In adapt_subagent_stop(), after episode capture:
event_writer.append({
    "type": "agent.complete",
    "source": "hook:subagent_stop",
    "agent": agent_type,
    "result": plan_status,
    "summary": task_description[:120],
    "meta": {
        "plan_status": plan_status,
        "tier": tier,
        "episode_id": episode_id,
        "task_id": task_id,
        "anomalies": anomaly_count,
    }
})
```

**PostToolUse hook** -- writes `git.commit`, `git.push`:

```python
# In adapt_post_tool_use(), alongside SessionContextWriter:
for evt in critical_events:
    event_writer.append({
        "type": f"git.{evt.event_type.value.replace('git_', '')}",
        "source": "hook:post_tool_use",
        "agent": current_agent or "",
        "result": "ok",
        "summary": evt.data.get("commit_message", evt.data.get("branch", "")),
        "meta": evt.data,
    })
```

**Stop hook** -- writes `system.session_end`:

```python
event_writer.append({
    "type": "system.session_end",
    "source": "hook:stop",
    "result": stop_reason,
    "summary": f"Session ended: {stop_reason}",
})
```

### 5.2 External Writers (Cron/Loop)

External triggers write events by calling a small CLI wrapper:

```bash
# Cron job writes its trigger event
gaia-events write \
  --type trigger.scheduled \
  --source "cron:drift-check" \
  --summary "Drift check cron triggered"

# Then runs the actual task
claude -p "check for infrastructure drift" --output-format json

# The agent's SubagentStop hook writes agent.complete automatically
```

For Claude `/loop` or `/schedule`:

```bash
# Loop iteration writes trigger event
gaia-events write --type trigger.scheduled --source "loop:monitor-30m" --summary "30m monitor loop"
```

### 5.3 Manual Writers

User can write events directly:

```bash
gaia-events write --type trigger.manual --source cli:user --summary "remember: DNS cutover at 3pm"
```

---

## 6. Readers

### 6.1 Context Injection (Primary Consumer)

The existing `session_event_injector.py` pattern is reused. During
SubagentStart, the hook reads the last N events from `events.jsonl`, filters
by agent domain, and appends to `additionalContext`.

```python
def build_event_context(agent_type: str, max_events: int = 15) -> str | None:
    """Read events.jsonl, filter by agent domain, format for injection."""
    events_path = get_events_dir() / "events.jsonl"
    if not events_path.exists():
        return None

    # Read last 100 lines (cheap: seek from end)
    raw_events = _tail_jsonl(events_path, 100)

    # Filter by agent relevance
    filtered = _filter_for_agent(raw_events, agent_type)

    if not filtered:
        return None

    # Format as markdown for injection
    lines = ["# Event Context (Last 24h)"]
    for evt in filtered[-max_events:]:
        ts = evt["ts"][:16]
        lines.append(f"- [{ts}] {evt['type']}: {evt['summary']}")

    return "\n".join(lines)
```

**Agent filtering** extends the existing `AGENT_EVENT_FILTERS` map:

```python
AGENT_EVENT_FILTERS = {
    "terraform-architect": ["agent.complete", "git.*", "infra.*", "context.*", "trigger.*"],
    "gitops-operator":     ["agent.complete", "git.*", "infra.*", "trigger.*"],
    "developer":    ["agent.complete", "git.*", "context.*"],
    "cloud-troubleshooter": "*",
    "gaia-system":          "*",
}
```

### 6.2 CLI (gaia-events)

A lightweight CLI for humans and scripts:

```bash
gaia-events list                      # Last 20 events
gaia-events list --last 24h           # Events in last 24 hours
gaia-events list --type agent.*       # Filter by type glob
gaia-events list --agent terraform-*  # Filter by agent
gaia-events tail                      # Follow new events (like tail -f)
gaia-events write --type ... --source ... --summary ...
gaia-events prune                     # Manual retention cleanup
gaia-events stats                     # Event counts by type, last 24h/7d
```

Implementation: Python script in `gaia-ops/bin/gaia-events` or
`gaia-ops/commands/gaia-events.py`.

### 6.3 Heartbeat / Decision Loop

A heartbeat process reads events to decide if action is needed:

```bash
#!/bin/bash
# Example: check if drift-check has run in the last 6 hours
last_drift=$(gaia-events list --type trigger.scheduled --source "cron:drift-check" --last 6h --count)
if [ "$last_drift" -eq 0 ]; then
    claude -p "check for infrastructure drift" --output-format json
fi
```

This pattern is agnostic -- works with bash, cron, systemd timers, or any
scheduler. The event log is the source of truth for "when did X last happen."

---

## 7. Relationship to Existing Systems

### 7.1 Session Events: MERGE

Session events (`session_context_writer.py` + `session_event_injector.py`)
should be **absorbed into Event Context** over time.

**Migration path:**

1. **Phase 1 (parallel):** Event Context writes alongside session events.
   Both systems active. Session event injector continues working.
2. **Phase 2 (switchover):** Event Context injection replaces session event
   injection. Session events module deprecated.
3. **Phase 3 (cleanup):** Remove session_context_writer.py and
   session_event_injector.py. Remove `.claude/session/active/context.json`.

**Why merge:** Session events is a narrower version of Event Context that
stores the same information (git commits, pushes) in a different format
(JSON array in a single file vs JSONL). No reason to maintain two systems.

### 7.2 Episodic Memory: KEEP SEPARATE, ENRICH

Episodic memory captures a **complete workflow episode** -- one record per
agent execution. Event Context captures **discrete events** -- many records
per agent execution. Different abstractions, both needed.

**Integration point:** `episode_writer.py` already calls `get_session_events()`
to enrich episodes. After migration, it reads from Event Context instead:

```python
# Instead of: session_events = get_session_events()
# Use:        recent_events = event_reader.get_recent(hours=1, types=["git.*", "infra.*"])
```

### 7.3 Workflow Recorder: KEEP SEPARATE

Workflow recorder is telemetry (tokens, duration, compliance). Event Context
is operational log (what happened). No overlap, no merge needed.

### 7.4 Audit Logger: KEEP SEPARATE

Audit logger records every tool execution at the finest granularity. Event
Context records significant operational events. Different granularity,
different consumers.

---

## 8. Trigger Mechanisms (Agnostic)

### 8.1 Linux Cron

```cron
# Check email every morning at 9am
0 9 * * * cd /path/to/project && gaia-events write --type trigger.scheduled --source "cron:email-check" --summary "Daily email check" && claude -p "check email for action items" --output-format json

# Monitor drift every 6 hours
0 */6 * * * cd /path/to/project && gaia-events write --type trigger.scheduled --source "cron:drift-check" --summary "6h drift check" && claude -p "check for infrastructure drift" --output-format json
```

### 8.2 Claude /loop

```
/loop 30m check infrastructure drift and report changes
```

The agent's SubagentStop hook writes `agent.complete` automatically.
If the loop wrapper writes a `trigger.scheduled` event before each iteration,
the heartbeat pattern works.

### 8.3 Claude /schedule (Cloud-Based)

Same pattern as cron -- the cloud scheduler calls a webhook that:
1. Writes a `trigger.scheduled` event
2. Invokes the Claude task
3. The hook lifecycle handles the rest

### 8.4 Event Feedback Loop

Every trigger mechanism follows the same contract:

```
1. External trigger writes trigger.scheduled event
2. Claude agent runs
3. SubagentStop hook writes agent.complete event
4. Next trigger can read both events to decide next action
```

---

## 9. Implementation: Pure Infrastructure

### 9.1 Recommendation: Infrastructure, Not Skill or Agent

**Event Context should be infrastructure** -- built into the hook system,
not a skill or an agent.

**Why not a skill?**
- Skills teach process to agents. Event Context is plumbing that agents
  consume passively via context injection. Agents do not need to know
  how events work.

**Why not an agent?**
- No agent needs to "manage events." Events are written by hooks and
  external triggers. Events are read by the injection system. There is
  no decision-making step that requires an agent.

**What it is:**
- A new module in `hooks/modules/events/`
- A new path function in `hooks/modules/core/paths.py`
- Writer calls in existing hook adapters
- A replacement injector in the SubagentStart flow
- A CLI script in `gaia-ops/bin/` or `gaia-ops/commands/`

### 9.2 Component Inventory

| Component | Location | New/Modify |
|-----------|----------|------------|
| `EventWriter` class | `hooks/modules/events/event_writer.py` | NEW |
| `EventReader` class | `hooks/modules/events/event_reader.py` | NEW |
| `EventContextInjector` | `hooks/modules/events/event_context_injector.py` | NEW |
| `get_events_dir()` | `hooks/modules/core/paths.py` | MODIFY (add function) |
| SubagentStop writer call | `hooks/adapters/claude_code.py` | MODIFY |
| PostToolUse writer call | `hooks/adapters/claude_code.py` | MODIFY |
| Stop hook writer call | `hooks/adapters/claude_code.py` | MODIFY |
| SubagentStart injection | `hooks/adapters/claude_code.py` | MODIFY |
| `gaia-events` CLI | `gaia-ops/bin/gaia-events` | NEW |
| Event types enum | `hooks/modules/events/event_types.py` | NEW |

### 9.3 Implementation Plan

**Phase 1: Core (1 day)**
- Create `hooks/modules/events/` package
- Implement `EventWriter` with file locking (reuse `fcntl` pattern from
  `SessionContextWriter`)
- Implement `EventReader` with tail-from-end optimization
- Add `get_events_dir()` to `paths.py`
- Add event type constants

**Phase 2: Hook Integration (1 day)**
- Wire `EventWriter.append()` into `adapt_subagent_stop()`
- Wire `EventWriter.append()` into `adapt_post_tool_use()` (alongside
  existing `SessionContextWriter` -- parallel mode)
- Wire `EventWriter.append()` into `adapt_stop()`
- Build `EventContextInjector` for SubagentStart flow
- Wire injector alongside existing `build_session_events()`

**Phase 3: CLI (0.5 day)**
- Create `gaia-events` script with list, write, prune, stats subcommands
- Wire into package.json bin or build system

**Phase 4: Session Events Migration (0.5 day)**
- Replace `build_session_events()` calls with `build_event_context()`
- Deprecate `session_event_injector.py`
- Update `episode_writer.py` to read from Event Context

**Phase 5: External Triggers (documentation)**
- Document cron patterns
- Document loop/schedule integration patterns
- Provide example scripts

---

## 10. EventWriter Implementation Sketch

```python
"""Event writer for GAIA Event Context system."""

import fcntl
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.paths import get_events_dir


class EventWriter:
    """Append-only JSONL event writer with file locking."""

    def __init__(self, events_dir: Optional[Path] = None):
        self.events_dir = events_dir or get_events_dir()
        self.events_file = self.events_dir / "events.jsonl"
        self.lock_file = self.events_dir / "events.jsonl.lock"

    def append(self, event: Dict[str, Any]) -> None:
        """Append a single event to the log.

        Args:
            event: Dict with at minimum: type, source, result, summary.
                   ts is added automatically if missing.
        """
        if "ts" not in event:
            event["ts"] = datetime.now(timezone.utc).isoformat()

        self.events_dir.mkdir(parents=True, exist_ok=True)

        with open(self.lock_file, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                with open(self.events_file, "a") as f:
                    f.write(json.dumps(event, separators=(",", ":")) + "\n")
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def prune(self, retention_days: int = 7) -> int:
        """Remove events older than retention window. Returns count removed."""
        if not self.events_file.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        kept = []
        removed = 0

        with open(self.lock_file, "w") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                with open(self.events_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            evt = json.loads(line)
                            ts = datetime.fromisoformat(evt["ts"])
                            if ts > cutoff:
                                kept.append(line)
                            else:
                                removed += 1
                        except (json.JSONDecodeError, KeyError, ValueError):
                            kept.append(line)  # Keep unparseable lines

                with open(self.events_file, "w") as f:
                    for line in kept:
                        f.write(line + "\n")
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

        return removed
```

---

## 11. Open Questions

1. **Event deduplication:** If the same cron fires twice in quick succession,
   should we deduplicate? Current design: no -- every event is recorded.
   Consumers can filter by recency.

2. **Event size limits:** Should we cap `meta` field size? Recommendation:
   yes, 2KB per event max. Enforced by writer.

3. **Multi-project:** If `.claude/` serves multiple projects, should events
   be namespaced by project? Current design: no -- single project scope,
   matching how session events work today.

4. **Encryption/redaction:** Should sensitive data in summaries be redacted?
   Current design: writer responsibility. Hooks already sanitize tool output.

---

## 12. Summary

| Aspect | Decision |
|--------|----------|
| **Type** | Pure infrastructure (hooks + CLI) |
| **Storage** | `.claude/events/events.jsonl` (JSONL, append-only) |
| **Writers** | Hooks (automatic), CLI (manual/cron), agents (via hook) |
| **Readers** | Context injection, CLI, heartbeat loops |
| **Retention** | 7 days rolling (configurable) |
| **Session events** | Absorb over 4 phases |
| **Episodic memory** | Keep separate, enrich from events |
| **Audit logs** | Keep separate (different purpose) |
| **Workflow recorder** | Keep separate (telemetry, not operations) |
| **Trigger model** | Agnostic: cron, loop, schedule all write events the same way |
