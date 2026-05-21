-- Migration v2 -> v3 (merge path).
--
-- Runs when the guard probe in bootstrap_database.sh detects "state 3" --
-- both `context_contracts` (legacy v2 table) AND `project_context_contracts`
-- (new v3 table) exist.  This happens when an earlier bootstrap under v3
-- code applied schema.sql (which created the new table with IF NOT EXISTS)
-- but the old table was never dropped because schema.sql no longer declares
-- it and IF NOT EXISTS only creates -- it never drops.
--
-- See v2_to_v3.sql for the rename path (state 1) and for the full discussion
-- of the three entry states.
--
-- Strategy
-- --------
-- The new table is created in v3 shape (column `contract_name`), the old
-- table is in v2 shape (column `section_name`).  The two columns are
-- semantically identical, so we copy rows with column aliasing:
--
--   INSERT INTO project_context_contracts (workspace, contract_name, ...)
--   SELECT workspace, section_name, ...
--   FROM context_contracts
--   WHERE (workspace, section_name) NOT IN (
--       SELECT workspace, contract_name FROM project_context_contracts
--   );
--
-- The NOT IN guard makes the copy idempotent: rows that were already
-- migrated by a prior partial run are skipped instead of duplicating PK
-- conflicts.  Once the copy is complete and verified, the legacy table is
-- dropped along with its index.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.  If
-- the copy or the drop fails, the transaction rolls back, the ledger is
-- NOT stamped, and the next bootstrap retry observes state 3 again.

-- 1. Copy rows from the legacy table into the new one, skipping any whose
--    primary key already exists in the new table.
INSERT INTO project_context_contracts (workspace, contract_name, payload, metadata, updated_at)
SELECT workspace, section_name, payload, metadata, updated_at
FROM context_contracts
WHERE (workspace, section_name) NOT IN (
    SELECT workspace, contract_name FROM project_context_contracts
);

-- 2. Drop the legacy index explicitly.  SQLite drops indexes with their
--    table on DROP TABLE, but being explicit catches the case where the
--    index was created standalone or renamed without us noticing.
DROP INDEX IF EXISTS idx_context_contracts_workspace;

-- 3. Drop the legacy table.  Its rows have already been copied above.
DROP TABLE context_contracts;

-- 4. Ensure the new index exists with the canonical name (in case an
--    earlier partial run created the table but not its index).
CREATE INDEX IF NOT EXISTS idx_project_context_contracts_workspace
    ON project_context_contracts(workspace);

-- 5. Permissions table + index.  IF NOT EXISTS makes this safe whether
--    schema.sql already created them in a previous bootstrap or not.
CREATE TABLE IF NOT EXISTS agent_contract_permissions (
    agent_name    TEXT NOT NULL,
    contract_name TEXT NOT NULL,
    can_read      INTEGER NOT NULL DEFAULT 0,
    can_write     INTEGER NOT NULL DEFAULT 0,
    cloud_scope   TEXT,
    PRIMARY KEY (agent_name, contract_name, cloud_scope)
);

CREATE INDEX IF NOT EXISTS idx_agent_contract_perms_agent
    ON agent_contract_permissions(agent_name);
