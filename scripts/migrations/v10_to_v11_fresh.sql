-- Migration v10 -> v11 fresh-install variant
--
-- Used by bootstrap_database.sh when the live DB was created directly from
-- schema.sql at v11 state (i.e. schema.sql already declares memory.class as
-- NOT NULL CHECK and trg_pcc_history with correct column references).
--
-- On a fresh install:
--   - schema.sql creates memory with class NOT NULL CHECK -> no rebuild needed
--   - schema.sql creates trg_pcc_history with correct column refs -> no fix needed
--
-- This variant is a no-op; it only exists so the bootstrap guard-probe branch
-- can select it and stamp the ledger without applying DDL.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: fresh install already at v11 state (schema.sql created all objects).
SELECT 1;
