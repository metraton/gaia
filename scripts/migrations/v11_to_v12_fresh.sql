-- Migration v11 -> v12 fresh-install variant
--
-- Used by bootstrap_database.sh when the live DB was created directly from
-- schema.sql at v12 state (i.e. schema.sql already declares the approvals
-- and approval_events tables plus the trigger family).
--
-- On a fresh install:
--   - schema.sql creates approvals with all columns -> no DDL needed
--   - schema.sql creates approval_events with all columns -> no DDL needed
--   - schema.sql creates all three triggers -> no DDL needed
--
-- This variant is a no-op; it only exists so the bootstrap guard-probe branch
-- can select it and stamp the ledger without applying DDL.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: fresh install already at v12 state (schema.sql created all objects).
SELECT 1;
