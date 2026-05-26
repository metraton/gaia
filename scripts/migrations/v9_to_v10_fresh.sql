-- Migration v9 -> v10 fresh-install variant (episodic-workflow-to-db AC-3)
--
-- Used by bootstrap_database.sh when the live DB already has the
-- episode_anomalies table (i.e. schema.sql ran first on a clean install and
-- created the tables in v10 target state, including episodes.tier column).
--
-- On a fresh install, schema.sql creates the episodes table WITH the tier
-- column, so ALTER TABLE is not needed. However, the tier-dependent indexes
-- (idx_episodes_tier, idx_episodes_tier_outcome) cannot be declared in
-- schema.sql because schema.sql runs before migrations on existing DBs where
-- tier does not yet exist. This fresh variant creates those indexes safely,
-- since on a fresh install tier is guaranteed to exist.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.

-- Create tier indexes that schema.sql cannot safely declare for existing DBs.
CREATE INDEX IF NOT EXISTS idx_episodes_tier ON episodes(tier);
CREATE INDEX IF NOT EXISTS idx_episodes_tier_outcome ON episodes(tier, outcome);
