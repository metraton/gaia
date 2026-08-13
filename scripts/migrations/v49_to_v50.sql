-- Migration v49 -> v50: row age, the kernel's own access counter pair, and the
-- one-shot capture-then-reset of the deliberate-read axis.
--
-- WHAT CHANGES, IN THIS ORDER
--   1. memory.created_at      TEXT (nullable)            -- row age, forward-only
--   2. memory.kernel_count    INTEGER NOT NULL DEFAULT 0 -- third access axis
--      memory.last_kernel_at  TEXT (nullable)
--   3. memory_deliberate_capture_v50 -- new table; durable record of every row
--      that carries deliberate-read signal, taken immediately before step 4
--   4. deliberate_count / last_deliberate_at zeroed on exactly the rows step 3
--      captured, and on nothing else
--
-- THE ORDER OF 3 AND 4 IS A SAFETY PROPERTY, NOT A PREFERENCE
--   Section 3c of scripts/bootstrap_database.py wraps each migration file in
--   ONE transaction -- `con.executescript(f"BEGIN;\n{mig_sql}\nCOMMIT;")`,
--   with a ROLLBACK and no ledger stamp if any statement raises. Capture and
--   reset are therefore not two acts with a window between them: they are one
--   act. No reader can interleave, and no crash can leave the axis erased but
--   unrecorded -- either both happen or neither does.
--
--   That single transaction is the only mechanism that makes the record
--   correspond to the state actually discarded. Measured during this plan: a
--   census of the deliberate axis, verified from OUTSIDE the act that erases
--   it, moves the very figures it measures -- one row went from 6 to 7
--   deliberate reads between two regenerations of the same census, with a
--   timestamp outside the contaminating sweep's window, i.e. a real read
--   produced by the work of verifying. The instrument and the measured system
--   are the same system. While Gaia is running, no regeneration of the census
--   can be the last one; only a capture taken from inside the erasing act can.
--
-- COMPLETENESS OF THE CAPTURE, HELD BY CONSTRUCTION
--   Step 3 and step 4 carry the SAME predicate, written identically, over the
--   same table, with no other filter of any kind:
--
--       deliberate_count != 0 OR last_deliberate_at IS NOT NULL
--
--   Nothing narrows it. There is no `deleted_at IS NULL` clause, so the
--   soft-deleted rows (10 live at authoring time) are captured exactly as they
--   are reset. There is no workspace clause, so all workspaces (5 live at
--   authoring time) are in scope for both. The predicate is an OR over the two
--   telemetry columns rather than a test of the counter alone, so a row with a
--   timestamp but a zero counter, and a row with a counter but a NULL
--   timestamp, both satisfy it and are therefore in BOTH statements -- neither
--   edge shape can be erased without first being written down.
--
--   The guarantee is the IDENTITY of the two predicates, not their cleverness:
--   any row the predicate excludes is excluded from the capture AND from the
--   reset, so it keeps its signal untouched. An excluded row is never a lost
--   row; only a row erased by a predicate WIDER than the capture's could be
--   lost, and no such asymmetry exists here. This property cannot be verified
--   after the fact -- once the source column is zeroed there is nothing left
--   to recount it against -- so it is held by construction. If this file is
--   ever read as a template for another capture-then-reset, the two predicates
--   must stay textually identical; that identity is the whole safety argument.
--
-- WHAT THE CAPTURE PRESERVES, AND WHAT IT DOES NOT
--   The counter and its single timestamp: how many identified reads a row
--   accumulated, and when the last one happened. It does NOT preserve a
--   history of when each read occurred -- that history never existed in the
--   schema and this migration cannot invent it.
--
-- WHY THE RESET IS JUSTIFIED
--   The deliberate axis is dominated by contamination, not by use. At
--   authoring time the live distribution is: 1030 rows at exactly 1, 85 at 2,
--   6 at 3, and one row each at 5, 7 and 8. The mass at 1 is the residue of a
--   two-minute bulk sweep that read rows nobody asked for; the legitimate
--   signal is the short tail above it. A ranking fed by that column would be
--   ranking the sweep. Zeroing the axis is what lets the clean measurement
--   start, and the capture above is what makes the zeroing a move rather than
--   a loss.
--
-- WHY THE RESET TRAVELS IN A MIGRATION
--   A direct UPDATE against gaia.db is a CATEGORICAL, non-approvable denial in
--   Gaia's own guard; the guard names a migration as the sanctioned route.
--   This file is that route.
--
-- NON-IDEMPOTENCY, STATED PLAINLY
--   Steps 3 and 4 are NOT idempotent. Re-running them after signal has
--   re-accumulated would erase that fresh signal, and the capture INSERT would
--   collide with its own primary key and abort. Nothing IN THIS FILE makes
--   that safe. What makes it safe is the ledger: Section 3c iterates only
--   range(current_version + 1, expected_version + 1), so a version already
--   stamped is never reopened, and this file is unreachable from the moment
--   its stamp lands. The correct demonstration of that is therefore NOT
--   applying this file twice by hand -- that demonstrates the damage rather
--   than ruling it out -- but seeding a fresh deliberate value AFTER applying,
--   invoking the runner again, and observing that the seeded value survives.
--
--   The plain INSERT below is deliberate: not OR IGNORE, not OR REPLACE. A
--   primary-key collision means something was already captured under this
--   version, and the only safe response is to fail -- the transaction rolls
--   back, the reset does not run, and no signal is lost.
--
-- COLUMN ADDITIONS AND REPLAY
--   SQLite has no `ADD COLUMN IF NOT EXISTS`. Idempotency for steps 1 and 2 is
--   supplied by the runner's `_filter_add_column_idempotent`, which neutralises
--   a column line whose column already exists, and by Section 1.5's pre-schema
--   reconcile, which adds it to an existing DB before schema.sql replays. Both
--   layers parse ONE column addition per LINE, which is why the three are
--   written flat below. Every other statement in this file passes through
--   verbatim.

