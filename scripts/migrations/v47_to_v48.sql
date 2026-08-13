-- Migration v47 -> v48: usage telemetry columns on curated memory rows, plus
-- the memory_au FTS re-index trigger recreated with a WHEN clause so the new
-- telemetry columns cannot amplify write-per-read on the search index.
--
-- WHAT CHANGES
--   Four columns on `memory`, one ADD COLUMN per line:
--
--     injection_count    INTEGER NOT NULL DEFAULT 0
--     deliberate_count   INTEGER NOT NULL DEFAULT 0
--     last_injected_at   TEXT (nullable)
--     last_deliberate_at TEXT (nullable)
--
--   Plus memory_au (the memory_fts re-index trigger) dropped and recreated
--   with a WHEN clause -- see "MEMORY_AU: SAME DELIVERY, SAME VERIFICATION
--   WINDOW" below.
--
--   The column additions are purely additive. No existing column is altered
--   or dropped. The two counters default to 0 (never NULL) so the 1295 rows live today on
--   schema_version=47 read as "never yet measured" -- the same answer a
--   fresh install gives. The two timestamps are nullable by design: NULL
--   means "never accessed by that surface", not "accessed at time zero".
--
-- WHY TWO COUNTERS, NOT ONE
--   This is the central design decision of the entry (telemetria-de-uso-en-
--   memoria-curada, P1): injection_count and deliberate_count are SEPARATE
--   on purpose. Injection is what the digest/sections/types context blocks
--   and the subagent kernel render automatically at session start or
--   dispatch; deliberate is what a caller pulls on explicit request (`memory
--   show`, `get-relevant --initiative`). A single merged counter would let a
--   row already selected for automatic injection reinforce itself every time
--   it is rendered again -- a row on top stays on top because it is on top,
--   and the ranking freezes. Keeping the two counters apart is what makes
--   that failure mode structurally impossible: this migration ships the
--   storage only, no scoring change and no reordering.
--
-- WHY THIS DOES NOT TOUCH updated_at
--   updated_at DESC is the sort key every injection path uses to pick which
--   rows make the top _MEMORY_ROW_LIMIT=20. A telemetry write that touched
--   updated_at would reorder the injected block on every read that measures
--   it -- exactly what this entry's central acceptance criterion forbids.
--   Nothing in this migration alters updated_at or the upsert path; the two
--   new counters and their timestamps exist as columns a future narrow,
--   dedicated UPDATE can write without going anywhere near it.
--
-- WHY trg_memory_history DOES NOT FIRE ON THESE COLUMNS
--   trg_memory_history's WHEN clause (schema.sql) enumerates the columns it
--   audits -- name/body/workspace/type/description/status/class/project_ref/
--   initiative/deleted_at -- and the four columns added here are not among
--   them, by construction. A telemetry write happens on every deliberate
--   read and every automatic injection; if it tripped this trigger,
--   memory_history would grow without bound on every session start. No
--   change to the trigger is needed here BECAUSE its WHEN clause already
--   excludes columns it does not name -- adding a column to `memory` never
--   implicitly adds it to an existing trigger's WHEN.
--
-- MEMORY_AU: SAME DELIVERY, SAME VERIFICATION WINDOW
--   The memory_au FTS re-index trigger had no WHEN clause, so every UPDATE
--   -- including this migration's own telemetry-only writes -- re-indexed
--   memory_fts. That is the packaging decision recorded here: it ships in
--   THIS migration, not a follow-up version, because it is the same v48
--   entry and the runner already has a proven precedent for a DROP+CREATE
--   trigger statement passing through untouched (see IDEMPOTENCY below).
--   The recreated trigger is defined below, after the column additions.
--
-- IDEMPOTENCY
--   SQLite has no `ADD COLUMN IF NOT EXISTS`. Idempotency is supplied by the
--   bootstrap runner's guard (`_filter_add_column_idempotent` in
--   scripts/bootstrap_database.py): each line below is NEUTRALISED (commented
--   out) when the target column already exists on `memory`, and applied
--   otherwise. The same runner's Section 1.5 pre-schema reconcile adds these
--   columns to an EXISTING DB before schema.sql replays, so schema.sql's own
--   (idempotent) CREATE TABLE IF NOT EXISTS never has to reconcile a missing
--   column against a live table. Both mechanisms depend on the statement
--   being ONE `ALTER TABLE ... ADD COLUMN ...` per LINE, which is why the
--   four are written flat below rather than combined. The filter only
--   matches `ALTER TABLE ... ADD COLUMN` lines (`_ADD_COLUMN_RE`) -- every
--   other line, including the DROP/CREATE TRIGGER pair below, passes through
--   verbatim. v40_to_v41.sql already proved this precedent live: it drops
--   and recreates trg_memory_history in the same shipped migration file with
--   no special-casing from the runner.

ALTER TABLE memory ADD COLUMN injection_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory ADD COLUMN deliberate_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory ADD COLUMN last_injected_at TEXT;
ALTER TABLE memory ADD COLUMN last_deliberate_at TEXT;

-- memory_au: recreate with a WHEN clause scoping the re-index to the four
-- columns the trigger body actually writes into memory_fts (workspace, name,
-- description, body) -- see schema.sql for the full rationale. On a DB that
-- already has SOME version of memory_au (any prior schema version always
-- shipped one), schema.sql's own `CREATE TRIGGER IF NOT EXISTS` is a no-op,
-- so only this explicit DROP+CREATE actually changes the trigger's
-- definition on an existing installation.
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
