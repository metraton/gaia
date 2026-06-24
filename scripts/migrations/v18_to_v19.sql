-- Migration v18 -> v19: audit-immutability gap closure (Task B).
--
-- Adds BEFORE UPDATE trigger bu_approvals_status_has_event on the approvals
-- table to enforce that every approvals.status transition is accompanied by a
-- preceding event row in the append-only approval_events chain.
--
-- The trigger fires when status changes to 'approved', 'rejected', or 'revoked'.
-- It checks that an event row with the matching event_type exists for the
-- approval_id within the same transaction. If no matching event is found it
-- raises ABORT, rolling back the UPDATE.
--
-- This closes the gap where a direct UPDATE approvals SET status = 'approved'
-- could flip the status column without leaving an auditable event, violating
-- the "auditable + immutable" invariant of the approval_events chain.
--
-- Bootstrap note: a fresh install (schema.sql) already includes this trigger
-- via CREATE TRIGGER IF NOT EXISTS; this migration only adds it to existing DBs
-- that were initialized before v19.

CREATE TRIGGER IF NOT EXISTS bu_approvals_status_has_event
BEFORE UPDATE OF status ON approvals
WHEN NEW.status != OLD.status AND NEW.status IN ('approved', 'rejected', 'revoked')
BEGIN
    SELECT CASE
        WHEN (
            SELECT COUNT(*) FROM approval_events
             WHERE approval_id = NEW.id
               AND event_type = CASE NEW.status
                                    WHEN 'approved' THEN 'APPROVED'
                                    WHEN 'rejected' THEN 'REJECTED'
                                    WHEN 'revoked'  THEN 'REVOKED'
                                END
        ) = 0
        THEN RAISE(ABORT, 'approvals: status change requires a preceding event in approval_events')
    END;
END;
