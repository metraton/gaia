-- Migration v7 -> v8 (agent-contract-handoff M3: approval_grants table)
--
-- Background
-- ----------
-- v7 schema has:
--   workspaces(name, identity, created_at, last_scan_at)
--
-- v8 adds the approval_grants table for DB-backed T3 command approval storage.
-- This replaces the filesystem JSON approval store (.claude/cache/approvals/).
-- Per D5 / D10: no TTL column (enforced at query time via created_at + 10 min);
-- byte-for-byte command match; each command_set item is single-use.
--
-- Design decision: v8 bump (option a)
-- ------------------------------------
-- The approval_grants table was previously appended to v6_to_v7.sql as a
-- supplemental idempotent block.  That approach conflated two unrelated
-- concerns in one migration version.  v8 is the correct version for M3:
-- each independent feature gets its own migration version (precedent from
-- v5->v6 evidence table decision).  The M3 appended block has been removed
-- from v6_to_v7.sql and moved here.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v7 state and the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.

-- 1. Create the approval_grants table.
CREATE TABLE IF NOT EXISTS approval_grants (
    approval_id          TEXT PRIMARY KEY,           -- nonce, e.g. 32-char hex
    agent_id             TEXT,                       -- agent that initiated the request
    session_id           TEXT,                       -- CLAUDE_SESSION_ID at grant time
    command_set_json     TEXT NOT NULL,              -- JSON array of {command, rationale}
    scope                TEXT NOT NULL DEFAULT 'COMMAND_SET',  -- grant scope type
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at           TEXT,                       -- ISO8601 or NULL (TTL enforced at query time)
    status               TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|CONSUMED|REVOKED|EXPIRED
    consumed_indexes_json TEXT,                      -- JSON array of consumed command_set indexes
    consumed_at          TEXT,                       -- ISO8601 when all items consumed
    revoked_at           TEXT                        -- ISO8601 when explicitly revoked
);

-- 2. Indexes to support agent- and session-scoped approval queries.
CREATE INDEX IF NOT EXISTS idx_approval_grants_agent   ON approval_grants(agent_id);
CREATE INDEX IF NOT EXISTS idx_approval_grants_session ON approval_grants(session_id);
CREATE INDEX IF NOT EXISTS idx_approval_grants_status  ON approval_grants(status);
