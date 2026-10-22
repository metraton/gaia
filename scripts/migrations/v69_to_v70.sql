-- gaia-compat: backward
-- Migration v69 -> v70: one store for what closed turns proposed.
--
-- Structure only: a new table and its index, no existing row is read or
-- rewritten. Rows from before v70 are recorded by the separate backfill verb
-- (`gaia contract proposals backfill`), never by this file.
--
-- Backward: code older than v70 never reads or writes this table, so its
-- writes stay correct; its closes simply record no proposals.

CREATE TABLE IF NOT EXISTS proposals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id    INTEGER NOT NULL,
    item_index    INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('memory', 'context', 'thread')),
    project       TEXT,
    payload       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'accepted', 'dismissed')),
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_at    TEXT,
    decided_by    TEXT,
    decision_note TEXT,
    UNIQUE (handoff_id, kind, item_index),
    FOREIGN KEY (handoff_id) REFERENCES agent_contract_handoffs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status, id);
