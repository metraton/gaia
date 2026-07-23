-- Migration v36 -> v37: born-at-dispatch foundation on agent_contract_handoffs
-- (plan 34 / brief 114, task 3 "cimiento del rediseno del contrato").
--
-- WHAT CHANGES
--   1. Binding section (born-at-dispatch coordinates), all NULLABLE:
--        plan_task_id      -> FK tasks.id  (NOT named task_id: that name already
--                             denotes the harness agent id in task_info["task_id"]
--                             -- plan 34 A1/F6). This is the plan-task binding.
--        plan_id           -> FK plans.id
--        parent_handoff_id -> FK agent_contract_handoffs.id (verifier -> producer)
--        kind              -> pure dispatch label, no CHECK (plan 34 S3)
--      (brief_id, the fifth coordinate, already existed.)
--   2. Turn-state column task_status is RENAMED to agent_state.
--   3. The agent_state CHECK gains DISPATCHED (the born-at-dispatch ROW state)
--      alongside the six envelope verdicts. DISPATCHED is a ROW state ONLY, never
--      an envelope plan_status value (plan 34 F9): episodes.plan_status is NOT
--      touched by this migration.
--   4. Legacy rows are backfilled: their previous task_status value is carried
--      verbatim into agent_state (the six verdicts are a subset of the new CHECK,
--      so every legacy row remains valid); the binding columns stay NULL.
--   5. contract_id UNIQUE + every existing index are preserved across the rebuild.
--
-- WHY a table rebuild (not a bare ALTER): agent_state carries a CHECK and
-- SQLite cannot ALTER a CHECK in place (no ALTER TABLE ... ALTER COLUMN / no
-- ADD/DROP CONSTRAINT). The supported procedure
-- (https://www.sqlite.org/lang_altertable.html, "Making Other Kinds Of Table
-- Schema Changes") is create-new / copy / drop-old / rename, exactly as
-- v34_to_v35.sql and v35_to_v36.sql already do for this and the neighbouring
-- table. agent_contract_handoffs.id is a true INTEGER PRIMARY KEY (a rowid
-- alias), so copying it as an ordinary column preserves its value -- which
-- matters because agent_contract_handoff_approvals.handoff_id references it by
-- value, and (new in v37) parent_handoff_id self-references the same id space.
-- The table is NOT an FTS5 external-content source (only episodes is), so no
-- shadow-index / trigger correlation is needed here.
--
-- IDEMPOTENCY ACROSS THE RENAME (the hard part -- required by the floor model,
-- since this file is REPLAYED on every fresh install). A CHECK-widening rebuild
-- like v34_to_v35 stays idempotent trivially because it SELECTs a column whose
-- NAME is unchanged. A column RENAME cannot: on an existing v36 DB the source
-- column is `task_status`, but on a fresh install schema.sql has ALREADY
-- produced the v37 shape, so the source column is `agent_state` and there is no
-- `task_status` to SELECT. Referencing either name unconditionally aborts on the
-- other path.
--
-- The fix leans on the bootstrap runner's ADD COLUMN idempotency guard
-- (_filter_add_column_idempotent in bootstrap_database.{sh,py}): a bare
-- `ALTER TABLE t ADD COLUMN c` line is NEUTRALISED (commented out) when column c
-- already exists on t, and applied otherwise. So the six ADD COLUMN lines below
-- converge BOTH shapes to a superset that carries every column the rebuild's
-- SELECT names -- exactly the missing ones are added, the present ones skipped:
--   * on a v36 DB: agent_state + the four binding columns are added (NULL);
--     task_status is skipped (already present, real values kept).
--   * on a fresh v37 DB: task_status is added (NULL); agent_state + binding are
--     skipped (already present, real values kept).
-- The rebuild then backfills agent_state = COALESCE(agent_state, task_status):
-- on v36 that resolves to the legacy task_status; on v37 to the existing
-- agent_state. The DROP+RENAME discards whichever transient column was added
-- (the stray task_status on v37, the stray NULL agent_state placeholder on v36
-- is superseded by the COALESCE), leaving an identical, clean v37 table on both
-- paths. A standalone test (tests/cli/test_migration_v36_to_v37.py) applies this
-- file through the SAME _filter_add_column_idempotent guard against a v36 copy
-- AND asserts the fresh-v37 replay is a harmless no-op.
--
-- FOREIGN KEYS: bootstrap runs migrations with SQLite's default foreign_keys=OFF
-- inside a single BEGIN/COMMIT, so the DROP+RENAME cannot orphan any child rows
-- and the new binding FKs are not validated during the rebuild.

-- Step 1: converge both shapes. Each ALTER is neutralised by the runner guard
-- when its column already exists (one `ALTER TABLE ... ADD COLUMN ...` per line,
-- as the guard's matcher requires). Order is irrelevant -- the guard is per-line.
ALTER TABLE agent_contract_handoffs ADD COLUMN agent_state TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN task_status TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN plan_task_id INTEGER;
ALTER TABLE agent_contract_handoffs ADD COLUMN plan_id INTEGER;
ALTER TABLE agent_contract_handoffs ADD COLUMN parent_handoff_id INTEGER;
ALTER TABLE agent_contract_handoffs ADD COLUMN kind TEXT;

-- Step 2: rebuild into the canonical v37 shape.
CREATE TABLE IF NOT EXISTS agent_contract_handoffs_v37_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id       TEXT,
    agent_id          TEXT NOT NULL,
    session_id        TEXT,
    workspace         TEXT NOT NULL,
    brief_id          INTEGER,
    plan_task_id      INTEGER,
    plan_id           INTEGER,
    parent_handoff_id INTEGER,
    kind              TEXT,
    agent_state       TEXT NOT NULL
                      CHECK (agent_state IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT', 'NEEDS_VERIFICATION', 'DISPATCHED')),
    raw_handoff_json  TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace)         REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (brief_id)          REFERENCES briefs(id),
    FOREIGN KEY (plan_task_id)      REFERENCES tasks(id),
    FOREIGN KEY (plan_id)           REFERENCES plans(id),
    FOREIGN KEY (parent_handoff_id) REFERENCES agent_contract_handoffs(id)
);

INSERT INTO agent_contract_handoffs_v37_new
    (id, contract_id, agent_id, session_id, workspace, brief_id,
     plan_task_id, plan_id, parent_handoff_id, kind,
     agent_state, raw_handoff_json, created_at)
SELECT
    id, contract_id, agent_id, session_id, workspace, brief_id,
    plan_task_id, plan_id, parent_handoff_id, kind,
    -- Backfill / map the legacy turn state: on a v36 DB agent_state was just
    -- added NULL and task_status holds the real value; on a fresh v37 DB it is
    -- the reverse. COALESCE picks the real one on both paths.
    COALESCE(agent_state, task_status),
    raw_handoff_json, created_at
FROM agent_contract_handoffs;

DROP TABLE agent_contract_handoffs;

ALTER TABLE agent_contract_handoffs_v37_new RENAME TO agent_contract_handoffs;

-- Step 3: recreate the indexes the DROP removed with the old table. IF NOT
-- EXISTS keeps this idempotent if any somehow survive a replay. The UNIQUE
-- index on contract_id is what makes finalize's ON CONFLICT(contract_id) DO
-- NOTHING a real constraint-backed idempotent UPSERT -- preserved verbatim.
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
