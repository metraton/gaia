-- Migration v16 -> v17 (DEMOTE case: workspaces soft-delete columns)
--
-- Background
-- ----------
-- v16 added soft-delete (status / missing_since) to the `projects` table. The
-- multi-workspace DEMOTE test revealed the same gap one level up: when a
-- workspace loses its Gaia install footprint (the user removes its `.claude/`,
-- "demoting" it), the `workspaces` row had no way to record that. A re-scan of
-- a demoted directory would persist the row and refresh `last_scan_at` as if it
-- were still a live workspace.
--
-- This migration adds two scanner-owned columns to the `workspaces` table,
-- mirroring the v16 projects soft-delete contract:
--
--   status TEXT NOT NULL DEFAULT 'active'
--     Values: 'active' | 'missing'.
--     'active'  -- the workspace carried a Gaia install on the last scan.
--     'missing' -- the workspace's install footprint disappeared (demoted);
--                  kept as a tombstone so its projects/history survive.
--     Default 'active': existing rows (live workspaces at migration time) are
--     classified active without requiring callers to supply the column.
--
--   missing_since TEXT (nullable)
--     ISO8601 UTC timestamp of when status was first set to 'missing'.
--     NULL when status='active'. Set on the first scan that finds the install
--     gone; subsequent scans leave it unchanged (records the FIRST demote).
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT. A failure
-- mid-flight rolls back to v16 state; the ledger row is NOT inserted, so the
-- next bootstrap retry sees the same pending migration.

-- ---------------------------------------------------------------------------
-- Step 1: Add status column to workspaces (NOT NULL DEFAULT 'active')
-- ---------------------------------------------------------------------------
ALTER TABLE workspaces ADD COLUMN status TEXT NOT NULL DEFAULT 'active';

-- ---------------------------------------------------------------------------
-- Step 2: Add missing_since column to workspaces (nullable)
-- ---------------------------------------------------------------------------
ALTER TABLE workspaces ADD COLUMN missing_since TEXT;

-- ---------------------------------------------------------------------------
-- Step 3: Bump schema_version to 17
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (17, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'workspaces soft-delete: status + missing_since columns (DEMOTE)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;          -- expect: 17
-- PRAGMA table_info(workspaces);                      -- expect: status, missing_since columns present
