-- Migration v53 -> v54: per-grant history of the host tool calls that reserved.
--
-- A retry must be a different host tool call than the one that was blocked.
-- That property lived only in the OpenCode adapter, so it could not be met by
-- Claude Code at all; enforcing it in reserve_plan_command needs history that
-- outlives a settled item, which clears reservation_tool_use_id.
--
-- The column is NULL on every grant that predates this migration: an empty
-- history conflicts with nothing, so those grants keep reserving exactly as
-- they do today.

ALTER TABLE approval_grants ADD COLUMN reserved_tool_use_ids_json TEXT;
