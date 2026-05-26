-- Migration v6 -> v7 (agent-contract-handoff M1: workspaces.last_scan_at)
--
-- Background
-- ----------
-- v6 schema has:
--   workspaces(name, identity, created_at)
--
-- v7 adds last_scan_at (ISO8601 TEXT, nullable) to workspaces.
-- NULL means "never scanned via gaia scan" -- not 'pending'.
-- The column is written by bin/cli/scan.py after a successful scan run.
--
-- Design decision: independent migration version
-- -----------------------------------------------
-- workspaces.last_scan_at is the first column added to workspaces.
-- It belongs in v7, owned by the agent-contract-handoff brief (M1).
-- M3 and M4 will add further columns in later v7 sub-tasks of the
-- same migration file (both will extend this file in their dispatches).
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v6 state and the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.

-- 1. Add last_scan_at column to workspaces.
--    SQLite ALTER TABLE ADD COLUMN is safe for nullable columns.
ALTER TABLE workspaces ADD COLUMN last_scan_at TEXT;

