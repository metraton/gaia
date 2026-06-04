-- Migration v17 -> v18 (stable project identity: collapse same repo across vantages)
--
-- Background
-- ----------
-- The `projects` table is keyed (workspace, name). The scanner derives `name`
-- from the on-disk basename and `workspace` from the scan vantage's identity.
-- The SAME physical repository scanned from two different roots -- e.g. once
-- from the workspace root and once from inside the repo's own subdirectory
-- (which resolves a different *workspace* identity) -- produced TWO distinct
-- (workspace, name) rows: a duplicate of one physical project.
--
-- This migration adds a stable, vantage-independent project identity so the
-- UPSERT can collapse those duplicates into one row. Identity is resolved by
-- the scanner (tools/scan/store_populator.resolve_project_identity) in this
-- order: git-common-dir (realpath) > normalized remote (host/owner/repo) >
-- realpath of the project path.
--
-- Two additive, NON-DESTRUCTIVE changes:
--
--   1. ALTER TABLE projects ADD COLUMN project_identity TEXT (nullable)
--      Scanner-owned. NULL allowed for legacy/uninitialized rows so the column
--      adds cleanly to an existing DB without backfill. A subsequent `gaia scan`
--      populates it for every live project.
--
--   2. CREATE UNIQUE INDEX idx_projects_identity ... WHERE project_identity IS NOT NULL
--      PARTIAL unique index. Enforces one row per physical repo for rows that
--      HAVE an identity, while exempting legacy NULL-identity rows from the
--      uniqueness constraint (so multiple NULL rows can coexist until the next
--      scan backfills them). This is the ON CONFLICT target the writer uses.
--
-- Backfill note
-- -------------
-- This migration does NOT backfill project_identity for existing rows -- it
-- cannot derive git-common-dir from SQL alone, and the value is cheap to
-- recompute on the next scan. Existing duplicate rows (if any) are left intact;
-- the first scan after migration assigns identities and any genuine duplicate
-- collapses on the following scan once both rows share an identity. Because the
-- index is PARTIAL, the ADD COLUMN (all rows NULL) cannot fail on existing
-- duplicates.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT. A failure
-- mid-flight rolls back to v17 state; the ledger row is NOT inserted, so the
-- next bootstrap retry sees the same pending migration.

-- ---------------------------------------------------------------------------
-- Step 1: Add project_identity column to projects (nullable, scanner-owned)
-- ---------------------------------------------------------------------------
ALTER TABLE projects ADD COLUMN project_identity TEXT;

-- ---------------------------------------------------------------------------
-- Step 2: Partial UNIQUE index that collapses the same physical repo to one row
-- ---------------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_identity
    ON projects(project_identity) WHERE project_identity IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Step 3: Bump schema_version to 18
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (18, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'projects stable identity: project_identity column + partial unique index (collapse same repo across vantages)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;                                  -- expect: 18
-- SELECT name FROM pragma_table_info('projects') WHERE name='project_identity'; -- expect: project_identity
-- SELECT name FROM sqlite_master WHERE type='index' AND name='idx_projects_identity'; -- expect: idx_projects_identity
