-- Migration v25 -> v26: scan-v2 SV3 memory-resilience foundation.
--
-- SV3 blinds the four memory-loss vectors identified in the decision
-- `decision_scan_v2_memory_loss_vectors`, respecting the invariant "never
-- hard-delete curated memory except by explicit human curation". This
-- migration adds the two DDL structures those guarantees rest on and backfills
-- the remote-stable project anchor (memory.project_ref) that SV1 added the
-- column for:
--
--   1. `memory.deleted_at` (nullable TEXT): tombstone marker. delete_memory()
--      now soft-deletes by stamping this column instead of issuing a physical
--      DELETE, so the row and its body survive; every read path filters
--      `deleted_at IS NULL`. Hard DELETE is reserved for explicit human
--      curation (delete_memory(hard=True)).
--   2. `memory_history` table + `trg_memory_history` trigger: before/after
--      audit trail for `memory` mutations, mirroring the existing
--      project_context_contracts_history / trg_pcc_history and
--      project_history / trg_project_history patterns (see
--      gaia/store/schema.sql). The single trigger covers three vectors at the
--      SQL layer (so no code path can bypass it):
--        * archive-on-upsert  -- upsert's ON CONFLICT DO UPDATE archives the
--          previous body under before_body before overwriting it;
--        * tombstone-on-delete -- the deleted_at UPDATE lands a history row;
--        * relocate origin trace -- a workspace re-key records
--          before_workspace -> after_workspace.
--   3. project_ref backfill: for type='project' memory rows whose workspace
--      hosts EXACTLY ONE active project carrying a project_identity, anchor the
--      memory to that identity (remote-stable). Ambiguous workspaces (0 or >1
--      such projects) are left NULL -- never guessed. Idempotent: only rows
--      with project_ref IS NULL are touched, so the fresh-install replay and
--      any re-run are no-ops.
--
-- The DDL mirrors gaia/store/schema.sql (floor model: schema.sql already
-- carries these objects, so on a fresh install the CREATE statements below
-- target objects that already exist). CREATE TABLE / CREATE INDEX / CREATE
-- TRIGGER IF NOT EXISTS are idempotent by construction; the ADD COLUMN
-- statement is neutralised by bootstrap_database.sh's runner-level idempotency
-- guard (_filter_add_column_idempotent) when the column already exists, per the
-- floor-model replay convention (see scripts/migrations/README.md section 1) --
-- no `IF NOT EXISTS` needed in the SQL itself (SQLite has none for ADD COLUMN).

ALTER TABLE memory ADD COLUMN deleted_at TEXT;

CREATE TABLE IF NOT EXISTS memory_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace          TEXT NOT NULL,
    name               TEXT NOT NULL,
    before_workspace   TEXT,
    after_workspace    TEXT,
    before_body        TEXT,
    after_body         TEXT,
    before_type        TEXT,
    after_type         TEXT,
    before_description TEXT,
    after_description  TEXT,
    before_status      TEXT,
    after_status       TEXT,
    before_deleted_at  TEXT,
    after_deleted_at   TEXT,
    changed_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent   TEXT,
    FOREIGN KEY (workspace) REFERENCES workspaces(name)
);

CREATE INDEX IF NOT EXISTS idx_memory_history_workspace_name ON memory_history(workspace, name);

CREATE TRIGGER IF NOT EXISTS trg_memory_history
AFTER UPDATE ON memory
WHEN OLD.body IS NOT NEW.body
   OR OLD.workspace IS NOT NEW.workspace
   OR OLD.type IS NOT NEW.type
   OR OLD.description IS NOT NEW.description
   OR OLD.status IS NOT NEW.status
   OR OLD.deleted_at IS NOT NEW.deleted_at
BEGIN
    INSERT INTO memory_history (
        workspace, name,
        before_workspace, after_workspace,
        before_body, after_body,
        before_type, after_type,
        before_description, after_description,
        before_status, after_status,
        before_deleted_at, after_deleted_at,
        changed_at
    ) VALUES (
        NEW.workspace, NEW.name,
        OLD.workspace, NEW.workspace,
        OLD.body, NEW.body,
        OLD.type, NEW.type,
        OLD.description, NEW.description,
        OLD.status, NEW.status,
        OLD.deleted_at, NEW.deleted_at,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;

-- project_ref backfill (see header note 3). Anchors type='project' memory to
-- the sole active project_identity in its workspace when unambiguous; leaves
-- ambiguous workspaces NULL. Guarded on `project_ref IS NULL` for idempotency.
UPDATE memory
SET project_ref = (
    SELECT p.project_identity
    FROM projects p
    WHERE p.workspace = memory.workspace
      AND p.project_identity IS NOT NULL
      AND p.status = 'active'
)
WHERE type = 'project'
  AND project_ref IS NULL
  AND (
    SELECT COUNT(*)
    FROM projects p2
    WHERE p2.workspace = memory.workspace
      AND p2.project_identity IS NOT NULL
      AND p2.status = 'active'
  ) = 1;
