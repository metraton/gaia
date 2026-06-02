-- Migration v16 -> v17 fresh-install variant
--
-- Used by bootstrap_database.sh when the live DB was created directly from
-- schema.sql at v17 state -- i.e. the `workspaces` table already carries the
-- `status` and `missing_since` columns because schema.sql declared them that way.
--
-- On a fresh install there are no legacy rows to alter, so the ALTER TABLE
-- statements in v16_to_v17.sql would fail with "duplicate column name".
-- This variant is a no-op; it exists only so the bootstrap guard-probe branch
-- (Section 3c, case 17) can select it and stamp the ledger without attempting
-- the ALTER.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: fresh install already at v17 state (schema.sql created workspaces with
-- status and missing_since columns).
SELECT 1;
