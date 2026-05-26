-- v4 -> v5 fresh-install variant.
--
-- Used by bootstrap_database.sh Section 3c case 5 when the live DDL is
-- already at the v5 target state (acceptance_criteria.status column present,
-- milestones.status column present). This happens on a clean install where
-- schema.sql already created the v5 column layout.
--
-- The default v4_to_v5.sql cannot run here because ALTER TABLE ADD COLUMN
-- fails when the column already exists. This variant carries only the DDL
-- that schema.sql cannot declare safely:
--   * idx_ac_brief_status       -- index on acceptance_criteria(brief_id, status)
--   * idx_milestones_brief_status -- index on milestones(brief_id, status)
--
-- Both indexes are CREATE INDEX IF NOT EXISTS, making this script safe to re-run.

CREATE INDEX IF NOT EXISTS idx_ac_brief_status
    ON acceptance_criteria(brief_id, status);

CREATE INDEX IF NOT EXISTS idx_milestones_brief_status
    ON milestones(brief_id, status);
