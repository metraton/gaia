-- Migration v46 -> v47: time-bounded suspension of scheduled tasks.
--
-- WHAT CHANGES
--   One new table plus its two partial unique indexes:
--
--     schedule_suspensions(id, workspace, task_id, suspended_at, until, reason)
--
--   Purely additive. No existing table is altered and no column is dropped, so
--   an already-registered task keeps its row, its schedule_spec, its enabled
--   flag and its per-machine state byte-for-byte. A DB migrated from v46 has
--   zero suspension rows, which reads as "nothing suspended" -- the same answer
--   a fresh install gives.
--
-- WHY
--   `scheduled_tasks.enabled = 0` could only express a PERMANENT decision: off
--   until somebody turns it back on. There was no way to say "off for the next
--   eight hours" -- so the only available move was `disable`, and a task
--   switched off for an afternoon stayed off for weeks because nothing carried a
--   deadline and nothing ever reminded anyone. This table adds the missing
--   shape: a suspension that carries its own expiry, reactivates by simply
--   ceasing to apply, and gets announced at session start both while it is live
--   and once it has lapsed.
--
-- WHY A SEPARATE TABLE, not two columns on scheduled_tasks.
--   The suspension has a SCOPE that scheduled_tasks cannot hold. A workspace-
--   wide switch ("suspend everything") is one fact about the workspace, not a
--   fact repeated across every task row -- storing it per-task would mean N
--   writes for one decision and would make "was this suspended globally or
--   individually?" unanswerable after the fact. task_id NULL carries that
--   global scope in the same table as the per-task rows, so one expiry
--   evaluator serves both and the two never disagree about what "now" means.
--
-- WHY NO WAKING PROCESS
--   Expiry is evaluated at READ time (reader._load_suspension_index compares
--   `until` against now). Introducing a daemon or a cron entry to expire
--   suspensions would mean a scheduled task whose job is managing scheduled
--   tasks, with its own drift and its own failure mode. A comparison at read
--   time cannot drift and cannot fail to fire.
--
-- WHY A LAPSED ROW IS NOT DELETED
--   The row is the only record that something came back to life. Deleting it on
--   read would reactivate the tasks silently, which is precisely the failure the
--   feature exists to prevent. It survives until an explicit `gaia schedule
--   resume` clears it -- the same contract task_notifications has with `gaia
--   notifications ack`.
--
-- NO ALTER TABLE HERE, so the bootstrap runner's _filter_add_column_idempotent
--   guard has nothing to neutralise. CREATE TABLE / CREATE INDEX both carry
--   IF NOT EXISTS and are replay-safe against a DB where schema.sql already
--   created them (the fresh-install path).

CREATE TABLE IF NOT EXISTS schedule_suspensions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace    TEXT,
    task_id      INTEGER,
    suspended_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    until        TEXT,
    reason       TEXT,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_suspensions_global
    ON schedule_suspensions(COALESCE(workspace, '')) WHERE task_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_schedule_suspensions_task
    ON schedule_suspensions(task_id) WHERE task_id IS NOT NULL;
