-- Migration v38 -> v39: structural cut marker on agent_contract_handoffs.
--
-- WHAT CHANGES
--   One column: agent_contract_handoffs.cut_reason (TEXT, nullable).
--   One partial index: idx_agent_contract_handoffs_cut, over the non-NULL side.
--   One backfill: rows still in the 'DISPATCHED' ROW state are stamped
--   'never_finalized' so the invariant holds for rows born before this version.
--
-- WHY
--   A turn ends one of two ways: the agent runs its own `gaia contract
--   finalize` (a CLEAN closure it earned), or something else closes the row for
--   it -- a truncation salvage, a SubagentStop backstop, a reap, or nothing at
--   all when the harness cut is hard enough that no hook ever fires. That
--   second population is exactly what an operator needs to find, and until v39
--   it was recorded ONLY inside raw_handoff_json as `degraded` / `reaped` /
--   `salvaged`. No SQL predicate reaches a JSON body without parsing every row,
--   so "which turns did not close cleanly" was not a query at all. cut_reason
--   lifts that fact into a column:
--
--       SELECT contract_id, agent_id, cut_reason
--         FROM agent_contract_handoffs
--        WHERE cut_reason IS NOT NULL;
--
--   The column is DEFAULT-MARKED rather than default-clean, and that inversion
--   is what makes the hardest case detectable: insert_dispatched_handoff stamps
--   'never_finalized' at BIRTH, and only finalize_agent_contract_handoff called
--   WITHOUT a cut_reason clears it. A turn that simply disappears -- no
--   SubagentStop, no closure path, no further write of any kind -- is still
--   marked, because nothing ever cleared its birth stamp. Cleanliness is
--   something a turn EARNS by finalizing.
--
--   No CHECK constraint, mirroring `kind`: ALTER TABLE ADD COLUMN carries no
--   CHECK, so declaring one only in schema.sql would leave a migrated DB and a
--   fresh install with different shapes. The vocabulary is owned by
--   gaia.state.CUT_REASONS (never_finalized / reaped / backstop_capture /
--   salvaged_truncation).
--
--   The index is PARTIAL because the cut population is the minority this query
--   wants; indexing only the non-NULL side keeps it proportional to that
--   minority rather than to the whole contract history.
--
-- IDEMPOTENCY
--   All three statements are safe to replay, on both paths:
--
--   * ADD COLUMN. SQLite has no `ADD COLUMN IF NOT EXISTS`, so idempotency is
--     supplied by the bootstrap runner's guard (`_filter_add_column_idempotent`
--     in scripts/bootstrap_database.py): the line is NEUTRALISED (commented out)
--     when agent_contract_handoffs.cut_reason already exists, and applied
--     otherwise. The same runner's Section 1.5 pre-schema reconcile parses this
--     same line to add the column to an EXISTING DB before schema.sql runs, so
--     the CREATE INDEX in schema.sql cannot abort with "no such column". Both
--     depend on the statement being ONE `ALTER TABLE ... ADD COLUMN ...` per
--     LINE, which is why it is written flat below.
--   * CREATE INDEX IF NOT EXISTS. Idempotent by construction; an index is
--     derived state, so applying it twice or to an empty table are both
--     harmless.
--   * The backfill UPDATE. Idempotent by its own WHERE: after the first run no
--     row matches `cut_reason IS NULL AND agent_state = 'DISPATCHED'` any more,
--     so a replay writes nothing. It also cannot lose or duplicate a row -- it
--     sets one column on rows that already exist, and touches no row that has
--     left the 'DISPATCHED' state (a row that already converged to a verdict
--     had its cut_reason decided by whichever writer landed that verdict).
--
--   ORDERING across the two paths (unchanged from v37 -> v38): bootstrap
--   applies schema.sql FIRST (Section 2) and only then walks pending migrations
--   (Section 3c). On a FRESH install the column and the index already exist by
--   the time this file is replayed -- the ADD COLUMN is neutralised, the
--   CREATE INDEX is a no-op, and the backfill matches nothing because the table
--   is empty. On a live v38 DB the pre-schema reconcile adds the column,
--   schema.sql creates the index, and this file then finds both present and
--   performs only the backfill. Both paths converge on the same shape; the
--   ledger stamp is what differs.

ALTER TABLE agent_contract_handoffs ADD COLUMN cut_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_cut ON agent_contract_handoffs(cut_reason) WHERE cut_reason IS NOT NULL;

UPDATE agent_contract_handoffs
   SET cut_reason = 'never_finalized'
 WHERE cut_reason IS NULL
   AND agent_state = 'DISPATCHED';
