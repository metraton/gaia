-- Migration v30 -> v31: drop three duplicate (byte-identical) indexes.
--
-- Root cause (migrate_06 / migrate_08 inheritance):
--   * The historical migrate_06_state_machines.py created indexes on the
--     column named `project` -- e.g.
--         CREATE INDEX idx_episodes_project_timestamp ON episodes(project, ...)
--   * migrate_08_rename_workspace.py later RENAMED the `project` column to
--     `workspace` and re-created the canonical indexes under the new
--     `*_workspace*` names (the shape gaia/store/schema.sql carries today).
--   * The rename left the OLD `*_project*` indexes in place, now pointing at
--     the renamed `workspace` column -- so each pair is byte-identical and one
--     of the two is pure dead weight (extra write amplification, larger DB, no
--     read benefit). The redundant scan in schema.sql confirmed all three:
--         idx_memory_project              == idx_memory_workspace              -> memory(workspace)
--         idx_episodes_project_timestamp  == idx_episodes_workspace_timestamp  -> episodes(workspace, timestamp DESC)
--         idx_harness_events_project_ts   == idx_harness_events_workspace_ts   -> harness_events(workspace, ts DESC)
--
-- Fix: DROP the `*_project*` variant of each pair, keeping the canonically
-- named `*_workspace*` index (the one schema.sql declares for fresh installs).
--
-- Idempotency (floor model, replayed on every fresh install): schema.sql never
-- created the `*_project*` variants, so on a fresh install these statements
-- target objects that do not exist -- DROP INDEX IF EXISTS is a no-op there and
-- a real drop only on an older DB that inherited them via migrate_06/08. No
-- runner guard needed. schema.sql is unchanged: it is already the post-drop
-- canonical shape (it only declares the `*_workspace*` names).

DROP INDEX IF EXISTS idx_memory_project;
DROP INDEX IF EXISTS idx_episodes_project_timestamp;
DROP INDEX IF EXISTS idx_harness_events_project_ts;
