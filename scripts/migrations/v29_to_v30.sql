-- Migration v29 -> v30: add the scheduled-task DESIRED-STATE registry.
--
-- Moves recurring headless tasks out of a single machine's crontab and into
-- gaia.db as OS-agnostic desired state, so any machine sharing the DB can
-- materialize them. Three tables:
--
--   scheduled_tasks         -- the desired state (one row per task). The schedule
--                              is NEUTRAL JSON (schedule_spec: calendar|interval),
--                              not a raw cron string, so a per-platform backend
--                              (cron today; launchd/schtasks later) can translate
--                              it. prompt_body is canonical/portable; prompt_path
--                              + project_dir are machine-local.
--   scheduled_task_machines -- machine scoping when machine_scope = 'named'.
--   scheduled_task_state    -- per-machine materialization state (installed?,
--                              backend, last_synced_at) -- the crontab is local
--                              to each machine, so this is tracked per (task,
--                              machine).
--
-- Consent model: writing desired state (register/enable/disable) is reversible
-- local bookkeeping (T0); only MATERIALIZING it into the machine scheduler
-- (`gaia schedule sync`) is a consented mutation (T3). The SessionStart hook only
-- DETECTS drift (zero-noise when reconciled); it never writes the scheduler.
--
-- Idempotency (floor model, replayed on every fresh install): schema.sql already
-- carries these tables and index, so on a fresh install these statements target
-- objects that already exist. CREATE TABLE/INDEX IF NOT EXISTS are idempotent by
-- construction -- no runner guard needed (no ADD COLUMN here).

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace     TEXT,
    name          TEXT NOT NULL,
    schedule_spec TEXT NOT NULL,
    schedule_hint TEXT,
    prompt_body   TEXT,
    prompt_path   TEXT,
    project_dir   TEXT,
    wrapper_kind  TEXT DEFAULT 'headless-claude',
    enabled       INTEGER NOT NULL DEFAULT 1,
    machine_scope TEXT NOT NULL DEFAULT 'all',
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (workspace, name)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_workspace ON scheduled_tasks(workspace, enabled);

CREATE TABLE IF NOT EXISTS scheduled_task_machines (
    task_id      INTEGER NOT NULL,
    machine_name TEXT NOT NULL,
    PRIMARY KEY (task_id, machine_name),
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS scheduled_task_state (
    task_id        INTEGER NOT NULL,
    machine_name   TEXT NOT NULL,
    backend        TEXT,
    installed      INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT,
    PRIMARY KEY (task_id, machine_name),
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id) ON DELETE CASCADE
);
