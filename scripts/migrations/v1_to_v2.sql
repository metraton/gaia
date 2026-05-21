-- Migration v1 -> v2: widen memory.type CHECK constraint.
--
-- Background
-- ----------
-- v1 schema: memory.type CHECK (type IN ('project', 'user', 'feedback'))
-- v2 schema: memory.type CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative'))
--
-- The bug this migration fixes
-- ----------------------------
-- bootstrap_database.sh applies schema.sql with `CREATE TABLE IF NOT EXISTS`.
-- On DBs created under v1 the CREATE short-circuits and the widened CHECK in
-- schema.sql never lands -- yet the bootstrap historically stamped the
-- schema_version ledger row for v2 unconditionally. Result: the ledger lied
-- and writes of the new types ('atom', 'decision', 'negative') failed at
-- runtime with CHECK constraint errors while `gaia doctor` reported OK.
--
-- Pattern: SQLite cannot ALTER a CHECK constraint. Canonical workaround:
--   1. Drop the FTS5 mirror triggers (avoid duplicate writes during copy).
--   2. Rename the old table out of the way.
--   3. Create the new table with the widened CHECK matching schema.sql.
--   4. Copy rows preserving rowid (memory_fts joins on rowid).
--   5. Drop the renamed old table.
--   6. Recreate the indexes on the new table.
--   7. Recreate the FTS5 mirror triggers verbatim.
--   8. Rebuild memory_fts from the new memory table.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT. A
-- failure mid-flight rolls back to the v1 state and the ledger row is NOT
-- inserted -- the next bootstrap retry will see the same pending migration.
--
-- Pre-conditions: caller has verified that the live memory.type CHECK does
-- NOT yet include 'atom'. Applying this on an already-widened table would
-- still succeed (the rename-create-copy ends in the same state) but the
-- caller skips it to avoid unnecessary work.

-- 1. Drop the FTS5 trigger trio. They mirror writes into memory_fts; we do
--    not want them to fire during the bulk copy below. memory_fts itself is
--    untouched here -- we will rebuild it after the copy completes.
DROP TRIGGER IF EXISTS memory_ai;
DROP TRIGGER IF EXISTS memory_ad;
DROP TRIGGER IF EXISTS memory_au;

-- 2. Rename the old table out of the way. The indexes (idx_memory_project,
--    idx_memory_workspace, idx_memory_type) move with the table under their
--    original names -- SQLite ALTER TABLE RENAME carries indexes along.
ALTER TABLE memory RENAME TO memory_v1_legacy;

-- 3. Create the new memory table with the widened CHECK constraint that
--    matches schema.sql verbatim (6 allowed types).
CREATE TABLE memory (
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL CHECK (type IN ('project', 'user', 'feedback', 'atom', 'decision', 'negative')),
    description       TEXT,
    body              TEXT NOT NULL,
    origin_session_id TEXT,
    updated_at        TEXT,
    PRIMARY KEY (workspace, name),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

-- 4. Copy all rows preserving rowid. This is critical: memory_fts joins on
--    rowid, so changing them would invalidate the FTS5 index. We list rowid
--    explicitly to bypass SQLite's default rowid allocation.
INSERT INTO memory (rowid, workspace, name, type, description, body, origin_session_id, updated_at)
SELECT rowid, workspace, name, type, description, body, origin_session_id, updated_at
FROM memory_v1_legacy;

-- 5. Drop the renamed old table. Its indexes go with it.
DROP TABLE memory_v1_legacy;

-- 6. Recreate the indexes on the new table. Same names as schema.sql.
CREATE INDEX IF NOT EXISTS idx_memory_workspace ON memory(workspace);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory(type);

-- 7. Recreate the three FTS5 mirror triggers verbatim from schema.sql.
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

-- 8. Rebuild memory_fts from the new memory table. FTS5's native reindex:
--    clears the internal state and rescans the backing table. Cleaner than
--    DELETE + INSERT loops.
INSERT INTO memory_fts(memory_fts) VALUES('rebuild');
