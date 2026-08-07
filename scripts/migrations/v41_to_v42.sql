-- v42: ordered, reservation-based plan-first COMMAND_SET execution.
ALTER TABLE approval_grants ADD COLUMN request_fingerprint TEXT;
ALTER TABLE approval_grants ADD COLUMN next_index INTEGER NOT NULL DEFAULT 0;
ALTER TABLE approval_grants ADD COLUMN reservation_index INTEGER;
ALTER TABLE approval_grants ADD COLUMN reservation_session_id TEXT;
ALTER TABLE approval_grants ADD COLUMN reservation_tool_use_id TEXT;
ALTER TABLE approval_grants ADD COLUMN reservation_at TEXT;
ALTER TABLE approval_grants ADD COLUMN failed_index INTEGER;
ALTER TABLE approval_grants ADD COLUMN failure_reason TEXT;
ALTER TABLE approval_grants ADD COLUMN source TEXT NOT NULL DEFAULT 'legacy';
