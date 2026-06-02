-- Migration v12 -> v13 (gaia-scan-overhaul: workspace->group->repo model, AC-2)
--
-- Background
-- ----------
-- v12 schema has the durable approvals / approval_events tables from the
-- approval-model-redesign brief. v13 adds a `group_name` column to the
-- `projects` table to support the workspace->group->repo hierarchy introduced
-- by the gaia-scan-overhaul brief (AC-2).
--
-- Why `group_name` and not `group`
-- ---------------------------------
-- `group` is a reserved keyword in SQL. Using it as a column name requires
-- quoting everywhere and produces subtle bugs in hand-written queries.
-- `group_name` is the canonical column name for this feature.
--
-- Column semantics
-- ----------------
--   group_name TEXT (nullable)
--     The optional organizational group or team this project belongs to within
--     its workspace. For example, in a GitHub organization the group_name might
--     be a team slug, a monorepo sub-directory, or any intermediate container
--     between the workspace and the individual project (repo).
--
--     NULL = ungrouped (the default for all pre-existing rows after migration).
--     The value is assigned by populate_project (T2.2 of the plan) using
--     scanner-specific detection logic. This migration only adds the column;
--     it does NOT back-fill existing rows.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v12 state; the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.
-- Closes T1.1 (schema + writer plumbing) of brief gaia-scan-overhaul.

-- ---------------------------------------------------------------------------
-- Step 1: Add group_name column to projects
-- ---------------------------------------------------------------------------
ALTER TABLE projects ADD COLUMN group_name TEXT;

-- ---------------------------------------------------------------------------
-- Step 2: Bump schema_version to 13
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (13, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'projects.group_name column (workspace->group->repo, AC-2)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;         -- expect: 13
-- PRAGMA table_info(projects);                     -- expect group_name column present
