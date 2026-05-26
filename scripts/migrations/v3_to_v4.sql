-- Migration v3 -> v4 (memory model: class + status + memory_links).
--
-- Background
-- ----------
-- v3 schema had:
--   memory(workspace, name, type, description, body, origin_session_id, updated_at)
--   PK: (workspace, name)
--   type CHECK constraint: ('project','user','feedback','atom','decision','negative')
--
-- v4 schema adds two ortogonal axes to the memory model:
--   * class  -- semantic role (anchor | thread | log), nullable for legacy rows.
--   * status -- lifecycle for class=thread (open | carry_forward | graduated | closed).
--
-- Plus a new table:
--   memory_links(workspace, src_name, dst_name, kind, created_at)
--   PK: (workspace, src_name, dst_name, kind)
--   kind CHECK: ('relates_to','supersedes','derived_from','graduated_to')
--
-- Design decision: NO CHECK constraint on memory.class / memory.status
-- ---------------------------------------------------------------------
-- SQLite's ALTER TABLE ADD COLUMN cannot attach a CHECK that depends on
-- the new column's enum values. The standard workaround is a full table
-- rebuild (CREATE new, INSERT SELECT, DROP old, RENAME), which would:
--   1. Force re-creation of memory_fts triggers (drift risk on live DBs).
--   2. Move all existing memory rows through a copy step, complicating the
--      AC-2 contract that "the 36 me-workspace rows survive intact" with
--      byte-identical bodies.
--   3. Add complexity disproportionate to the gain -- the writer layer
--      validates the enum on every upsert in T3 anyway.
--
-- Therefore: schema declares class/status as plain TEXT nullable columns.
-- Enum enforcement is the writer's job (gaia/store/writer.py, T3).
-- memory_links.kind keeps its CHECK because it is a brand-new table with
-- no rebuild cost.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT. A
-- failure mid-flight rolls back to v3 state and the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.

-- 1. Add the two nullable columns to memory. Existing rows get NULL,
--    which is the documented "legacy row" state and is what T8/T9 will
--    reclassify interactively.
ALTER TABLE memory ADD COLUMN class TEXT;
ALTER TABLE memory ADD COLUMN status TEXT;

-- 2. Index on (workspace, class, status) -- supports the injector path
--    that picks class=thread/status=carry_forward first.
CREATE INDEX IF NOT EXISTS idx_memory_class_status
    ON memory(workspace, class, status);

-- 3. New table memory_links + indexes. IF NOT EXISTS makes the script
--    idempotent if a partial earlier attempt already created them.
CREATE TABLE IF NOT EXISTS memory_links (
    workspace  TEXT NOT NULL,
    src_name   TEXT NOT NULL,
    dst_name   TEXT NOT NULL,
    kind       TEXT NOT NULL CHECK (kind IN ('relates_to', 'supersedes', 'derived_from', 'graduated_to')),
    created_at TEXT,
    PRIMARY KEY (workspace, src_name, dst_name, kind),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS memory_links_src
    ON memory_links(workspace, src_name);

CREATE INDEX IF NOT EXISTS idx_memory_links_dst_kind
    ON memory_links(workspace, dst_name, kind);
