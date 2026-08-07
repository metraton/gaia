-- Migration v42 -> v43: dispatch correlation + kernel payload on
-- agent_contract_handoffs.
--
-- WHAT CHANGES
--   Seven nullable columns on agent_contract_handoffs, all stamped at birth
--   (insert_dispatched_handoff) or at claim time (claim_dispatch_row):
--
--     dispatch_prompt_id    TEXT  -- host prompt_id of the PreToolUse:Task event
--     dispatch_tool_use_id  TEXT  -- host tool_use_id of the Task tool call
--     dispatch_description  TEXT  -- Task tool `description` parameter
--     dispatch_prompt       TEXT  -- Task tool `prompt` parameter (the goal)
--     claimed_at            TEXT  -- ISO8601 stamp set once by claim_dispatch_row
--     context_anchors       TEXT  -- JSON list of context anchors computed at dispatch
--     kernel_sections       TEXT  -- JSON object (role/surface/can_read/can_write)
--
--   One partial index over the unclaimed side: the claim query only ever
--   targets `claimed_at IS NULL` rows still in the DISPATCHED state.
--   No backfill: a historical row has no dispatch payload to recover, and a
--   guessed correlation is worse than a NULL.
--
-- WHY
--   The dispatch->start bridge used to live in /tmp cache files correlated by
--   (agent_type, task_description) heuristics. Persisting the dispatch
--   coordinates on the born row makes the row itself the bridge:
--   claim_dispatch_row correlates a SubagentStart to its born row by
--   dispatch_prompt_id / dispatch_description, claims it atomically
--   (claimed_at), and the kernel injected into the subagent
--   (modules/context/kernel_builder.py) renders from kernel_sections +
--   dispatch_prompt without rebuilding project context.
--
--   No CHECK constraints, mirroring `kind` / `cut_reason` / `harness_agent_id`:
--   ALTER TABLE ADD COLUMN carries no CHECK, so declaring one only in
--   schema.sql would leave a migrated DB and a fresh install with different
--   shapes.
--
-- IDEMPOTENCY
--   Same contract as v39->v40: one `ALTER TABLE ... ADD COLUMN ...` per LINE
--   so the bootstrap runner's guard (_filter_add_column_idempotent) can
--   neutralise already-present columns and its Section 1.5 pre-schema
--   reconcile can add them to an existing DB before schema.sql replays.
--   CREATE INDEX IF NOT EXISTS is idempotent by construction.

ALTER TABLE agent_contract_handoffs ADD COLUMN dispatch_prompt_id TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN dispatch_tool_use_id TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN dispatch_description TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN dispatch_prompt TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN claimed_at TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN context_anchors TEXT;
ALTER TABLE agent_contract_handoffs ADD COLUMN kernel_sections TEXT;

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_unclaimed ON agent_contract_handoffs(dispatch_prompt_id) WHERE claimed_at IS NULL;
