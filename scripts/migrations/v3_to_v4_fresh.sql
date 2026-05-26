-- v3 -> v4 fresh-install variant.
--
-- Used by bootstrap_database.sh Section 3c case 4 when the live DDL is
-- already at the v4 target state (memory.class column present, memory_links
-- table present). This happens on a clean install where schema.sql already
-- created the v4 column layout and the memory_links table.
--
-- The default v3_to_v4.sql cannot run here because ALTER TABLE ADD COLUMN
-- fails when the column already exists. This variant carries only the DDL
-- that schema.sql cannot declare safely:
--   * idx_memory_class_status -- references columns added at ALTER time,
--     so schema.sql cannot pre-declare it (the replay on v3 DBs would parse-
--     fail before the migration ran).
--
-- Everything else (memory_links table, memory_links indexes) is declared in
-- schema.sql and is therefore already present on a fresh install. CREATE
-- INDEX IF NOT EXISTS makes this script safe to re-run.

CREATE INDEX IF NOT EXISTS idx_memory_class_status
    ON memory(workspace, class, status);
