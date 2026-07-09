-- Migration v28 -> v29: add `task_notifications` table (brief: headless task
-- scheduler / "programador de tareas headless").
--
-- A headless scheduled task (see the scheduled-task skill) runs unattended and
-- cannot ask the user anything mid-run, so it leaves ONE report row here when it
-- finishes: a generic PII-free summary plus any accumulated approval_ids, keyed
-- by the resumable Claude session_id (`claude --resume <session_id>`). The row
-- carries a MUTABLE `unread` flag that `gaia notifications ack` clears -- this is
-- what makes it a lightweight unread inbox (surfaced at SessionStart and as a
-- per-prompt counter), distinct from the append-only harness_events audit mirror.
-- Not curated memory, so it is written without an agent_permissions gate.
--
-- Idempotency (floor model, replayed on every fresh install): schema.sql already
-- carries this table and index, so on a fresh install these statements target
-- objects that already exist. `CREATE TABLE IF NOT EXISTS` and
-- `CREATE INDEX IF NOT EXISTS` are idempotent by construction -- no runner guard
-- needed (unlike ADD COLUMN, which SQLite cannot make conditional).

CREATE TABLE IF NOT EXISTS task_notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace  TEXT,
    task_name  TEXT NOT NULL,
    headline   TEXT NOT NULL,
    body       TEXT,
    session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    unread     INTEGER NOT NULL DEFAULT 1,
    acked_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_notifications_unread ON task_notifications(unread, created_at DESC);
