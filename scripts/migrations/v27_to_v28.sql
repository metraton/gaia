-- Migration v27 -> v28: add `contract_id` idempotency key to
-- agent_contract_handoffs (brief: contract-as-managed-data-agent-contract-
-- handoff-agnostico-por-cli, task T7).
--
-- Establishes the exact idempotency key `gaia.store.writer
-- .finalize_agent_contract_handoff` UPSERTs on: `INSERT ... ON CONFLICT
-- (contract_id) DO NOTHING`. `contract_id` holds the CLI-minted draft/contract
-- id (`gaia.contract.drafts.mint_draft_id`, shape `"{agent_id}.{token}"`), so
-- the first writer to commit a row for a given contract_id wins and every
-- later write for the SAME contract_id -- a retried `gaia contract finalize`,
-- or (T9) a racing SubagentStop hook backstop -- is a genuine no-op: no
-- duplicate row, no error. See gaia/store/schema.sql's inline comment on the
-- column for the full rationale.
--
-- SQLite ALTER TABLE ADD COLUMN is safe and additive: existing rows receive
-- NULL and the table is NOT rebuilt (https://www.sqlite.org/lang_altertable.html).
-- A NULL contract_id is exempt from the UNIQUE constraint below (SQLite's
-- UNIQUE index permits any number of NULLs), so pre-T7 rows are untouched.
--
-- Idempotency (floor model, replayed on every fresh install): schema.sql
-- already carries this column and index, so on a fresh install this ADD
-- COLUMN targets a column that already exists. bootstrap_database.sh's
-- runner-level guard (_filter_add_column_idempotent) neutralises that exact
-- statement in that case -- no `IF NOT EXISTS` needed in the SQL itself
-- (SQLite has none for ADD COLUMN). `CREATE UNIQUE INDEX IF NOT EXISTS` is
-- idempotent by construction.

ALTER TABLE agent_contract_handoffs ADD COLUMN contract_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
