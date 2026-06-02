-- Migration v15 -> v16 (gaia-scan-overhaul: projects soft-delete columns)
--
-- Background
-- ----------
-- The prune step in scan_workspace_to_store currently issues hard DELETEs for
-- projects that are no longer found on disk. Brief gaia-scan-overhaul replaces
-- that with a soft-delete: projects are marked 'missing' instead of removed,
-- so historical context (memory atoms, episodes, briefs keyed on that project)
-- survives the scan cycle.
--
-- This migration adds two scanner-owned columns to the `projects` table:
--
--   status TEXT NOT NULL DEFAULT 'active'
--     Values: 'active' | 'missing'.
--     'active'  -- project was present on the last scan run.
--     'missing' -- project was NOT found on the most recent scan; kept as a
--                  tombstone so child-table data and historical context survive.
--     Default 'active': existing rows (which were present at migration time)
--     are classified as active. New rows inserted by the scanner also default
--     to 'active' without requiring callers to supply the column.
--
--   missing_since TEXT (nullable)
--     ISO8601 UTC timestamp of when status was first set to 'missing'.
--     NULL when status='active'. The scanner sets this on the first cycle
--     where it cannot find the project; subsequent cycles leave it unchanged
--     (the timestamp records the FIRST disappearance, not the most recent).
--
-- Scope of this migration
-- -----------------------
-- ONLY the DDL changes. The prune logic (DELETE -> UPDATE status='missing') is
-- implemented in the NEXT task (scan populator changes). This migration only
-- ensures the columns exist and that the writer accepts them.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT. A failure
-- mid-flight rolls back to v15 state; the ledger row is NOT inserted, so the
-- next bootstrap retry sees the same pending migration.

-- ---------------------------------------------------------------------------
-- Step 1: Add status column to projects (NOT NULL DEFAULT 'active')
-- ---------------------------------------------------------------------------
ALTER TABLE projects ADD COLUMN status TEXT NOT NULL DEFAULT 'active';

-- ---------------------------------------------------------------------------
-- Step 2: Add missing_since column to projects (nullable)
-- ---------------------------------------------------------------------------
ALTER TABLE projects ADD COLUMN missing_since TEXT;

-- ---------------------------------------------------------------------------
-- Step 3: Bump schema_version to 16
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (16, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'projects soft-delete: status + missing_since columns');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;          -- expect: 16
-- PRAGMA table_info(projects);                       -- expect: status, missing_since columns present
