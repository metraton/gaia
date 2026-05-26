-- Migration v5 -> v6 (evidence three-tier storage: dedicated evidence table).
--
-- Background
-- ----------
-- v5 schema has:
--   acceptance_criteria(id, brief_id, ac_id, description, evidence_type,
--                        evidence_shape, artifact_path, status)
--   milestones(id, brief_id, order_num, name, description, status)
--
-- v6 schema adds the evidence table for structured per-AC evidence storage.
-- Each row is one evidence artifact tied to a brief_id + ac_id pair.
-- Two storage modes:
--   inline: text IS NOT NULL, artifact_path IS NULL  (payload <= 4096 bytes)
--   blob:   text IS NULL,     artifact_path IS NOT NULL (payload on filesystem)
--
-- Design decision: independent migration version
-- ------------------------------------------------
-- Evidence is an independent feature (Plan B). Extending v4_to_v5 with this
-- DDL would have conflated two unrelated concerns in one migration version.
-- The override decision (D4 of Plan A) establishes that each independent
-- feature gets its own migration version -- evidence belongs in v6.
--
-- brief_id FK CASCADE ensures cleanup when a brief is deleted.
-- type CHECK enforces the evidence taxonomy at the DB layer (new table,
-- no rebuild penalty unlike the memory.class situation).
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v5 state and the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.

-- 1. Create the evidence table.
CREATE TABLE IF NOT EXISTS evidence (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id         INTEGER NOT NULL,
    ac_id            TEXT NOT NULL,
    task_id          TEXT,
    type             TEXT NOT NULL CHECK (type IN ('text', 'file', 'command_output', 'url', 'screenshot')),
    text             TEXT,
    artifact_path    TEXT,
    size_bytes       INTEGER,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    created_by_agent TEXT,
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

-- 2. Indexes to support brief-scoped and AC-scoped evidence queries.
CREATE INDEX IF NOT EXISTS idx_evidence_brief ON evidence(brief_id);
CREATE INDEX IF NOT EXISTS idx_evidence_ac ON evidence(brief_id, ac_id);