ALTER TABLE memory ADD COLUMN created_at TEXT;
ALTER TABLE memory ADD COLUMN kernel_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE memory ADD COLUMN last_kernel_at TEXT;

-- created_at is FORWARD-ONLY BY DECISION: no backfill, here or ever. A row
-- that existed before this migration has no knowable birth date, and every
-- available substitute is a fabrication -- stamping `now` would make the whole
-- corpus newborn and let it sweep any recency-weighted ranking, and borrowing
-- updated_at would date a row by its last edit, which is precisely the proxy
-- this entry exists to stop using. NULL here means "age unknown", never "age
-- zero"; any consumer must branch on it explicitly.
--
-- kernel_count / last_kernel_at are the THIRD access axis, separated from
-- injection_count for the same reason injection and deliberate were separated
-- in v48: mixing signals of different natures freezes the ranking. The kernel
-- block fires on EVERY subagent dispatch over the rows tagged type=user
-- AND audience=executor, and the measurement shows the effect -- the three
-- heads of the injection ranking are exactly those rows (37, 37, 26) against
-- 17 or less for everything else. This migration ships the storage only: the
-- single call site moves to the new axis in its own task. Forward-only there
-- too -- what the kernel already added to injection_count is NOT retroactively
-- subtracted; that historical prefix stays mixed and is declared suspect,
-- rather than being given an invented correction.

-- Durable record of the deliberate axis as it stands one statement before it
-- is zeroed. Deliberately carries NO foreign key to `memory` or `workspaces`:
-- the point of the table is to outlive the row it describes, including a row
-- later hard-deleted or a workspace later dropped. The primary key mirrors
-- memory's own (workspace, name).
CREATE TABLE IF NOT EXISTS memory_deliberate_capture_v50 (
    workspace          TEXT NOT NULL,
    name               TEXT NOT NULL,
    deliberate_count   INTEGER NOT NULL,
    last_deliberate_at TEXT,
    captured_at        TEXT NOT NULL,
    PRIMARY KEY (workspace, name)
);

INSERT INTO memory_deliberate_capture_v50
    (workspace, name, deliberate_count, last_deliberate_at, captured_at)
SELECT workspace, name, deliberate_count, last_deliberate_at,
       strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM memory
WHERE deliberate_count != 0 OR last_deliberate_at IS NOT NULL;

-- The reset. Same table, same predicate as the capture above, and it touches
-- EXCLUSIVELY the deliberate counter and its timestamp: no body, no other
-- counter, no updated_at, no lifecycle column. Both columns it writes sit
-- outside trg_memory_history's WHEN clause and outside memory_au's, so this
-- statement lands zero history rows and zero FTS re-indexing -- the same
-- narrow-write discipline every telemetry write in v48 already follows.
UPDATE memory
SET deliberate_count = 0,
    last_deliberate_at = NULL
WHERE deliberate_count != 0 OR last_deliberate_at IS NOT NULL;
