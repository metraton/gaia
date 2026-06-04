# Crons Persistence Schema

**Version:** 1  
**File location:** `.claude/crons.json`  
**Owner:** Gaia cron persistence system

---

## Schema

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

## Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Unique identifier for the cron. Used as the dedup key during restore. Must be URL-safe (alphanumeric, hyphens). |
| `interval_minutes` | integer | yes | How often the cron fires, in minutes. Mirrors CronCreate interval. |
| `prompt` | string | yes | The exact prompt sent to the orchestrator on each tick. |
| `enabled` | boolean | yes | If false, the cron is skipped during restore. Allows pausing without deletion. |
| `created` | string (ISO 8601 UTC) | yes | Timestamp when the cron was first created. Set once, never updated. |
| `last_run` | string (ISO 8601 UTC) or null | yes | Timestamp of the most recent execution. Null if the cron has never run. |

## Top-level Fields

| Field | Type | Description |
|-------|------|-------------|
| `crons` | array | The list of persisted cron entries. May be empty. |
| `version` | integer | Schema version. Currently 1. Increment when field semantics change. |

## Constraints

- `name` must be unique within the `crons` array. Duplicate names are invalid.
- `interval_minutes` must be a positive integer greater than 0.
- `last_run` is `null` when the cron has been created but has not yet fired.

## Example: Multiple Crons

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
    },
    {
      "name": "drift-monitor",
      "interval_minutes": 60,
      "prompt": "Check for infrastructure drift in the current project",
      "enabled": false,
      "created": "2026-04-10T10:00:00Z",
      "last_run": null
    }
  ],
  "version": 1
}
```

## File Location

The file lives at `.claude/crons.json`, resolved relative to the active project root (same directory where `.claude/` is found). The path module `find_claude_dir()` from `hooks/modules/core/paths.py` provides the canonical `.claude/` path.

For projects that use `CLAUDE_PLUGIN_DATA`, the file lives under that data directory instead, consistent with how other persisted data (logs, sessions, grants) is stored.
