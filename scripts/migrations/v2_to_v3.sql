-- Migration v2 -> v3 (rename path).
--
-- Background
-- ----------
-- v2 schema had:
--   context_contracts (workspace, section_name, payload, metadata, updated_at)
--   PK: (workspace, section_name)
--
-- v3 schema has:
--   project_context_contracts (workspace, contract_name, payload, metadata, updated_at)
--   PK: (workspace, contract_name)
--   + new table agent_contract_permissions
--   + new index idx_agent_contract_perms_agent
--
-- The rename of `context_contracts` -> `project_context_contracts` reflects
-- its actual role: rows are project-context contracts, not permission grants.
-- The column rename `section_name` -> `contract_name` aligns the vocabulary
-- with the permission model introduced alongside it.
--
-- Three real-world entry states
-- -----------------------------
-- State 1 (only old):  context_contracts exists, project_context_contracts does NOT.
--                      Typical of a clean v2 install upgrading for the first time.
--                      This script handles state 1 via ALTER TABLE RENAME.
-- State 2 (only new):  project_context_contracts exists, context_contracts does NOT,
--                      agent_contract_permissions exists.  Nothing to do -- the guard
--                      probe in bootstrap_database.sh detects state 2 and stamps the
--                      ledger without invoking this script.
-- State 3 (both):      Both tables exist.  Caused by a previous bootstrap where
--                      schema.sql under v3 was applied (CREATE TABLE IF NOT EXISTS
--                      created the new table) but the legacy context_contracts was
--                      never dropped.  State 3 needs row migration + drop, handled
--                      by v2_to_v3_merge.sql -- not by this script.
--
-- bootstrap_database.sh selects state 1 vs 3 via the guard probe and runs the
-- matching script.  Each script is therefore single-purpose and atomic.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.  A failure
-- mid-flight rolls back to v2 state and the ledger row is NOT inserted, so the
-- next bootstrap retry will see the same pending migration.

-- 1. Rename the table.
ALTER TABLE context_contracts RENAME TO project_context_contracts;

-- 2. Rename the column.
ALTER TABLE project_context_contracts RENAME COLUMN section_name TO contract_name;

-- 3. Index on workspace -- name reflects the new table identity.  The old
--    index moved with the table on RENAME TO, but its name still references
--    the legacy table.  Drop + recreate keeps the index name consistent
--    with the new table identity.
DROP INDEX IF EXISTS idx_context_contracts_workspace;
CREATE INDEX IF NOT EXISTS idx_project_context_contracts_workspace
    ON project_context_contracts(workspace);

-- 4. New permissions table + its index.  IF NOT EXISTS makes the script
--    safe to re-run if a partial earlier attempt already created them.
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
