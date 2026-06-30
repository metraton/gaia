-- Migration v20 -> v21: add hard-terminal 'descoped' status to
-- acceptance_criteria.status.
--
-- WHY a table rebuild (not ALTER): the status column carries a CHECK
-- constraint (status IN ('pending','done','blocked')). SQLite cannot ALTER a
-- CHECK constraint in place -- there is no `ALTER TABLE ... ALTER COLUMN` and
-- no `ALTER TABLE ... DROP/ADD CONSTRAINT`. The supported procedure
-- (https://www.sqlite.org/lang_altertable.html, "Making Other Kinds Of Table
-- Schema Changes") is: create a new table with the desired schema, copy the
-- rows, drop the old table, rename the new one into place. This file follows
-- that procedure to widen the CHECK to
-- ('pending','done','blocked','descoped').
--
-- 'descoped' is a HARD-TERMINAL status: an AC deliberately removed from scope.
-- The runtime transition table (gaia.state.transitions.AC_LIFECYCLE_TRANSITIONS)
-- allows pending->descoped and blocked->descoped, and NO transition out of
-- descoped. The CHECK only enumerates the legal *values*; the no-reopen rule is
-- enforced by the transition table, not by the column constraint.
--
-- IDEMPOTENCY (required by the floor model -- this file is replayed on every
-- fresh install). On a fresh install schema.sql has already produced the v21
-- shape (the CHECK already lists 'descoped'). Re-running this rebuild against a
-- DB that is already at the target shape is harmless: it reconstructs an
-- identical table from identical rows and re-applies the same CHECK. The
-- DROP TABLE + RENAME are deterministic; no data is lost on either path:
--   * DB at v20 (CHECK lacks 'descoped'): rows copied, CHECK widened.
--   * fresh DB at v21 (CHECK already has 'descoped'): rows copied, CHECK is the
--     same -- a no-op in effect.
--
-- FOREIGN KEYS: nothing references acceptance_criteria (it is a child of
-- briefs, not a parent of anything). bootstrap_database.sh runs migrations with
-- SQLite's default foreign_keys=OFF and inside a single BEGIN/COMMIT, so the
-- DROP+RENAME below is safe and cannot orphan any child rows. The FK from
-- acceptance_criteria -> briefs is recreated verbatim on the new table.
--
-- This rebuild does NOT touch the briefs table, so closing/reopening behaviour
-- of briefs is unaffected. Bootstrap Section 3c wraps this whole file in one
-- transaction and stamps schema_version=21 only on success.

CREATE TABLE IF NOT EXISTS acceptance_criteria_v21_new (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id       INTEGER NOT NULL,
    ac_id          TEXT NOT NULL,
    description    TEXT,
    evidence_type  TEXT,
    evidence_shape TEXT,
    artifact_path  TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'done', 'blocked', 'descoped')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE
);

INSERT INTO acceptance_criteria_v21_new
    (id, brief_id, ac_id, description, evidence_type, evidence_shape,
     artifact_path, status)
SELECT
    id, brief_id, ac_id, description, evidence_type, evidence_shape,
    artifact_path, status
FROM acceptance_criteria;

DROP TABLE acceptance_criteria;

ALTER TABLE acceptance_criteria_v21_new RENAME TO acceptance_criteria;

-- The index was dropped together with the old table; recreate it. IF NOT EXISTS
-- keeps this idempotent if the index somehow survives on a replay.
CREATE INDEX IF NOT EXISTS idx_acceptance_criteria_brief
    ON acceptance_criteria(brief_id);
