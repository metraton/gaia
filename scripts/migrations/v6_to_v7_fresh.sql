-- Migration v6 -> v7 fresh-install variant (agent-contract-handoff M1)
--
-- Used by bootstrap_database.sh when the live DB already has last_scan_at
-- (i.e. schema.sql ran first on a clean install and created workspaces with
-- the v7 column already present). This variant is a no-op -- it only exists
-- so the bootstrap guard-probe branch can select it and stamp the ledger.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: column already exists on fresh install (schema.sql created it).
-- This variant only exists to stamp the ledger without applying DDL.
SELECT 1;
