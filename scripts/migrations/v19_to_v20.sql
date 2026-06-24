-- Migration v19 -> v20: add multi_use and confirmed columns to approval_grants.
--
-- These two columns support the upcoming FS-grant-plane migration:
--   multi_use INTEGER NOT NULL DEFAULT 0  -- 1 = multi-use grant, 0 = single-use (BOOLEAN)
--   confirmed INTEGER NOT NULL DEFAULT 0  -- 1 = grant confirmed by user, 0 = pending (BOOLEAN)
--
-- Both columns use the established boolean-as-INTEGER convention (DEFAULT 0)
-- already in use across this schema (e.g. allow_write, can_read, can_write).
--
-- SQLite ALTER TABLE ADD COLUMN is safe and additive: existing rows receive the
-- DEFAULT value and the table is NOT rebuilt.  Zero data loss is guaranteed by
-- the SQLite specification (https://www.sqlite.org/lang_altertable.html).
--
-- Bootstrap note: migrations run once via the version chain.  A fresh install
-- seeds from schema.sql (which already includes both columns) and then skips
-- this migration file by version-gating; this file runs only against existing
-- DBs initialized before v20 (which do not yet have these columns).

ALTER TABLE approval_grants ADD COLUMN multi_use INTEGER NOT NULL DEFAULT 0;
ALTER TABLE approval_grants ADD COLUMN confirmed INTEGER NOT NULL DEFAULT 0;
