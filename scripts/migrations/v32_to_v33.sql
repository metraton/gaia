-- Migration v32 -> v33: add ON DELETE CASCADE to the workspace FK on four
-- audit-trail tables (memory_history, agent_contract_handoffs,
-- project_context_contracts_history, project_history).
--
-- WHY a table rebuild (not ALTER): SQLite cannot add/modify a FOREIGN KEY
-- clause in place -- there is no `ALTER TABLE ... ALTER COLUMN` and no
-- `ALTER TABLE ... DROP/ADD CONSTRAINT`. The supported procedure
-- (https://www.sqlite.org/lang_altertable.html, "Making Other Kinds Of Table
-- Schema Changes") is: create a new table with the desired schema, copy the
-- rows, drop the old table, rename the new one into place. This file follows
-- that procedure for all four tables.
--
-- BUG this closes: `prune_empty_workspaces()` (gaia/store/writer.py) does
-- `DELETE FROM workspaces WHERE name = ?` for a workspace confirmed to hold
-- no CURATED collateral (no live memory, no project_context_contracts, no
-- briefs). Under `foreign_keys=ON` (set by every writer._connect call), that
-- DELETE also has to satisfy the FK on these four AUDIT-TRAIL tables, which
-- until now referenced workspaces(name) WITHOUT CASCADE. A workspace that is
-- clean of curated content can still carry residual audit rows here (e.g. a
-- memory_history row from a memory that was later hard-deleted, or an
-- agent_contract_handoffs row from a past agent run) -- the DELETE then
-- raises a FOREIGN KEY constraint failed error and the whole prune
-- transaction rolls back. CASCADE makes these tables behave like what they
-- are: audit trail of a workspace's history, not curated content of their
-- own -- they are meant to disappear with the workspace they describe.
--
-- IDEMPOTENCY (required by the floor model -- this file is replayed on every
-- fresh install). On a fresh install schema.sql has already produced the v33
-- shape (the FK already carries ON DELETE CASCADE). Re-running this rebuild
-- against a DB already at the target shape is harmless: it reconstructs an
-- identical table from identical rows and re-applies the same (now-CASCADE)
-- FK. The DROP TABLE + RENAME are deterministic; no data is lost on either
-- path:
--   * DB at v32 (FK lacks CASCADE): rows copied, FK gains ON DELETE CASCADE.
--   * fresh DB at v33 (FK already CASCADE): rows copied, FK is the same --
--     a no-op in effect.
--
-- FOREIGN KEYS / CHILDREN: bootstrap_database.sh runs migrations with
-- SQLite's default `foreign_keys=OFF` inside a single BEGIN/COMMIT, so the
-- DROP+RENAME below never triggers a cascade delete and cannot orphan any
-- child rows. `agent_contract_handoff_approvals` references
-- `agent_contract_handoffs(id)` -- the rebuild below preserves `id` values
-- verbatim (explicit column list, AUTOINCREMENT PK re-declared identically),
-- so that child FK stays valid across the rebuild. None of the other three
-- tables has a table that references it.
--
-- TRIGGERS: `trg_memory_history`, `trg_pcc_history`, and `trg_project_history`
-- are defined ON `memory` / `project_context_contracts` / `projects`
-- respectively (they INSERT INTO the audit table on that source table's
-- AFTER UPDATE) -- not ON the audit tables themselves. Dropping and
-- recreating the audit tables below does not remove those triggers, but it
-- DOES require `PRAGMA legacy_alter_table = ON` below: since SQLite 3.25,
-- `ALTER TABLE ... RENAME TO` eagerly re-validates every trigger/view in the
-- schema to fix up any reference to the renamed table's old name. Three of
-- the four tables here (memory_history, project_context_contracts_history,
-- project_history) are the INSERT target of a trigger defined on a DIFFERENT
-- table, so at the instant of `ALTER TABLE ..._v33_new RENAME TO
-- memory_history` the target name is transiently unresolved (the old table
-- was just DROPped, the rename has not yet landed) and that eager
-- re-validation aborts with "no such table: main.memory_history" even though
-- the trigger itself is never executed. `legacy_alter_table = ON` reverts to
-- the pre-3.25 RENAME behavior (rename only, no schema-wide re-validation),
-- which is exactly what this rebuild needs -- the triggers' bodies do not
-- reference these tables' OLD (`*_v33_new`) name, only their FINAL name, so
-- there is nothing for the fixup pass to correct in the first place.

PRAGMA legacy_alter_table = ON;

-- ---------------------------------------------------------------------------
-- memory_history
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS memory_history_v33_new (
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
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

INSERT INTO memory_history_v33_new
    (id, workspace, name, before_workspace, after_workspace, before_body,
     after_body, before_type, after_type, before_description,
     after_description, before_status, after_status, before_deleted_at,
     after_deleted_at, changed_at, changed_by_agent)
SELECT
    id, workspace, name, before_workspace, after_workspace, before_body,
    after_body, before_type, after_type, before_description,
    after_description, before_status, after_status, before_deleted_at,
    after_deleted_at, changed_at, changed_by_agent
FROM memory_history;

DROP TABLE memory_history;

ALTER TABLE memory_history_v33_new RENAME TO memory_history;

CREATE INDEX IF NOT EXISTS idx_memory_history_workspace_name ON memory_history(workspace, name);

-- ---------------------------------------------------------------------------
-- agent_contract_handoffs
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_contract_handoffs_v33_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id      TEXT,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,
    workspace        TEXT NOT NULL,
    brief_id         INTEGER,
    task_status      TEXT NOT NULL
                     CHECK (task_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT')),
    raw_handoff_json TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (brief_id)  REFERENCES briefs(id)
);

INSERT INTO agent_contract_handoffs_v33_new
    (id, contract_id, agent_id, session_id, workspace, brief_id, task_status,
     raw_handoff_json, created_at)
SELECT
    id, contract_id, agent_id, session_id, workspace, brief_id, task_status,
    raw_handoff_json, created_at
FROM agent_contract_handoffs;

DROP TABLE agent_contract_handoffs;

ALTER TABLE agent_contract_handoffs_v33_new RENAME TO agent_contract_handoffs;

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);

-- ---------------------------------------------------------------------------
-- project_context_contracts_history
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_context_contracts_history_v33_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_key        TEXT NOT NULL,
    workspace           TEXT NOT NULL,
    before_payload_json TEXT,
    after_payload_json  TEXT NOT NULL,
    changed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent    TEXT,
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

INSERT INTO project_context_contracts_history_v33_new
    (id, contract_key, workspace, before_payload_json, after_payload_json,
     changed_at, changed_by_agent)
SELECT
    id, contract_key, workspace, before_payload_json, after_payload_json,
    changed_at, changed_by_agent
FROM project_context_contracts_history;

DROP TABLE project_context_contracts_history;

ALTER TABLE project_context_contracts_history_v33_new RENAME TO project_context_contracts_history;

CREATE INDEX IF NOT EXISTS idx_pcc_history_contract ON project_context_contracts_history(contract_key);

-- ---------------------------------------------------------------------------
-- project_history
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS project_history_v33_new (
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
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

INSERT INTO project_history_v33_new
    (id, workspace, name, before_path, after_path, before_workspace,
     after_workspace, before_name, after_name, before_status, after_status,
     changed_at)
SELECT
    id, workspace, name, before_path, after_path, before_workspace,
    after_workspace, before_name, after_name, before_status, after_status,
    changed_at
FROM project_history;

DROP TABLE project_history;

ALTER TABLE project_history_v33_new RENAME TO project_history;

CREATE INDEX IF NOT EXISTS idx_project_history_workspace_name ON project_history(workspace, name);

PRAGMA legacy_alter_table = OFF;
