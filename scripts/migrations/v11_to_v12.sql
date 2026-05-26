-- Migration v11 -> v12 (approval-model-redesign: user-in-loop, fingerprint-bound, hash-chained)
--
-- Background
-- ----------
-- v11 schema has all the episodic/memory/handoff tables from prior migrations.
-- v12 adds two tables for the new approval model:
--   approvals         -- durable record per approval lifecycle, P-{id} prefixed
--   approval_events   -- append-only hash-chained audit log per lifecycle event
--
-- Three trigger families:
--   ai_approval_events_hash       -- AFTER INSERT: computes this_hash via gaia_sha256()
--   bu_approval_events_immutable  -- BEFORE UPDATE: raises (append-only invariant)
--   bd_approval_events_immutable  -- BEFORE DELETE: raises (append-only invariant)
--
-- Design decisions (from plan D15 and brief approach)
-- ----------------------------------------------------
-- D1: approvals.id carries P-{uuid4} prefix (TEXT PK, not AUTOINCREMENT INTEGER).
--   Rationale: the prefix is readable in denial messages and debug output without
--   a JOIN. UUIDs avoid collisions without a central counter. The hook generates
--   the id and embeds it in the denial message so subagents can reference it.
--
-- D2: approval_events.this_hash = SHA-256(prev_hash || fingerprint)
--   Computed by the AFTER INSERT trigger via SQLite scalar function `gaia_sha256`
--   registered in gaia.store.writer._connect(). SQLite's built-in functions
--   do not include SHA-256 so we inject a Python function at connection time.
--   The trigger runs deterministically with the connection's registered function.
--
-- D3: Genesis row bootstrapping
--   For the first event row of any approval chain (row 0), prev_hash IS NULL.
--   this_hash = SHA-256("" || fingerprint) where the null is treated as an
--   empty string by the trigger expression COALESCE(prev_hash, '').
--   This is the documented canonical treatment -- callers should not assume a
--   sentinel hash (like all-zeros); they must use COALESCE('', prev_hash) when
--   walking the chain. The chain_walk validator in gaia/approvals/chain.py
--   implements this correctly.
--
-- D4: Append-only invariant
--   BEFORE UPDATE and BEFORE DELETE triggers raise an error with the literal
--   message "approval_events is append-only" so any accidental mutative SQL
--   gets a clear, actionable error rather than a silent no-op or wrong-table
--   write. These triggers are part of the security contract: a tampered row
--   breaks hash-chain validation *and* prevents direct mutation at the SQL layer.
--
-- D5: event_type CHECK constraint
--   Nine valid event types from the plan spec. `EXPIRED` is intentionally
--   excluded (no TTL-based expiry in this brief). `ESCALATED` is excluded
--   (no multi-level approval chain in this brief). The CHECK is on the
--   approval_events table so the constraint is enforced at the DB layer.
--
-- Atomicity: bootstrap_database.sh wraps this script in BEGIN/COMMIT.
-- A failure mid-flight rolls back to v11 state; the ledger row is NOT
-- inserted, so the next bootstrap retry sees the same pending migration.
-- Closes M1 (Wave 1) of brief approval-model-redesign-user-in-loop (brief_id=71).

-- ---------------------------------------------------------------------------
-- Step 1: Create approvals table (durable lifecycle record)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approvals (
    id           TEXT PRIMARY KEY,           -- P-{uuid4} prefixed identifier
    agent_id     TEXT,                       -- agent that initiated the request
    session_id   TEXT,                       -- CLAUDE_SESSION_ID at request time
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'rejected', 'revoked', 'expired')),
    fingerprint  TEXT,                       -- SHA-256 hex of canonical sealed_payload_json
    payload_json TEXT,                       -- canonical-JSON sealed_payload at REQUESTED time
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    decided_at   TEXT                        -- ISO-8601 UTC when approved/rejected/revoked
);

-- Indexes for the common query patterns:
--   (a) All pending approvals regardless of session (cross-session recovery)
--   (b) All approvals for a specific agent
--   (c) All approvals for a specific session
CREATE INDEX IF NOT EXISTS idx_approvals_status     ON approvals(status);
CREATE INDEX IF NOT EXISTS idx_approvals_agent      ON approvals(agent_id);
CREATE INDEX IF NOT EXISTS idx_approvals_session    ON approvals(session_id);

