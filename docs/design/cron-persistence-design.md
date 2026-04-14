# Cron Persistence Design

**Status:** DESIGN
**Date:** 2026-04-13
**Author:** gaia-system (meta-agent)

---

## 1. Problem Statement

`CronCreate` schedules recurring agent triggers within the active Claude Code session.
When the session closes, the Claude Code process terminates and all live crons are
gone. No persistence mechanism exists. The next session starts with zero crons,
requiring the user to recreate them manually.

**Goal:** Persist cron definitions to disk and restore them automatically on
`SessionStart` so recurring automations survive session boundaries.

---

## 2. Task 1: crons.json Format

The schema is already defined and documented at:

```
/home/jorge/ws/me/gaia-ops-dev/config/crons-schema.md
```

For reference, the canonical format is:

```json
{
  "crons": [
    {
      "name": "check-email",
      "interval_minutes": 180,
      "prompt": "Revisa el correo y haz triage según gmail-triage skill",
      "enabled": true,
      "created": "2026-04-13T20:00:00Z",
      "last_run": "2026-04-13T23:00:00Z"
    }
  ],
  "version": 1
}
```

**File location:** `.claude/crons.json`, resolved via `get_plugin_data_dir()` from
`hooks/modules/core/paths.py`. This places it alongside other persisted data (logs,
sessions, approval grants) and respects the `CLAUDE_PLUGIN_DATA` env var when set.

All field semantics, constraints, and examples are in the schema document.
This design document does not duplicate them.

---

## 3. Task 2: SessionStart Restore Design

### 3.1 Existing SessionStart Hook

`hooks/session_start.py` is a thin stdin/stdout entry point. Current responsibilities:

1. First-time setup — creates project permissions if not present (`run_first_time_setup`)
2. Project scan — if in ops mode and context is stale, runs a lightweight scan

Both are non-fatal: exceptions are caught and `sys.exit(0)` is always returned.
The hook reads stdin and prints a JSON response dict but does not block the session.

The pattern for new functionality: add a module under `hooks/modules/`, import and
call it from `session_start.py` after the existing steps, wrapped in try/except.

### 3.2 Where the Restore Logic Goes

**New module:** `hooks/modules/crons/cron_restorer.py`

This module is called from `session_start.py` in the ops-mode branch, after the
project scan step.

```
hooks/modules/crons/
    __init__.py
    cron_restorer.py    <- restore logic
    cron_writer.py      <- write/update crons.json (shared with Task 3)
```

### 3.3 The Key Challenge: Detecting Already-Running Crons

**Finding:** `CronList` is a native tool available to the orchestrator
(`gaia-orchestrator.md` tools field). It lists currently active crons in the
Claude Code session.

**The constraint:** `session_start.py` is a Python hook that runs before the
orchestrator is active. It cannot call `CronList` directly — that's an
orchestrator-level tool, not a Python API.

This means the check for "is this cron already running?" cannot happen in the hook
layer. Two viable approaches exist:

**Option A: Hook creates, orchestrator deduplicates (recommended)**

The hook does NOT call `CronCreate`. Instead, it writes a restore request to a
sidecar file (e.g., `.claude/crons-pending-restore.json`) and emits a message
in the hook response. On `UserPromptSubmit` (or as an injected system message),
the orchestrator reads the pending restore file, calls `CronList` to check what
is already running, and calls `CronCreate` only for crons not already present.

Advantages:
- Deduplication uses the actual `CronList` tool — authoritative
- No duplicate crons even if SessionStart fires more than once
- Restore is observable: the orchestrator can report what it did

Disadvantages:
- Crons are not active until the first user interaction (not instant on startup)

**Option B: Hook restores unconditionally, accept rare duplicates**

The hook injects a tool invocation via the `additionalContext` mechanism (same as
`session_event_injector.py`) that prompts the orchestrator to run the restore on
its first turn. The orchestrator calls `CronList`, diffs against `crons.json`, and
calls `CronCreate` for missing entries.

This is structurally the same as Option A but uses the existing additionalContext
injection path rather than a sidecar file.

**Recommended: Option A with additionalContext injection** (hybrid of both).

The hook sets a flag and injects an instruction into `additionalContext` for
`SubagentStart`. The orchestrator — which already has `CronList` and `CronCreate`
in its tools — handles the actual restore on its first active turn.

### 3.4 Restore Flow (Detailed)

```
SessionStart hook (Python):
  1. Read .claude/crons.json (via get_plugin_data_dir())
  2. If file missing or empty -> skip, log "no crons.json found"
  3. If crons array has entries -> write restore intent to
     .claude/session/active/pending-cron-restore.json
     Format: { "crons": [<enabled cron entries only>], "requested_at": "<ISO>" }
  4. Log "N crons pending restore"

UserPromptSubmit hook (or orchestrator first turn):
  The session_event_injector already reads .claude/session/active/ and injects
  context into SubagentStart additionalContext. Extend it to detect
  pending-cron-restore.json and inject a restore instruction:

  "CRON RESTORE PENDING: N crons are defined in .claude/crons.json and need to
   be restored. Before handling the user's request, call CronList to see what is
   already running, then call CronCreate for each cron in the pending list that
   is not already active. Report: N restored, M already running, K disabled."

Orchestrator (first active turn with pending restore):
  1. CronList -> get currently active crons by name
  2. For each entry in pending list:
     a. If name found in CronList output -> skip (already running)
     b. If name not found -> CronCreate(prompt, interval_minutes)
  3. Delete .claude/session/active/pending-cron-restore.json
  4. AskUserQuestion: "Restored N crons (M already running)"
     -- or silently continue if N=0 and M=total
```

### 3.5 Deduplication Key

The `name` field in `crons.json` is the dedup key. `CronList` returns active cron
names. A cron is "already running" if its name appears in the `CronList` output.

