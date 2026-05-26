-- Migration v9 -> v10 (episodic-workflow-to-db: episodes workspace canonical)
--
-- Background
-- ----------
-- v9 schema has the episodes table with these columns:
--   episode_id, workspace, timestamp, session_id, task_id, agent,
--   type, title, prompt, enriched_prompt, wf_prompt, clarifications,
--   keywords, tags, commands_executed, context_metrics, relevance_score,
--   outcome, duration_seconds, exit_code, plan_status, output_length,
--   output_tokens_approx
--
-- v10 adds:
--   episodes.tier           -- security tier (T0/T1/T2/T3), promoted from context_metrics blob
--   episode_anomalies       -- structured anomaly records extracted from context_metrics blob
--
-- Design decisions
-- ----------------
-- D1: tier -> top-level column (not blob)
--   Rationale: tier is a single short TEXT value (T0/T1/T2/T3) with a clear
--   compliance query pattern: "COUNT(*) WHERE tier='T3' AND outcome='partial'".
--   Keeping it in the context_metrics JSON blob would require a full-table
--   JSON parse for every compliance query. With 10,000+ rows in workspace 'me'
--   alone, this is a significant performance cost. A column + index reduces
--   that to a B-tree lookup. The schema cost is one ALTER TABLE + one index.
--   Alternative considered: keep in blob. Rejected because the query pattern
--   is both real (used by context_injector.py anomaly surfacing) and frequent
--   (every compliance dashboard query). No reason to pay JSON parsing overhead
--   when the data is a four-value enum.
--
-- D2: episode_anomalies -> separate table (not blob)
--   Rationale: anomalies have a stable schema {type, severity, message} per
--   object. The query "all anomalies of type X in the last 7 days" is a real
--   operational need -- context_injector.py currently reads anomalies.jsonl
--   to surface critical anomalies in orchestrator context. That reader must
--   be ported post-migration. A separate table with a type index enables
--   `SELECT * FROM episode_anomalies JOIN episodes ON ... WHERE type=? AND
--   episodes.timestamp > ?` without JSON parsing any rows. With anomalies
--   present in a large fraction of episodes (4 anomalies in the 12 observed
--   sessions), the cardinality justifies a separate table. The anomalies[]
--   array is still preserved inside context_metrics for backward compatibility
--   with any reader that parses the full blob -- the table is an additional
--   queryable index, not a replacement.
--   Alternative considered: keep in context_metrics blob. Rejected because
--   the type-filtered cross-episode query has no efficient implementation
--   without the table. GROUP BY type reports are otherwise O(N) full scans
--   with JSON parsing per row.
--
-- Column notes
-- ------------
-- episodes.workspace: already present in the v9 schema; NO ALTER TABLE needed.
--   Live DB confirmed: workspace column exists and has data ('me', 'bildwiz',
--   'nfi'). The default 'me' for legacy rows is unnecessary -- workspace is
--   already populated. This step is a no-op in terms of DDL.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v9 state; the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.
-- Closes AC-2 of brief episodic-workflow-to-db (brief_id=72).

-- Step 1: Add tier column to episodes.
-- SQLite does not support CHECK constraints in ALTER TABLE ADD COLUMN without
-- a DEFAULT, so the CHECK is omitted here; validation is enforced at the
-- application layer (episodic.py / workflow_recorder.py writers).
ALTER TABLE episodes ADD COLUMN tier TEXT;

-- Step 2: Index tier for compliance queries.
CREATE INDEX IF NOT EXISTS idx_episodes_tier ON episodes(tier);

-- Step 3: Compound index for the primary compliance query pattern:
-- "T3 operations with non-COMPLETE outcomes in time window".
CREATE INDEX IF NOT EXISTS idx_episodes_tier_outcome ON episodes(tier, outcome);

-- Step 4: Create episode_anomalies table.
-- Each row is one anomaly record extracted from an episode's context_metrics
-- blob. The payload column holds the full original JSON object for forward
-- compatibility (additional keys in future anomaly schemas are preserved).
CREATE TABLE IF NOT EXISTS episode_anomalies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  TEXT NOT NULL,              -- FK -> episodes.episode_id
    workspace   TEXT NOT NULL,              -- denormalized for partition queries without JOIN
    timestamp   TEXT NOT NULL,              -- denormalized from parent episode for time-range queries
    type        TEXT NOT NULL,              -- e.g. "investigation_skip", "no_tool_use"
    severity    TEXT,                       -- e.g. "warning", "error", "info"
    message     TEXT,                       -- human-readable description
    payload     TEXT,                       -- full JSON object (forward-compat for extra keys)
    FOREIGN KEY (episode_id) REFERENCES episodes(episode_id) ON DELETE CASCADE
);

-- Step 5: Indexes on episode_anomalies.
-- Primary query patterns:
--   (a) All anomalies of type X: WHERE type = ?
--   (b) Cross-episode anomaly report in time window: WHERE type = ? AND timestamp > ?
--   (c) Anomalies for a specific episode: WHERE episode_id = ?
--   (d) Workspace-scoped anomaly dashboard: WHERE workspace = ? AND timestamp > ?
CREATE INDEX IF NOT EXISTS idx_episode_anomalies_type      ON episode_anomalies(type);
CREATE INDEX IF NOT EXISTS idx_episode_anomalies_workspace  ON episode_anomalies(workspace, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_episode_anomalies_episode    ON episode_anomalies(episode_id);

-- Step 6: Bump schema_version to 10.
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (10, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'episodes.tier column + idx + episode_anomalies table (brief episodic-workflow-to-db AC-2)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;           -- expect: 10
-- PRAGMA table_info(episodes);                       -- expect: tier column present (after output_tokens_approx)
-- SELECT * FROM sqlite_master WHERE type='index' AND name LIKE 'idx_episodes_tier%';  -- expect: 2 rows
-- PRAGMA table_info(episode_anomalies);              -- expect: 7 columns
-- SELECT COUNT(*) FROM episode_anomalies;            -- expect: 0 (populated by T3 migration task)
