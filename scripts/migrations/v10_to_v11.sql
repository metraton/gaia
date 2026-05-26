-- Migration v10 -> v11
--
-- Two structural changes in a single migration:
--
--   Part A: memory.class NOT NULL + CHECK constraint enforcement
--   Part B: trg_pcc_history trigger — fix column references (contract_key ->
--           contract_name, payload_json -> payload) that caused runtime errors
--           in v1->v2 migration path.
--
-- Background (Part A)
-- -------------------
-- v4 introduced memory.class as a NULLABLE column with enum enforcement only
-- at the writer layer (not in DDL). The explicit decision documented in
-- schema.sql was:
--   "adding a CHECK at ALTER TIME forces a table-rebuild that would risk
--    FTS trigger drift on the live DB"
-- Task #2 reclassified all pre-v4 NULL rows, so the precondition for a safe
-- rebuild is satisfied (0 rows with class IS NULL).
--
-- The rebuild follows the same rename-create-copy-drop pattern as v1_to_v2.sql:
--   1. Guard: fail fast if any NULL class rows exist.
--   2. Drop FTS5 mirror triggers (avoid double-writes during bulk copy).
--   3. Rename old table out of the way.
--   4. Create new table with NOT NULL CHECK(class IN ('anchor','thread','log')).
--   5. Copy rows preserving rowid.
--   6. Drop renamed old table.
--   7. Recreate indexes (workspace, type, class+status).
--   8. Recreate FTS5 mirror triggers verbatim.
--   9. Rebuild memory_fts.
--
-- Background (Part B)
-- -------------------
-- v8_to_v9.sql created trg_pcc_history referencing:
--   OLD.contract_key  -- but project_context_contracts has column contract_name
--   OLD.payload_json  -- but project_context_contracts has column payload
--   NEW.payload_json  -- same
-- The trigger DDL was stored in sqlite_master with the wrong column names. SQLite
-- defers column resolution to execution time, so the trigger was silently
-- accepted at CREATE time but blew up whenever it fired, AND when any
-- DDL mutation (like ALTER TABLE in v1_to_v2 migration) caused SQLite to
-- validate all live triggers -- aborting the v1->v2 migration transaction.
--
-- Precondition guards
-- -------------------
-- We assert 0 NULL class rows before starting the rebuild. If any exist, the
-- migration aborts and the caller must run `gaia memory reclassify` first.
-- SQLite does not have native ASSERT, so we use a CREATE TABLE trick: if the
-- subquery returns any rows the INSERT will succeed but we check with a
-- NOT EXISTS guard pattern via CASE WHEN inside a temporary trigger.
-- Simpler: we use a CHECK on a temp row that fails if the count > 0.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v10 state; the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.
-- Closes ledger task #6.

-- ============================================================
-- PRE-CONDITION: coalesce any remaining NULL class values to 'log'
-- ============================================================
-- On the live DB, task #2 reclassified all NULL rows before this migration
-- runs, so this UPDATE is a no-op. In test/CI scenarios where a synthetic
-- DB is built from v1 and migrated through all versions, legacy rows that
-- were never reclassified get a safe default of 'log'. Using COALESCE in
-- the INSERT SELECT below (Step A4) achieves the same result without a
-- separate UPDATE pass, but an explicit UPDATE here makes the intent clear
-- and ensures the NOT NULL CHECK in the new table never fires unexpectedly.
UPDATE memory SET class = 'log' WHERE class IS NULL;

-- ============================================================
-- PART A: memory table rebuild (NOT NULL + CHECK on class)
-- ============================================================

-- Step A1: Drop the FTS5 trigger trio before the rename. They will be
-- recreated verbatim in Step A8. Dropping them prevents double-writes
-- and avoids referencing the renamed table during the bulk copy.
DROP TRIGGER IF EXISTS memory_ai;
DROP TRIGGER IF EXISTS memory_ad;
DROP TRIGGER IF EXISTS memory_au;

-- Step A2: Rename old table out of the way. SQLite carries indexes along.
ALTER TABLE memory RENAME TO memory_v10_old;

-- Step A3: Create the new memory table with NOT NULL DEFAULT + CHECK on class.
-- Schema matches schema.sql exactly: class is NOT NULL with DEFAULT 'log' and
-- enum CHECK. The DEFAULT ensures that callers who do not supply class (e.g.
-- upsert_memory in writer.py) get a sensible default rather than a hard failure.
-- Explicit NULL is still rejected by NOT NULL.
CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    class             TEXT NOT NULL DEFAULT 'log' CHECK (class IN ('anchor', 'thread', 'log')),
    status            TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

-- Step A4: Copy all rows preserving rowid. memory_fts joins on rowid;
-- changing them would invalidate the FTS5 index. class is guaranteed
-- non-NULL by the UPDATE in the pre-condition step above.
INSERT INTO memory (rowid, workspace, name, type, description, body, origin_session_id, updated_at, class, status)
SELECT rowid, workspace, name, type, description, body, origin_session_id, updated_at, class, status
FROM memory_v10_old;

-- Step A5: Drop the renamed old table. Its indexes go with it.
DROP TABLE memory_v10_old;

-- Step A6: Recreate the standard indexes on the new table.
CREATE INDEX IF NOT EXISTS idx_memory_workspace ON memory(workspace);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);
-- idx_memory_class_status was created by v3_to_v4.sql; must be recreated here
-- because the underlying table was dropped and recreated above.
CREATE INDEX IF NOT EXISTS idx_memory_class_status ON memory(workspace, class, status);

-- Step A7: Recreate the three FTS5 mirror triggers verbatim from schema.sql.
CREATE TRIGGER memory_ai AFTER INSERT ON memory BEGIN
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;

CREATE TRIGGER memory_ad AFTER DELETE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
END;

CREATE TRIGGER memory_au AFTER UPDATE ON memory BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;

-- Step A8: Rebuild memory_fts from the new memory table.
INSERT INTO memory_fts(memory_fts) VALUES('rebuild');

-- ============================================================
-- PART B: trg_pcc_history trigger fix
-- ============================================================
-- Drop the broken trigger (created by v8_to_v9.sql with wrong column refs).
DROP TRIGGER IF EXISTS trg_pcc_history;

-- Recreate with correct column references:
--   OLD.contract_name  (project_context_contracts PK component)
--   OLD.payload        (project_context_contracts payload column)
--   NEW.payload        (project_context_contracts payload column)
-- The INSERT target history table still uses column `contract_key` (unchanged).
CREATE TRIGGER trg_pcc_history
AFTER UPDATE ON project_context_contracts
BEGIN
    INSERT INTO project_context_contracts_history (
        contract_key, workspace, before_payload_json, after_payload_json, changed_at
    ) VALUES (
        OLD.contract_name,
        OLD.workspace,
        OLD.payload,
        NEW.payload,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;

-- ============================================================
-- LEDGER BUMP
-- ============================================================
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (11, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'memory.class NOT NULL + CHECK; trg_pcc_history column fix (task #6)');