This relies on the orchestrator setting the cron name consistently — see Task 3.

### 3.6 Report Format

After restore, the orchestrator reports to the user:

```
Cron restore complete: 2 restored, 1 already running, 1 disabled (skipped).
```

If all crons were already running (e.g., session reconnect with persistent process):

```
Cron restore: all 3 crons already running, nothing to do.
```

If no crons.json exists: silent (no report).

---

## 4. Task 3: Persisting on CronCreate

### 4.1 The Three Options

**Option A: Orchestrator convention**

After every `CronCreate`, the orchestrator calls the meta-agent or a skill that
writes to `crons.json`. This is a soft convention: it depends on orchestrator
discipline, not enforcement.

**Option B: PostToolUse hook for CronCreate**

The `post_tool_use.py` hook currently matches `Bash` and `AskUserQuestion`
(see `hooks.json`). Add a new matcher for `CronCreate`:

```json
{
  "matcher": "CronCreate",
  "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/post_tool_use.py"}]
}
```

In `adapt_post_tool_use`, detect `tool_name == "CronCreate"`, extract `tool_input`
(which contains `prompt`, `interval_minutes`, and optionally `name`), and call
`cron_writer.py` to upsert the entry into `crons.json`.

**Option C: Wrap CronCreate with a Skill**

Create a `cron-create` skill that instructs the orchestrator to write to
`crons.json` before or after calling `CronCreate`. This is documentation, not
enforcement.

### 4.2 Recommended Approach: PostToolUse Hook (Option B)

**Rationale:**

The PostToolUse hook is already the established pattern for intercepting tool
completions in Gaia. It runs automatically on every `CronCreate` call regardless
of which agent or session triggers it. No orchestrator discipline is required.
The hook receives `tool_input` (the parameters passed to `CronCreate`) and
`tool_response` (the result, including the assigned cron ID if the tool returns
one). Both are available for writing to `crons.json`.

The soft-convention approaches (A and C) are fragile: they break any time a new
code path calls `CronCreate` that wasn't updated to follow the convention.

**Implementation sketch:**

In `hooks.json`, add to PostToolUse matchers:
```json
{
  "matcher": "CronCreate",
  "hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/post_tool_use.py"}]
}
```

In `hooks/modules/crons/cron_writer.py`:
```python
def upsert_cron(name: str, interval_minutes: int, prompt: str) -> None:
    """Write or update a cron entry in .claude/crons.json."""
    path = get_plugin_data_dir() / "crons.json"
    data = _read_crons_json(path)
    existing = {c["name"]: c for c in data["crons"]}
    if name in existing:
        # Update prompt/interval, preserve created/last_run
        existing[name]["interval_minutes"] = interval_minutes
        existing[name]["prompt"] = prompt
        existing[name]["enabled"] = True
    else:
        existing[name] = {
            "name": name,
            "interval_minutes": interval_minutes,
            "prompt": prompt,
            "enabled": True,
            "created": datetime.utcnow().isoformat() + "Z",
            "last_run": None,
        }
    data["crons"] = list(existing.values())
    _write_crons_json(path, data)
```

In `adapt_post_tool_use`, add:
```python
if tool_name == "CronCreate" and success:
    from modules.crons.cron_writer import upsert_cron
    name = parameters.get("name") or _derive_name(parameters.get("prompt", ""))
    upsert_cron(
        name=name,
        interval_minutes=parameters.get("interval_minutes", 60),
        prompt=parameters.get("prompt", ""),
    )
```

**Name derivation:** `CronCreate` may or may not accept an explicit `name`
parameter (depends on the tool schema). If no name is provided, derive one
from the first 5 words of the prompt, slugified (lowercase, hyphens). The
orchestrator should always pass an explicit name when creating crons so the
dedup key is stable across sessions.

**Handling CronDelete:** Similarly, add a `CronDelete` PostToolUse matcher
that removes the entry from `crons.json` when a cron is deleted.

### 4.3 Naming Convention

The orchestrator must use explicit, stable names when calling `CronCreate`.
Names must be:
- Lowercase alphanumeric with hyphens
- Descriptive enough to recognize across sessions
- Consistent (same name for the same logical cron)

Example: `check-email`, `drift-monitor`, `gtd-review`.

This convention should be documented in the orchestrator agent definition or
in a `cron-management` skill.

---

## 5. Open Questions (Not Blocking Design)

| Question | Impact | Resolution |
|----------|--------|------------|
| Does `CronCreate` accept an explicit `name` parameter? | If not, name derivation from prompt must be used. | Check tool schema at implementation time. |
| Does `CronList` return cron names in a stable format? | Dedup depends on name matching. | Validate at implementation time. |
| Should `last_run` be updated by a PostToolUse hook on `CronTick` (if that event exists)? | Enables smarter restore (skip if very recent). | Investigate Claude Code cron event hooks. |
| Should disabled crons be preserved in `crons.json` indefinitely? | UX: disabled means "paused", not "deleted". | Default: yes. User can clean up manually or via a command. |

---

## 6. Summary

| Component | Approach | Location |
|-----------|----------|----------|
| Schema | Already defined | `config/crons-schema.md` |
| Persist on create | PostToolUse hook for `CronCreate` | `hooks/modules/crons/cron_writer.py` |
| Persist on delete | PostToolUse hook for `CronDelete` | `hooks/modules/crons/cron_writer.py` |
| Restore on session start | Hook writes pending-restore; orchestrator executes | `hooks/modules/crons/cron_restorer.py` + orchestrator |
| Deduplication | `CronList` called by orchestrator before `CronCreate` | Orchestrator layer |
| Naming convention | Explicit stable names required from orchestrator | Orchestrator agent definition |
