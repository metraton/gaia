-- Migration v35 -> v36: add a DB CHECK constraint on task_gates.status,
-- closing the documented asymmetry from gaia.state.VALID_GATE_STATUSES /
-- STATE_MACHINE_REGISTRY -- the ("task_gates", "status") entry previously had
-- no matching SQL CHECK (see gaia/store/schema.sql and gaia/state/__init__.py
-- for the pre-v36 rationale), so tools/state/diff_source_of_truth.py reported
-- it as permanently divergent. This migration retrofits the CHECK so the
-- Python tuple and the DB constraint are held identical like every other
-- registry entry.
--
-- WHY a table rebuild (not ALTER): task_gates.status has no CHECK today, and
-- SQLite cannot ALTER a column to ADD a CHECK constraint in place -- there is
-- no `ALTER TABLE ... ADD CONSTRAINT`. The supported procedure
-- (https://www.sqlite.org/lang_altertable.html, "Making Other Kinds Of Table
-- Schema Changes") is: create a new table with the desired schema, copy the
-- rows, drop the old table, rename the new one into place. This file follows
-- the exact same procedure used by v34_to_v35.sql (episodes.plan_status,
-- agent_contract_handoffs.task_status) and, before that, v20_to_v21.sql /
-- v21_to_v22.sql.
--
-- ROWID / ID PRESERVATION: task_gates.id is `INTEGER PRIMARY KEY
-- AUTOINCREMENT` -- a true rowid alias, exactly like
-- agent_contract_handoffs.id in v34_to_v35.sql. Copying it as an ordinary
-- column (as this migration does) preserves its value the same way; no
-- separate `rowid` pseudo-column handling is needed (that handling in
-- v34_to_v35.sql was specific to episodes, whose PRIMARY KEY is a TEXT
-- episode_id and which is the content table of an external-content FTS5
-- index keyed on rowid). task_gates carries no FTS5 shadow table and no
-- triggers (verified against gaia/store/schema.sql and gaia/store/writer.py
-- before writing this migration), so there is nothing else to keep
-- correlated across the rebuild.
--
-- FOREIGN KEY: task_gates.task_id references tasks(id) ON DELETE CASCADE.
-- Preserved verbatim on the rebuilt table. bootstrap_database.sh runs
-- migrations with SQLite's default foreign_keys=OFF inside a single
-- BEGIN/COMMIT, so the DROP+RENAME below cannot orphan any child/parent rows.
--
-- IDEMPOTENCY (required by the floor model -- this file is replayed on every
-- fresh install). On a fresh install schema.sql has already produced the v36
-- shape (the CHECK already present). Re-running this rebuild against a DB
-- already at the target shape is harmless: it reconstructs an identical
-- table from identical rows (ids included) and re-applies the same CHECK and
-- index.

CREATE TABLE IF NOT EXISTS task_gates_v36_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER NOT NULL,
    verification_type TEXT NOT NULL
                      CHECK (verification_type IN ('command', 'code', 'semantic', 'self_review')),
    evidence_type     TEXT,
    evidence_shape    TEXT,
    artifact_path     TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'pass', 'fail')),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

INSERT INTO task_gates_v36_new
    (id, task_id, verification_type, evidence_type, evidence_shape,
     artifact_path, status)
SELECT
    id, task_id, verification_type, evidence_type, evidence_shape,
    artifact_path, status
FROM task_gates;

DROP TABLE task_gates;

ALTER TABLE task_gates_v36_new RENAME TO task_gates;

-- Index was dropped together with the old table; recreate it. IF NOT EXISTS
-- keeps this idempotent if it somehow survives on a replay.
CREATE INDEX IF NOT EXISTS idx_task_gates_task ON task_gates(task_id);
