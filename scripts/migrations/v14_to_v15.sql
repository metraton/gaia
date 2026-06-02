-- Migration v14 -> v15 (child-table FK column rename: repo -> project)
--
-- Background
-- ----------
-- Commit be9698f ("feat(substrate): rename projects->workspaces, repos->projects")
-- renamed the substrate hierarchy. The OLD `repos` concept (git-bearing units)
-- became `projects`, and the OLD `projects` container became `workspaces`.
--
-- schema.sql and the writer/populator code were updated to use the `project`
-- column on the nine per-project child tables:
--   apps, libraries, services, features, tf_modules, tf_live, releases,
--   workloads, clusters_defined
-- ...but NO migration was ever shipped to rename the live column. Because every
-- child table is declared with `CREATE TABLE IF NOT EXISTS`, schema.sql silently
-- no-ops on a DB that already has these tables, so existing installations (at
-- v14) still carry the legacy column name `repo`. The writer/populator code,
-- which already emits `project` in every INSERT / SELECT / ON CONFLICT /
-- delete_missing path, then fails at runtime with "no such column: project" the
-- first time `gaia scan` populates infra/app rows.
--
-- This was latent because scan_workspace_to_store had never run end-to-end via
-- the CLI (it was only wired in T3.1), and the unit tests mock the writer or
-- use fixtures with no infra/app content, so the column mismatch never executed.
--
-- Fix direction
-- -------------
-- `project` is the canonical name: it is what schema.sql declares, what every
-- writer/populator SQL path emits, and what the writer's PK maps in
-- delete_missing_in use. The live DB is the sole outlier. The lowest-blast-
-- radius fix is therefore a forward migration that renames the legacy `repo`
-- column to `project` on each of the nine child tables. No code change is
-- required.
--
-- Mechanism
-- ---------
-- SQLite >= 3.25 supports `ALTER TABLE ... RENAME COLUMN`, which rewrites the
-- stored DDL in place, automatically updating the column's appearance in the
-- table's PRIMARY KEY and in its own FOREIGN KEY clause. The FTS5 mirror tables
-- (apps_fts, services_fts) index text content columns (name, etc.), NOT the FK
-- column, so they are unaffected. Verified on sqlite 3.45.x: PRAGMA
-- foreign_key_check returns no rows after the rename and project-keyed INSERTs
-- succeed.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT. A failure
-- mid-flight rolls back to v14 state; the ledger row is NOT inserted, so the
-- next bootstrap retry sees the same pending migration.

-- ---------------------------------------------------------------------------
-- Step 1: Rename repo -> project on each per-project child table
-- ---------------------------------------------------------------------------
ALTER TABLE apps             RENAME COLUMN repo TO project;
ALTER TABLE libraries        RENAME COLUMN repo TO project;
ALTER TABLE services         RENAME COLUMN repo TO project;
ALTER TABLE features         RENAME COLUMN repo TO project;
ALTER TABLE tf_modules       RENAME COLUMN repo TO project;
ALTER TABLE tf_live          RENAME COLUMN repo TO project;
ALTER TABLE releases         RENAME COLUMN repo TO project;
ALTER TABLE workloads        RENAME COLUMN repo TO project;
ALTER TABLE clusters_defined RENAME COLUMN repo TO project;

-- ---------------------------------------------------------------------------
-- Step 2: Bump schema_version to 15
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (15, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'rename child-table FK column repo -> project (substrate rename catch-up)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;          -- expect: 15
-- PRAGMA table_info(apps);                           -- expect: project (not repo)
-- PRAGMA foreign_key_check;                          -- expect: no rows
