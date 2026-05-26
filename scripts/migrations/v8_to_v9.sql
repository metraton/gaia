-- Migration v8 -> v9 (agent-contract-handoff M4: handoff persistence)
--
-- Background
-- ----------
-- v8 schema has:
--   approval_grants(approval_id, agent_id, session_id, command_set_json, ...)
--
-- v9 adds:
--   agent_contract_handoffs           -- persisted SubagentStop contract envelopes
--   agent_contract_handoff_approvals  -- approval decisions linked to handoffs
--   project_context_contracts_history -- audit trail for PCC mutations
--   trg_pcc_history                   -- AFTER UPDATE trigger on project_context_contracts
--
-- Design decisions
-- ----------------
-- * brief_id is NULLABLE -- subagents that return without a brief context
--   still get a handoff row. EXTENSION_POINT for state-machine-completion:
--   downstream briefs can query WHERE brief_id = <N> to verify completion
--   invariants across the handoff record.
-- * agent_contract_handoff_approvals CASCADE-deletes when the handoff row
--   is removed. approval_id FK references approval_grants for audit integrity.
-- * project_context_contracts_history captures before/after payloads at the
--   SQL layer -- no Python path can bypass the trigger.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v8 state; the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.

-- 1. Create the agent_contract_handoffs table.
CREATE TABLE IF NOT EXISTS agent_contract_handoffs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id         TEXT NOT NULL,               -- e.g. "a1b2c3d4e5"
    session_id       TEXT,                        -- CLAUDE_SESSION_ID at SubagentStop time
    workspace        TEXT NOT NULL,               -- FK -> workspaces.name
    brief_id         INTEGER,                     -- NULLABLE FK -> briefs.id; EXTENSION_POINT
    task_status      TEXT NOT NULL,               -- resolved plan_status from contract envelope
    raw_handoff_json TEXT NOT NULL,               -- full contract envelope serialized
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (workspace) REFERENCES workspaces(name),
    FOREIGN KEY (brief_id)  REFERENCES briefs(id)
);

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_workspace ON agent_contract_handoffs(workspace);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_brief     ON agent_contract_handoffs(brief_id);
CREATE INDEX IF NOT EXISTS idx_agent_contract_handoffs_session   ON agent_contract_handoffs(session_id);

-- 2. Create the agent_contract_handoff_approvals table.
CREATE TABLE IF NOT EXISTS agent_contract_handoff_approvals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    handoff_id  INTEGER NOT NULL,                -- FK -> agent_contract_handoffs.id
    approval_id TEXT NOT NULL,                   -- FK -> approval_grants.approval_id
    decision    TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'EXPIRED', 'REVOKED')),
    decided_at  TEXT NOT NULL,
    FOREIGN KEY (handoff_id)  REFERENCES agent_contract_handoffs(id) ON DELETE CASCADE,
    FOREIGN KEY (approval_id) REFERENCES approval_grants(approval_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_contract_handoff_approvals_handoff ON agent_contract_handoff_approvals(handoff_id);

-- 3. Create the project_context_contracts_history table.
CREATE TABLE IF NOT EXISTS project_context_contracts_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_key        TEXT NOT NULL,            -- project_context_contracts.contract_key
    workspace           TEXT NOT NULL,            -- FK -> workspaces.name
    before_payload_json TEXT,                     -- NULL on first insert (no prior value)
    after_payload_json  TEXT NOT NULL,            -- new payload value
    changed_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    changed_by_agent    TEXT,                     -- optional: GAIA_DISPATCH_AGENT at write time
    FOREIGN KEY (workspace) REFERENCES workspaces(name)
);

CREATE INDEX IF NOT EXISTS idx_pcc_history_contract ON project_context_contracts_history(contract_key);

-- 4. Create the AFTER UPDATE trigger on project_context_contracts.
CREATE TRIGGER IF NOT EXISTS trg_pcc_history
AFTER UPDATE ON project_context_contracts
BEGIN
    INSERT INTO project_context_contracts_history (
        contract_key, workspace, before_payload_json, after_payload_json, changed_at
    ) VALUES (
        OLD.contract_key,
        OLD.workspace,
        OLD.payload_json,
        NEW.payload_json,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;
