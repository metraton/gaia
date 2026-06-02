-- Migration v13 -> v14 (gaia-scan-overhaul: projects.path column, findability)
--
-- Background
-- ----------
-- v13 schema added `group_name` to the `projects` table (workspace->group->repo
-- hierarchy). v14 adds a `path TEXT` column to the same table so that the
-- scanner can persist the absolute on-disk path of each project, enabling
-- name-based findability (project name -> path + workspace) without
-- re-scanning or relying on external tooling.
--
-- Column semantics
-- ----------------
--   path TEXT (nullable)
--     Absolute filesystem path to the project root directory on the machine
--     where the scanner ran. For example: '/home/jorge/ws/me/gaia'.
--
--     NULL = path not yet recorded (the default for all pre-existing rows
--     after migration; also the default for rows created before the scanner
--     logic that assigns the value is deployed).
--     The value is assigned by populate_project (T2.x of the plan) using
--     the project_path argument already threaded through the scanner API.
--     This migration only adds the column; it does NOT back-fill existing
--     rows.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v13 state; the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.
-- Closes T1.2 (schema + writer plumbing) of brief gaia-scan-overhaul.

-- ---------------------------------------------------------------------------
-- Step 1: Add path column to projects
-- ---------------------------------------------------------------------------
ALTER TABLE projects ADD COLUMN path TEXT;

-- ---------------------------------------------------------------------------
-- Step 2: Bump schema_version to 14
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (14, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'projects.path column (findability: project -> path + workspace)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;         -- expect: 14
-- PRAGMA table_info(projects);                     -- expect path column present
