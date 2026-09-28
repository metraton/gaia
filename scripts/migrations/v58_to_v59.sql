-- gaia-compat: backward
-- Migration v58 -> v59: record in the version ledger the oldest code that may
-- still write to this database.
--
-- Several installations of different versions share one gaia.db. Each seal of
-- the chain now writes `schema_version.min_code_version`: the previous seal's
-- value, raised to the migration's own version only when the migration is
-- declared `-- gaia-compat: breaking`. A Gaia older than the database keeps
-- writing while its expected version is at least that minimum.
--
-- Backward: a nullable column on a table older code only reads; code that does
-- not know the column never selects it and its INSERT names its own columns.

ALTER TABLE schema_version ADD COLUMN min_code_version INTEGER;
