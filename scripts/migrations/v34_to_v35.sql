-- Migration v34 -> v35: add NEEDS_VERIFICATION to the plan_status enum on
-- both persisted columns that mirror it -- episodes.plan_status and
-- agent_contract_handoffs.task_status (harness R2, brief
-- harness-r2-needs-verification-y-complete-restringido-por-rol-verificador).
--
-- WHY a table rebuild (not ALTER): both columns carry a CHECK constraint.
-- SQLite cannot ALTER a CHECK constraint in place -- there is no
-- `ALTER TABLE ... ALTER COLUMN` and no `ALTER TABLE ... DROP/ADD
-- CONSTRAINT`. The supported procedure
-- (https://www.sqlite.org/lang_altertable.html, "Making Other Kinds Of Table
-- Schema Changes") is: create a new table with the desired schema, copy the
-- rows, drop the old table, rename the new one into place. This file follows
-- the exact same procedure used by v20_to_v21.sql (acceptance_criteria) and
-- v21_to_v22.sql (agent_contract_handoffs), applied here to both tables.
--
-- ROWID PRESERVATION (episodes only -- the risk this migration guards
-- against explicitly): episodes is an external-content source for the
-- episodes_fts FTS5 virtual table (content='episodes', content_rowid=
-- 'rowid'). The fts5 shadow index stores rowids, not primary-key values
-- (episode_id is a TEXT PRIMARY KEY, so SQLite still keeps a separate hidden
-- rowid). A naive `INSERT INTO new_table SELECT * FROM episodes` would let
-- SQLite assign fresh sequential rowids, silently decoupling every existing
-- fts5 index entry from its row. This migration instead selects and inserts
-- the `rowid` pseudo-column explicitly so every row keeps its exact original
-- rowid, leaving episodes_fts (and its AFTER INSERT/DELETE/UPDATE triggers,
-- recreated verbatim below since DROP TABLE drops them with the old table)
-- correctly correlated with no separate 'rebuild' pass required.
--
-- agent_contract_handoffs.id is a true `INTEGER PRIMARY KEY` (a rowid alias
-- itself), so copying it as an ordinary column (as v21_to_v22.sql already
-- does) preserves its value the same way -- no separate rowid handling
-- needed there. Preserving `id` matters because
-- agent_contract_handoff_approvals.handoff_id references it by value.
--
-- INDEX PRESERVATION -- closing an incidental fresh-install gap found while
-- auditing what must survive the rebuild: the LIVE gaia.db carries
-- idx_episodes_tier and idx_episodes_tier_outcome (added by a pre-floor
-- migration that no longer exists after the v18 floor squash -- see
-- scripts/migrations/README.md section 4), but gaia/store/schema.sql never
-- declares them, so a fresh install today silently lacks them. Recreating
-- both here with IF NOT EXISTS preserves them on the upgrade path (no index
-- lost) AND creates them for the first time on a fresh-install replay,
-- converging both paths on an identical, complete index set going forward.
--
-- IDEMPOTENCY (required by the floor model -- this file is replayed on every
-- fresh install). On a fresh install schema.sql has already produced the v35
-- shape (both CHECKs already widened). Re-running this rebuild against a DB
-- already at the target shape is harmless: it reconstructs identical tables
-- from identical rows (rowids/ids included) and re-applies the same CHECKs
-- and indexes.
--
-- FOREIGN KEYS: bootstrap_database.sh runs migrations with SQLite's default
-- foreign_keys=OFF inside a single BEGIN/COMMIT, so the DROP+RENAME below
-- cannot orphan any child rows on either table.

-- ---------------------------------------------------------------------------
-- episodes.plan_status
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS episodes_v35_new (
    episode_id            TEXT NOT NULL PRIMARY KEY,
    workspace             TEXT NOT NULL,
    timestamp             TEXT NOT NULL,
    session_id            TEXT,
    task_id               TEXT,
    agent                 TEXT,
    type                  TEXT,
    title                 TEXT,
    prompt                TEXT,
    enriched_prompt       TEXT,
    wf_prompt             TEXT,
    clarifications        TEXT,
    keywords              TEXT,
    tags                  TEXT,
    commands_executed     TEXT,
    context_metrics       TEXT,
    relevance_score       REAL,
    outcome               TEXT,
    duration_seconds      REAL,
    exit_code             INTEGER,
    plan_status           TEXT,
    output_length         INTEGER,
    output_tokens_approx  INTEGER,
    tier                  TEXT,
    CHECK (plan_status IS NULL OR plan_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT', 'NEEDS_VERIFICATION')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE
);

INSERT INTO episodes_v35_new
    (rowid, episode_id, workspace, timestamp, session_id, task_id, agent,
     type, title, prompt, enriched_prompt, wf_prompt, clarifications,
     keywords, tags, commands_executed, context_metrics, relevance_score,
     outcome, duration_seconds, exit_code, plan_status, output_length,
     output_tokens_approx, tier)
SELECT
    rowid, episode_id, workspace, timestamp, session_id, task_id, agent,
    type, title, prompt, enriched_prompt, wf_prompt, clarifications,
    keywords, tags, commands_executed, context_metrics, relevance_score,
    outcome, duration_seconds, exit_code, plan_status, output_length,
    output_tokens_approx, tier
FROM episodes;

DROP TABLE episodes;

ALTER TABLE episodes_v35_new RENAME TO episodes;

-- Indexes were dropped together with the old table; recreate them. IF NOT
-- EXISTS keeps this idempotent if they somehow survive on a replay.
CREATE INDEX IF NOT EXISTS idx_episodes_workspace_timestamp ON episodes(workspace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_episodes_tier ON episodes(tier);
CREATE INDEX IF NOT EXISTS idx_episodes_tier_outcome ON episodes(tier, outcome);

-- Triggers were dropped together with the old table; recreate them verbatim
-- (byte-identical to gaia/store/schema.sql) so episodes_fts stays in sync
-- for every future insert/update/delete.
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
END;

CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
END;

CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES ('delete', old.rowid, old.episode_id, old.prompt, old.enriched_prompt, old.tags, old.title);
    INSERT INTO episodes_fts(rowid, episode_id, prompt, enriched_prompt, tags, title)
    VALUES (new.rowid, new.episode_id, new.prompt, new.enriched_prompt, new.tags, new.title);
END;

-- ---------------------------------------------------------------------------
-- agent_contract_handoffs.task_status
-- ---------------------------------------------------------------------------
-- v37 REPLAY-SAFETY: schema.sql renamed task_status -> agent_state as of v37
-- (v36_to_v37.sql). On a fresh install schema.sql builds the v37 shape and this
-- v35-era rebuild is replayed, so its SELECT task_status would abort on the
-- now-absent column. Defensively (re)add it -- the bootstrap runner's ADD
-- COLUMN idempotency guard neutralises this line when task_status already
-- exists (the genuine v34->v35 upgrade path with real data) and applies it only
-- on the fresh-install v37 shape, where the table is EMPTY during replay
-- (0 rows copied). See v21_to_v22.sql for the full rationale.
ALTER TABLE agent_contract_handoffs ADD COLUMN task_status TEXT;

CREATE TABLE IF NOT EXISTS agent_contract_handoffs_v35_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id      TEXT,
    agent_id         TEXT NOT NULL,
    session_id       TEXT,
    workspace        TEXT NOT NULL,
    brief_id         INTEGER,
    task_status      TEXT NOT NULL
                     CHECK (task_status IN ('IN_PROGRESS', 'APPROVAL_REQUEST', 'COMPLETE', 'BLOCKED', 'NEEDS_INPUT', 'NEEDS_VERIFICATION')),
    raw_handoff_json TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name) ON DELETE CASCADE,
    FOREIGN KEY (brief_id)  REFERENCES briefs(id)
);

INSERT INTO agent_contract_handoffs_v35_new
    (id, contract_id, agent_id, session_id, workspace, brief_id, task_status,
     raw_handoff_json, created_at)
SELECT
    id, contract_id, agent_id, session_id, workspace, brief_id, task_status,
    raw_handoff_json, created_at
FROM agent_contract_handoffs;

DROP TABLE agent_contract_handoffs;

ALTER TABLE agent_contract_handoffs_v35_new RENAME TO agent_contract_handoffs;

-- Indexes were dropped together with the old table; recreate them. IF NOT
-- EXISTS keeps this idempotent if they somehow survive on a replay.
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_contract_handoffs_contract_id ON agent_contract_handoffs(contract_id);
