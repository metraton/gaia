-- Migration v40 -> v41: complete the curated-memory history envelope.
--
-- Existing history recorded body, type, description, status, workspace and
-- tombstone transitions, but omitted four semantic ownership/lifecycle fields
-- plus slug renames. Add nullable before/after columns so historical rows stay
-- valid, then replace the trigger so future changes are audited consistently.
-- The bootstrap runner makes each one-line ADD COLUMN idempotent on fresh
-- installs, where schema.sql already contains the target shape.

ALTER TABLE memory_history ADD COLUMN before_name TEXT;
ALTER TABLE memory_history ADD COLUMN after_name TEXT;
ALTER TABLE memory_history ADD COLUMN before_class TEXT;
ALTER TABLE memory_history ADD COLUMN after_class TEXT;
ALTER TABLE memory_history ADD COLUMN before_project_ref TEXT;
ALTER TABLE memory_history ADD COLUMN after_project_ref TEXT;
ALTER TABLE memory_history ADD COLUMN before_initiative TEXT;
ALTER TABLE memory_history ADD COLUMN after_initiative TEXT;

DROP TRIGGER IF EXISTS trg_memory_history;

CREATE TRIGGER trg_memory_history
AFTER UPDATE ON memory
WHEN OLD.name IS NOT NEW.name
   OR OLD.body IS NOT NEW.body
   OR OLD.workspace IS NOT NEW.workspace
   OR OLD.type IS NOT NEW.type
   OR OLD.description IS NOT NEW.description
   OR OLD.status IS NOT NEW.status
   OR OLD.class IS NOT NEW.class
   OR OLD.project_ref IS NOT NEW.project_ref
   OR OLD.initiative IS NOT NEW.initiative
   OR OLD.deleted_at IS NOT NEW.deleted_at
BEGIN
    INSERT INTO memory_history (
        workspace, name,
        before_name, after_name,
        before_workspace, after_workspace,
        before_body, after_body,
        before_type, after_type,
        before_description, after_description,
        before_status, after_status,
        before_class, after_class,
        before_project_ref, after_project_ref,
        before_initiative, after_initiative,
        before_deleted_at, after_deleted_at,
        changed_at
    ) VALUES (
        NEW.workspace, NEW.name,
        OLD.name, NEW.name,
        OLD.workspace, NEW.workspace,
        OLD.body, NEW.body,
        OLD.type, NEW.type,
        OLD.description, NEW.description,
        OLD.status, NEW.status,
        OLD.class, NEW.class,
        OLD.project_ref, NEW.project_ref,
        OLD.initiative, NEW.initiative,
        OLD.deleted_at, NEW.deleted_at,
        strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    );
END;
