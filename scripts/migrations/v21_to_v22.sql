-- Migration v21 -> v22: clean invalid agent_contract_handoffs.task_status
-- rows and add a CHECK constraint enumerating the legal plan_status values.
--
-- WHY a table rebuild (not ALTER): task_status has no CHECK today, so
-- SQLite cannot ADD one in place -- there is no `ALTER TABLE ... ADD
-- CONSTRAINT`. The supported procedure
-- (https://www.sqlite.org/lang_altertable.html, "Making Other Kinds Of
-- Table Schema Changes") is: create a new table with the desired schema,
-- copy the surviving rows, drop the old table, rename the new one into
-- place. This file follows that same procedure used by v20_to_v21.sql for
-- acceptance_criteria.status.
--
-- DATA CLEANUP FIRST: 37 rows in production carried task_status =
-- 'RANDOM_STATUS', an invalid value with no CHECK to reject it. Investigation
-- (session_id LIKE 'e2e-sim-%', a fixed test agent_id 'a5e6f7', workspace
-- 'global', no brief_id, and fabricated raw_handoff_json content such as a
-- canned "OOMKilled" pod diagnosis) confirms these are e2e-simulation-harness
-- probe rows that deliberately exercised the persistence path with an
-- out-of-enum value -- not real agent outcomes. There is no legitimate
-- plan_status to normalize them to, so they are deleted rather than
-- relabeled (relabeling would fabricate history for rows that never carried
-- a real status). The DELETE is scoped to the exact invalid value, so it is
-- a no-op (0 rows) on any DB where the value has already been cleaned or
-- never existed.
--
-- CHECK enum: mirrors episodes.plan_status exactly (see gaia/store/schema.sql
-- CHECK (plan_status IS NULL OR plan_status IN (...))) and the canonical
-- plan_status enum documented by the agent-protocol / agent-contract-handoff
-- skills. handoff_persister.py writes
-- envelope["agent_status"]["plan_status"] verbatim into task_status (falling
-- back to 'COMPLETE' when absent), so the two enums must never drift apart.
--
-- IDEMPOTENCY (required by the floor model -- this file is replayed on every
-- fresh install). On a fresh install schema.sql has already produced the v22
-- shape (the CHECK already exists and no RANDOM_STATUS rows exist). Re-running
-- this rebuild against a DB already at the target shape is harmless: the
-- DELETE matches 0 rows, and the rebuild reconstructs an identical table from
-- identical rows with the same CHECK.
--
-- FOREIGN KEYS: agent_contract_handoff_approvals.handoff_id references
-- agent_contract_handoffs(id) but carries no explicit CASCADE that requires
-- ordering here -- bootstrap_database.sh runs migrations with SQLite's
-- default foreign_keys=OFF inside a single BEGIN/COMMIT, so the DROP+RENAME
-- below cannot orphan any child rows. None of the 37 deleted rows have a
-- matching agent_contract_handoff_approvals row (verified prior to writing
-- this migration), so no child cleanup is needed either way.

-- v37 REPLAY-SAFETY: from v37 on, schema.sql produces the table with the
-- RENAMED column agent_state (task_status was renamed by v36_to_v37.sql).
-- On a fresh install schema.sql builds the v37 shape and THEN every forward
-- migration is replayed, so this v22-era rebuild would reference a task_status
-- column that no longer exists and abort ("no such column: task_status").
-- Defensively (re)add it: the bootstrap runner's ADD COLUMN idempotency guard
-- (_filter_add_column_idempotent) NEUTRALISES this line when task_status
-- already exists (the genuine v21->v22 upgrade path, where it holds real data),
-- and applies it only on the fresh-install v37 shape -- where the handoffs
-- table is EMPTY during migration replay, so the column is added NULL and the
-- rebuild below copies zero rows. Data on the genuine path is untouched.
ALTER TABLE agent_contract_handoffs ADD COLUMN task_status TEXT;

DELETE FROM agent_contract_handoffs WHERE task_status = 'RANDOM_STATUS';

CREATE TABLE IF NOT EXISTS agent_contract_handoffs_v22_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,
    workspace        TEXT NOT NULL,
    brief_id         INTEGER,
    task_status      TEXT NOT NULL
                     CHECK (task_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT')),
    raw_handoff_json TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name),
    FOREIGN KEY (brief_id)  REFERENCES briefs(id)
);

INSERT INTO agent_contract_handoffs_v22_new
    (id, agent_id, session_id, workspace, brief_id, task_status,
     raw_handoff_json, created_at)
SELECT
    id, agent_id, session_id, workspace, brief_id, task_status,
    raw_handoff_json, created_at
FROM agent_contract_handoffs;

DROP TABLE agent_contract_handoffs;

ALTER TABLE agent_contract_handoffs_v22_new RENAME TO agent_contract_handoffs;

-- Indexes were dropped together with the old table; recreate them. IF NOT
-- EXISTS keeps this idempotent if they somehow survive on a replay.
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
