-- Migration v14 -> v15 fresh-install variant
--
-- Used by bootstrap_database.sh when the live DB was created directly from
-- schema.sql at v15 state -- i.e. the per-project child tables (apps, services,
-- tf_modules, ...) already carry the `project` column because schema.sql
-- declared them that way.
--
-- On a fresh install there is no legacy `repo` column to rename, so the
-- RENAME COLUMN statements in v14_to_v15.sql would fail with "no such column:
-- repo". This variant is a no-op; it exists only so the bootstrap guard-probe
-- branch (Section 3c, case 15) can select it and stamp the ledger without
-- attempting the rename.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: fresh install already at v15 state (schema.sql created child tables
-- with the `project` column).
SELECT 1;
