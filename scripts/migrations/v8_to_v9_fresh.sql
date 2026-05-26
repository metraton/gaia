-- Migration v8 -> v9 fresh-install variant (agent-contract-handoff M4)
--
-- Used by bootstrap_database.sh when the live DB already has the
-- agent_contract_handoffs table (i.e. schema.sql ran first on a clean
-- install and created the tables in v9 target state).
--
-- This variant is a no-op -- it only exists so the bootstrap guard-probe
-- branch can select it and stamp the ledger without applying DDL.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- No DDL is executed; the COMMIT is harmless.

-- No-op: all v9 tables and trigger already exist on fresh install (schema.sql).
-- This variant only exists to stamp the ledger.
SELECT 1;