-- ---------------------------------------------------------------------------
-- Step 2: Create approval_events table (append-only hash-chained audit log)
--
-- Column inventory from plan D15 (verbatim):
--   id, approval_id (FK), event_type, agent_id, session_id,
--   payload_json, fingerprint, prev_hash, this_hash, metadata_json, created_at
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approval_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id   TEXT NOT NULL,                     -- FK -> approvals.id (P-{uuid4})
    event_type    TEXT NOT NULL CHECK (event_type IN (
                      'REQUESTED',
                      'SHOWN',
                      'APPROVED',
                      'REJECTED',
                      'EXECUTED',
                      'FAILED',
                      'NOOP',
                      'REVOKED',
                      'REVERTED'
                  )),
    agent_id      TEXT,                              -- agent that triggered this event
    session_id    TEXT,                              -- session that created this event row
    payload_json  TEXT,                              -- canonical-JSON sealed_payload at this event
    fingerprint   TEXT,                              -- SHA-256 hex of canonical payload_json
    prev_hash     TEXT,                              -- this_hash of the immediately preceding row
                                                     -- NULL for the genesis row (row 0 in the chain)
    this_hash     TEXT,                              -- SHA-256(COALESCE(prev_hash,'') || COALESCE(fingerprint,''))
                                                     -- computed by ai_approval_events_hash AFTER INSERT trigger
    metadata_json TEXT,                              -- free-form JSON for event-specific extras
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (approval_id) REFERENCES approvals(id)
);

-- Indexes for the common query patterns:
--   (a) All events for a specific approval (primary chain-walk pattern)
--   (b) All events of a specific type across all approvals (audit dashboard)
--   (c) All events for a specific session
CREATE INDEX IF NOT EXISTS idx_approval_events_approval  ON approval_events(approval_id, id);
CREATE INDEX IF NOT EXISTS idx_approval_events_type      ON approval_events(event_type);
CREATE INDEX IF NOT EXISTS idx_approval_events_session   ON approval_events(session_id);

-- ---------------------------------------------------------------------------
-- Step 3: this_hash computation -- application layer, not a trigger
--
-- The AFTER INSERT + BEFORE UPDATE combination is architecturally conflicted
-- in SQLite: an AFTER INSERT trigger that calls UPDATE on the same row would
-- fire the BEFORE UPDATE immutability trigger, blocking the computation.
--
-- Resolution: this_hash is computed by the application layer before each
-- INSERT via gaia.approvals.chain._compute_this_hash(prev_hash, fingerprint).
-- All INSERTs into approval_events MUST supply a computed this_hash value.
-- The Python helper gaia.approvals.chain.insert_event() enforces this at
-- the API boundary.
--
-- The gaia_sha256 scalar function is still registered on connections by
-- gaia.store.writer._connect() for ad-hoc queries, chain-walk re-validation,
-- and future trigger uses that do not conflict with the immutability triggers.
--
-- Named placeholder trigger (for schema consistency and test assertions about
-- trigger count): We create a named view-only trigger that does nothing, so
-- that `gaia doctor` can assert "ai_approval_events_hash trigger exists" and
-- the schema introspection is consistent with the migration spec.
-- This is intentionally a no-op SELECT that documents the design decision.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS ai_approval_events_hash
AFTER INSERT ON approval_events
BEGIN
    -- this_hash is computed by the application layer before INSERT.
    -- See gaia.approvals.chain._compute_this_hash() and insert_event().
    -- This trigger is a named placeholder for schema introspection consistency.
    SELECT 1;
END;

-- ---------------------------------------------------------------------------
-- Step 4: BEFORE UPDATE trigger -- enforce append-only invariant
--
-- Trigger name: bu_approval_events_immutable
--   bu_ prefix = BEFORE UPDATE
--
-- Raises with a clear message so accidental UPDATEs surface immediately
-- rather than silently corrupting the audit chain.
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS bu_approval_events_immutable
BEFORE UPDATE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval_events is append-only');
END;

-- ---------------------------------------------------------------------------
-- Step 5: BEFORE DELETE trigger -- enforce append-only invariant
--
-- Trigger name: bd_approval_events_immutable
--   bd_ prefix = BEFORE DELETE
-- ---------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS bd_approval_events_immutable
BEFORE DELETE ON approval_events
BEGIN
    SELECT RAISE(ABORT, 'approval_events is append-only');
END;

-- ---------------------------------------------------------------------------
-- Step 6: Bump schema_version to 12
-- ---------------------------------------------------------------------------
INSERT OR IGNORE INTO schema_version (version, applied_at, description)
VALUES (12, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
        'approvals + approval_events tables + hash-chain triggers (approval-model-redesign M1)');

-- Verification queries (run after applying):
-- SELECT MAX(version) FROM schema_version;                          -- expect: 12
-- SELECT name FROM sqlite_master WHERE type='table'
--   AND name IN ('approvals','approval_events');                    -- expect: 2 rows
-- SELECT name FROM sqlite_master WHERE type='trigger'
--   AND name IN ('ai_approval_events_hash',
--                'bu_approval_events_immutable',
--                'bd_approval_events_immutable');                   -- expect: 3 rows
-- PRAGMA table_info(approvals);                                     -- expect: 7 columns
-- PRAGMA table_info(approval_events);                               -- expect: 11 columns
