-- Migration v55 -> v56: managed briefs and plans, the data a verdict and an
-- acceptance rest on.
--
-- * task_gates.stale_at / stale_reason: a verdict whose subject changed (gate,
--   task goal, covered AC) is kept and marked stale instead of silently counting.
-- * task_gates.fail_cause: why a gate failed -- product, environment, broken
--   test, or changed requirement.
-- * evidence.gate_id / polarity: evidence can name the gate it came from and be
--   recorded as refuting.
-- * task_acceptance_criteria / task_dependencies: which ACs a task covers and
--   which tasks it waits for, as rows instead of prose in the goal. The AC is
--   keyed by its text id because upsert_brief re-inserts AC rows on every edit.
-- * brief_decisions: the brief's decisions, each naming the one it replaces.
--
-- Structure only: existing rows read with NULL / 'positive' defaults.
-- Each ADD COLUMN stays on one line: the runner's existence guard skips an
-- already-present column line by line, and a continuation line would be left
-- behind as a dangling fragment.

ALTER TABLE task_gates ADD COLUMN stale_at TEXT;
ALTER TABLE task_gates ADD COLUMN stale_reason TEXT;
ALTER TABLE task_gates ADD COLUMN fail_cause TEXT CHECK (fail_cause IN ('product', 'environment', 'broken_test', 'requirement_changed'));

ALTER TABLE evidence ADD COLUMN gate_id INTEGER REFERENCES task_gates(id) ON DELETE SET NULL;
ALTER TABLE evidence ADD COLUMN polarity TEXT NOT NULL DEFAULT 'positive' CHECK (polarity IN ('positive', 'negative'));

CREATE TABLE IF NOT EXISTS task_acceptance_criteria (
    task_id INTEGER NOT NULL,
    ac_id   TEXT NOT NULL,
    PRIMARY KEY (task_id, ac_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id            INTEGER NOT NULL,
    depends_on_task_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id),
    CHECK (task_id <> depends_on_task_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS brief_decisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    brief_id      INTEGER NOT NULL,
    decision      TEXT NOT NULL,
    rationale     TEXT,
    supersedes_id INTEGER,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (brief_id) REFERENCES briefs(id) ON DELETE CASCADE,
    FOREIGN KEY (supersedes_id) REFERENCES brief_decisions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_task_acceptance_criteria_ac ON task_acceptance_criteria(ac_id);
CREATE INDEX IF NOT EXISTS idx_task_dependencies_on ON task_dependencies(depends_on_task_id);
CREATE INDEX IF NOT EXISTS idx_brief_decisions_brief ON brief_decisions(brief_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_brief_decisions_supersedes
    ON brief_decisions(supersedes_id) WHERE supersedes_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_gate ON evidence(gate_id);
