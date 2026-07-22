-- Migration v33 -> v34: add the task_gates child table (planner-authored typed
-- verification gate slot, harness R1-A).
--
-- WHAT this adds: a new one-to-many child of tasks. verification_type is a
-- REAL column with a CHECK against the four VALID_VERIFICATION_TYPES literals
-- (gaia.state), registered in STATE_MACHINE_REGISTRY so the SQL CHECK and the
-- Python tuple stay identical (tools/state/diff_source_of_truth.py). The
-- evidence column NAMES (evidence_type / evidence_shape / artifact_path) are
-- copied VERBATIM from acceptance_criteria. `status` is a plain column with no
-- CHECK / state machine (gate lifecycle is the verifier's concern, out of
-- scope for R1-A).
--
-- WHY additive (no ALTER, no rebuild): this is a brand-new table plus one
-- index. It touches no existing table, copies no data, and cannot orphan any
-- row. An existing gaia.db at v33 simply gains an empty task_gates table; a
-- fresh install already has the identical shape from schema.sql.
--
-- IDEMPOTENCY (required by the floor model -- this file is REPLAYED on every
-- fresh install). On a fresh install schema.sql (applied unconditionally in
-- bootstrap Section 2, BEFORE the migration ledger in Section 3c) has already
-- created task_gates + idx_task_gates_task with this exact shape, so the
-- CREATE ... IF NOT EXISTS statements below are a no-op there. On an existing
-- v33 DB they create the table for the first time. Either path lands on the
-- same shape.

CREATE TABLE IF NOT EXISTS task_gates (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER NOT NULL,
    verification_type TEXT NOT NULL
                      CHECK (verification_type IN ('command', 'code', 'semantic', 'self_review')),
    evidence_type     TEXT,
    evidence_shape    TEXT,
    artifact_path     TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_gates_task ON task_gates(task_id);
