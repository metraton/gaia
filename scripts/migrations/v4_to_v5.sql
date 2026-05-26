-- Migration v4 -> v5 (state-machine completion: status columns on AC + milestones).
--
-- Background
-- ----------
-- v4 schema has:
--   acceptance_criteria(id, brief_id, ac_id, description, evidence_type,
--                        evidence_shape, artifact_path)
--   milestones(id, brief_id, order_num, name, description)
--
-- v5 schema adds a status lifecycle column to both tables:
--   * acceptance_criteria.status  -- lifecycle (pending | done | blocked)
--   * milestones.status           -- lifecycle (pending | done | blocked)
--
-- Design decision: CHECK constraint inline on ADD COLUMN
-- -------------------------------------------------------
-- SQLite >= 3.37 supports ADD COLUMN with NOT NULL + DEFAULT + CHECK in a
-- single statement without a full table rebuild. The bootstrap environment
-- is confirmed at SQLite 3.45.1, so this is safe.
--
-- The enum values are intentionally small: pending (default), done (AC
-- satisfied / milestone reached), blocked (cannot progress, needs action).
-- They mirror the task lifecycle (pending|done|skipped) but substitute
-- 'blocked' for 'skipped' to distinguish an actively-stuck entity from one
-- that was explicitly bypassed.
--
-- D2 (backfill): explicit UPDATE after ALTER ensures pre-existing rows
-- carry 'pending'. The NOT NULL DEFAULT 'pending' covers new rows; the
-- UPDATE covers rows inserted before this migration.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v4 state and the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.

-- 1. Add status column to acceptance_criteria.
ALTER TABLE acceptance_criteria
    ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'done', 'blocked'));

-- 2. Backfill existing AC rows (D2: explicit UPDATE, all rows -> 'pending').
UPDATE acceptance_criteria SET status = 'pending' WHERE status IS NULL;

-- 3. Add status column to milestones.
ALTER TABLE milestones
    ADD COLUMN status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'done', 'blocked'));

-- 4. Backfill existing milestone rows (D2).
UPDATE milestones SET status = 'pending' WHERE status IS NULL;

-- 5. Indexes to support brief-scoped status queries efficiently.
CREATE INDEX IF NOT EXISTS idx_ac_brief_status
    ON acceptance_criteria(brief_id, status);

CREATE INDEX IF NOT EXISTS idx_milestones_brief_status
    ON milestones(brief_id, status);
