-- Migration v17 -> v18 fresh-install variant
--
-- Used by bootstrap_database.sh when the live DB was created directly from
-- schema.sql at v18 state -- i.e. the `projects` table already carries the
-- `project_identity` column because schema.sql declared it that way.
--
-- On a fresh install there are no legacy rows to alter, so the ALTER TABLE
-- statement in v17_to_v18.sql would fail with "duplicate column name".
-- This variant therefore SKIPS the ALTER but still creates the partial unique
-- index -- the index is intentionally NOT declared in schema.sql because
-- referencing project_identity there would parse-fail when bootstrapping a
-- legacy (pre-v18) DB whose CREATE TABLE IF NOT EXISTS short-circuits before
-- the column exists. Creating it here (idempotent IF NOT EXISTS) is the only
-- place the index lands on a fresh install. Same convention as
-- idx_memory_class_status (scripts/migrations/v3_to_v4_fresh.sql).
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.

-- Step 1 (skipped on fresh install): the project_identity column already exists.

-- Step 2: create the partial unique index that collapses the same physical repo
-- (same project_identity) to one row, exempting NULL-identity legacy rows.
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_identity
    ON projects(project_identity) WHERE project_identity IS NOT NULL;
