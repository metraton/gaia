-- gaia-compat: breaking
-- Migration v68 -> v69: project context is keyed by the project it describes.
--
-- project_context replaces project_context_contracts, whose (workspace,
-- contract_name) key put a section about one project wherever the writer's
-- session happened to open. Structure only: no row is copied here, because a
-- workspace can hold several projects and SQL cannot tell which one a row is
-- about; the move is a reviewed manifest, and the old table stays readable as
-- the fallback until then.
--
-- History is written by the writer, not by a trigger, so inserts are recorded
-- and each change names the agent and the contract that proposed it.
--
-- Breaking: code older than v69 still writes project_context_contracts, which
-- readers no longer prefer, so its writes would silently diverge.

CREATE TABLE IF NOT EXISTS project_context (
    project_identity TEXT NOT NULL,
    contract_name    TEXT NOT NULL,
    payload          TEXT NOT NULL,
    metadata         TEXT,
    updated_at       TEXT,
    PRIMARY KEY (project_identity, contract_name)
);

CREATE TABLE IF NOT EXISTS project_context_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_identity    TEXT NOT NULL,
    contract_name       TEXT NOT NULL,
    before_payload_json TEXT,
    after_payload_json  TEXT NOT NULL,
    changed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent    TEXT,
    proposed_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_project_context_history_section
    ON project_context_history(project_identity, contract_name);
