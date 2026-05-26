-- Migration v7 -> v8 fresh-install variant (agent-contract-handoff M3)
--
-- Used by bootstrap_database.sh when the live DB already has the approval_grants
-- table (i.e. schema.sql ran first on a clean install and created the table
-- in v8 target state already present).  This variant is a no-op -- it only
-- exists so the bootstrap guard-probe branch can select it and stamp the ledger.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: approval_grants table already exists on fresh install (schema.sql created it).
-- The indexes are also created by schema.sql on fresh install.
-- This variant only exists to stamp the ledger without applying DDL.
SELECT 1;
