-- gaia-compat: backward
-- Migration v59 -> v60: workspace aliases.
--
-- `gaia workspace retire <source> --into <target>` re-keys what a workspace
-- owns and leaves its history (episodes, episode_anomalies, harness_events,
-- agent_contract_handoffs) where it was written. One row here records that the
-- retired name now means the target, so a reader of the target also reads the
-- rows still keyed to the alias, and a CLI given the old name lands on the new
-- one. `ledger` is the undo ledger the retire wrote.
--
-- Backward: a new table older code never reads.

CREATE TABLE IF NOT EXISTS workspace_aliases (
    alias      TEXT NOT NULL PRIMARY KEY,
    target     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ledger     TEXT
);

CREATE INDEX IF NOT EXISTS idx_workspace_aliases_target ON workspace_aliases(target);
