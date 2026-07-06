-- Migration v24 -> v25: scan-v2 SV1 schema foundation -- provenance +
-- memory-de-project anchor.
--
-- Adds the structures scan-v2's redesign needs before move-detection and
-- de-project memory anchoring (SV3/SV4) can be built on top of them:
--
--   1. `project_history` table + `trg_project_history` trigger: captures the
--      lineage of a `projects` row (path/workspace/name/status changes) at
--      the SQL layer, mirroring the existing project_context_contracts_history
--      / trg_pcc_history pattern (see gaia/store/schema.sql). This gives a
--      connected timeline for both "moved" (path/workspace/name changed) and
--      "soft-deleted" (status -> 'missing') without the scanner writing
--      history explicitly.
--   2. `projects.superseded_by` (nullable TEXT): will point to the successor
--      project_identity after a 'movido' adjudication. Written in SV4; only
--      the column is added here.
--   3. `memory.project_ref` (nullable TEXT): remote-stable anchor for
--      de-project memory. Populated/used in SV3; only the column is added
--      here. Verified not to break memory_fts (its triggers use an explicit
--      column list, not `SELECT *`) or the context-injection reader (uses
--      explicit column lists -- see gaia/store/writer.py).
--
-- The DDL mirrors gaia/store/schema.sql (floor model: schema.sql already
-- carries these objects, so on a fresh install the CREATE/ALTER statements
-- below target objects/columns that already exist). CREATE TABLE / CREATE
-- INDEX / CREATE TRIGGER IF NOT EXISTS are idempotent by construction; the
-- two ADD COLUMN statements are neutralised by bootstrap_database.sh's
-- runner-level idempotency guard (_filter_add_column_idempotent) when the
-- column already exists, per the floor-model replay convention (see
-- scripts/migrations/README.md section 1) -- no `IF NOT EXISTS` needed in
-- the SQL itself (SQLite has none for ADD COLUMN).

ALTER TABLE projects ADD COLUMN superseded_by TEXT;

ALTER TABLE memory ADD COLUMN project_ref TEXT;

CREATE TABLE IF NOT EXISTS project_history (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace         TEXT NOT NULL,
    name              TEXT NOT NULL,
    before_path       TEXT,
    after_path        TEXT,
    before_workspace  TEXT,
    after_workspace   TEXT,
    before_name       TEXT,
    after_name        TEXT,
    before_status     TEXT,
    after_status      TEXT,
    changed_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name)
);

CREATE INDEX IF NOT EXISTS idx_project_history_workspace_name ON project_history(workspace, name);

CREATE TRIGGER IF NOT EXISTS trg_project_history
AFTER UPDATE ON projects
WHEN OLD.path IS NOT NEW.path
   OR OLD.workspace IS NOT NEW.workspace
   OR OLD.name IS NOT NEW.name
   OR OLD.status IS NOT NEW.status
BEGIN
    INSERT INTO project_history (
        workspace, name,
        before_path, after_path,
        before_workspace, after_workspace,
        before_name, after_name,
        before_status, after_status,
        changed_at
    ) VALUES (
        NEW.workspace, NEW.name,
        OLD.path, NEW.path,
        OLD.workspace, NEW.workspace,
        OLD.name, NEW.name,
        OLD.status, NEW.status,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;
