-- Migration v48 -> v49: reconcile memory_au on installations that stamped v48
-- before the trigger DDL existed in v47_to_v48.sql.
--
-- WHY THIS FILE EXISTS AT ALL
--   The v48 entry was authored in two steps against the SAME migration file:
--   the four telemetry ADD COLUMN lines first, the memory_au DROP/CREATE pair
--   afterwards. Any installation whose bootstrap ran BETWEEN those two steps
--   applied the column half, stamped `schema_version` at 48, and can never see
--   the trigger half -- Section 3c of scripts/bootstrap_database.py iterates
--   only range(current+1, expected+1), so a ledger already at 48 skips
--   v47_to_v48.sql forever. Nothing else reaches the trigger either: Section
--   1.5's pre-schema reconcile matches `ALTER TABLE ... ADD COLUMN` lines only
--   (_ADD_COLUMN_RE) and never inspects triggers, and schema.sql's own
--   `CREATE TRIGGER IF NOT EXISTS memory_au` is a no-op against a trigger that
--   already exists under that name. Such a database therefore carries the new
--   columns with the OLD unconditional trigger -- the exact combination that
--   re-indexes a row's whole FTS document on every telemetry-only write.
--
--   A forward version is the only mechanism that reaches an already-stamped
--   ledger. Correcting v47_to_v48.sql in place cannot work: the file is
--   consulted solely on the ledger's way past 48, and these installations are
--   already past it.
--
-- WHY THIS IS A NO-OP WHERE IT IS ALREADY RIGHT
--   A database that stamped v48 from the completed file, and any fresh install
--   (whose memory_au comes from schema.sql, already carrying the WHEN clause),
--   receives a trigger definition identical to the one it already has. The
--   DROP+CREATE pair is written unconditionally rather than guarded because
--   SQLite offers no `CREATE OR REPLACE TRIGGER` and no way to compare a
--   trigger body in DDL: replacing a definition with a byte-identical one is
--   both the cheapest and the only reliable way to converge every starting
--   state on the same result.
--
-- WHAT THE WHEN CLAUSE IS FOR
--   memory_au mirrors `memory` into the memory_fts index, and the four columns
--   it writes there are workspace, name, description and body. The WHEN clause
--   restricts the trigger to changes in exactly those four, so a telemetry
--   UPDATE touching only injection_count/deliberate_count/last_injected_at/
--   last_deliberate_at -- which happens on every deliberate read and every
--   automatic injection -- no longer re-indexes anything. A real edit to
--   searchable content still re-indexes exactly as before. See schema.sql,
--   whose memory_au definition this statement reproduces verbatim; the two
--   must stay identical or a fresh install and a migrated one diverge.
--
-- IDEMPOTENCY
--   `DROP TRIGGER IF EXISTS` tolerates the trigger being absent, and the
--   recreate always lands the same definition, so re-running this migration
--   changes nothing. It carries no `ALTER TABLE ... ADD COLUMN` line, so the
--   runner's _filter_add_column_idempotent guard has nothing to neutralise
--   here and passes both statements through verbatim -- the same path
--   v40_to_v41.sql's trigger DROP/CREATE already took in a shipped release.

DROP TRIGGER IF EXISTS memory_au;

CREATE TRIGGER memory_au AFTER UPDATE ON memory
WHEN OLD.workspace IS NOT NEW.workspace
   OR OLD.name IS NOT NEW.name
   OR OLD.description IS NOT NEW.description
   OR OLD.body IS NOT NEW.body
BEGIN
    INSERT INTO memory_fts(memory_fts, rowid, workspace, name, description, body)
    VALUES ('delete', old.rowid, old.workspace, old.name, old.description, old.body);
    INSERT INTO memory_fts(rowid, workspace, name, description, body)
    VALUES (new.rowid, new.workspace, new.name, new.description, new.body);
END;
