-- v5 -> v6 fresh-install variant.
--
-- Used by bootstrap_database.sh Section 3c case 6 when the live DDL is
-- already at the v6 target state (evidence table present). This happens on
-- a clean install where schema.sql already created the evidence table.
--
-- The default v5_to_v6.sql cannot run here because CREATE TABLE IF NOT EXISTS
-- is a no-op when the table already exists, but the indexes must still be
-- stamped. This variant carries only the DDL that schema.sql cannot declare
-- safely for older DBs:
--   * idx_evidence_brief -- index on evidence(brief_id)
--   * idx_evidence_ac    -- index on evidence(brief_id, ac_id)
--
-- Both indexes are CREATE INDEX IF NOT EXISTS, making this script safe to re-run.

CREATE INDEX IF NOT EXISTS idx_evidence_brief ON evidence(brief_id);
CREATE INDEX IF NOT EXISTS idx_evidence_ac ON evidence(brief_id, ac_id);
