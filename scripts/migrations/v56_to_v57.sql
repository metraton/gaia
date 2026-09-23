-- Migration v56 -> v57: a plan that can be paused, versioned, and changed
-- under management.
--
-- * plans.paused_at / pause_reason: pause is an overlay on an 'active' plan,
--   not a new status. Widening the status CHECK would need a rebuild of plans
--   (create / copy / drop / rename), which reaches every existing plan row;
--   two nullable columns reach none, and 'active' keeps meaning "approved".
-- * plan_versions: each replaced plan version and why it was replaced.
-- * plan_changes / plan_change_tasks: request -> proposal (affected tasks and
--   why) -> approval -> application of a plan change.
--
-- Structure only: existing plans read as not paused, at version 1.
-- Each ADD COLUMN stays on one line: the runner's existence guard skips an
-- already-present column line by line.

ALTER TABLE plans ADD COLUMN paused_at TEXT;
ALTER TABLE plans ADD COLUMN pause_reason TEXT;

CREATE TABLE IF NOT EXISTS plan_versions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id   INTEGER NOT NULL,
    version   INTEGER NOT NULL,
    status    TEXT,
    content   TEXT,
    reason    TEXT,
    change_id INTEGER REFERENCES plan_changes(id) ON DELETE SET NULL,
    saved_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (plan_id, version),
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plan_changes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id       INTEGER NOT NULL,
    justification TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'requested'
                  CHECK (status IN ('requested', 'proposed', 'approved', 'applied')),
    proposal      TEXT,
    requested_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    proposed_at   TEXT,
    approved_at   TEXT,
    applied_at    TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS plan_change_tasks (
    change_id INTEGER NOT NULL,
    task_id   INTEGER NOT NULL,
    reason    TEXT NOT NULL,
    PRIMARY KEY (change_id, task_id),
    FOREIGN KEY (change_id) REFERENCES plan_changes(id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
