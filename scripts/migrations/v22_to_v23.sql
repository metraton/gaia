-- Migration v22 -> v23: add agent-owned `description` column to `projects`.
--
-- Closes the M3/T9 slice of the workspace-identity brief (AC-7): projects had
-- no free-text description column, and the scan path (upsert_project with
-- strip_agent_owned=True) must never be able to write it. The column is
-- registered as agent-owned in gaia/store/writer.py::_PROJECTS_AGENT_OWNED so
-- it survives any number of scanner rescans unchanged (extends the M1
-- coalesce-or-omit + ownership mechanism, AC-1, to this new column).
--
-- SQLite ALTER TABLE ADD COLUMN is safe and additive: existing rows receive
-- NULL and the table is NOT rebuilt (https://www.sqlite.org/lang_altertable.html).
--
-- Idempotency (floor model, replayed on every fresh install): schema.sql
-- already carries this column, so on a fresh install this ADD COLUMN targets
-- a column that already exists. bootstrap_database.sh's runner-level guard
-- (_filter_add_column_idempotent) neutralises this exact statement in that
-- case -- no `IF NOT EXISTS` needed in the SQL itself (SQLite has none for
-- ADD COLUMN).

ALTER TABLE projects ADD COLUMN description TEXT;
