-- Migration v13 -> v14 fresh-install variant
--
-- Used by bootstrap_database.sh when the live DB was created directly from
-- schema.sql at v14 state (i.e. schema.sql already declares the path column
-- on the projects table).
--
-- On a fresh install:
--   - schema.sql creates projects with all columns including path -> no DDL needed
--
-- This variant is a no-op; it only exists so the bootstrap guard-probe branch
-- can select it and stamp the ledger without applying DDL.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: fresh install already at v14 state (schema.sql created all objects).
SELECT 1;
