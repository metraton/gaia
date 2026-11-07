-- gaia-compat: backward
-- Migration v72 -> v73: a signal names every retirement and every owner.
--
-- op = 'delete' now means the row left the live set: a tombstone, a hard
-- delete, a demotion to class=log, or a supersedes link naming it as the old
-- row. A workspace-level row (no initiative, owner_level = workspace) raises
-- signals under subject 'workspace:<ws>', as user and project rows already do.
--
-- Backward: the table is unchanged; older code reads the same columns.

DROP TRIGGER IF EXISTS trg_signals_memory_insert;
DROP TRIGGER IF EXISTS trg_signals_memory_update;
DROP TRIGGER IF EXISTS trg_signals_memory_delete;

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_insert
AFTER INSERT ON memory
WHEN NEW.type = 'user' OR NEW.initiative IS NOT NULL OR NEW.owner_level = 'workspace'
BEGIN
    INSERT INTO signals (subject, source, workspace, ref, op)
    VALUES (
        CASE WHEN NEW.type = 'user' THEN 'user'
             WHEN NEW.initiative IS NOT NULL THEN 'project:' || NEW.initiative
             ELSE 'workspace:' || NEW.workspace END,
        'memory', NEW.workspace, NEW.name, 'insert'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_update
AFTER UPDATE ON memory
WHEN (NEW.type = 'user' OR NEW.initiative IS NOT NULL OR NEW.owner_level = 'workspace'
      OR OLD.type = 'user' OR OLD.initiative IS NOT NULL OR OLD.owner_level = 'workspace')
 AND (OLD.name IS NOT NEW.name
   OR OLD.body IS NOT NEW.body
   OR OLD.type IS NOT NEW.type
   OR OLD.description IS NOT NEW.description
   OR OLD.status IS NOT NEW.status
   OR OLD.class IS NOT NEW.class
   OR OLD.project_ref IS NOT NEW.project_ref
   OR OLD.initiative IS NOT NEW.initiative
   OR OLD.deleted_at IS NOT NEW.deleted_at
   OR OLD.audience IS NOT NEW.audience
   OR OLD.measured_at IS NOT NEW.measured_at
   OR OLD.method IS NOT NEW.method)
BEGIN
    INSERT INTO signals (subject, source, workspace, ref, op)
    VALUES (
        CASE
            WHEN NEW.type = 'user' THEN 'user'
            WHEN NEW.initiative IS NOT NULL THEN 'project:' || NEW.initiative
            WHEN NEW.owner_level = 'workspace' THEN 'workspace:' || NEW.workspace
            WHEN OLD.type = 'user' THEN 'user'
            WHEN OLD.initiative IS NOT NULL THEN 'project:' || OLD.initiative
            ELSE 'workspace:' || OLD.workspace
        END,
        'memory', NEW.workspace, NEW.name,
        CASE WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN 'delete'
             WHEN NEW.class = 'log' AND OLD.class IS NOT 'log' THEN 'delete'
             ELSE 'update' END
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_delete
AFTER DELETE ON memory
WHEN OLD.deleted_at IS NULL
 AND (OLD.type = 'user' OR OLD.initiative IS NOT NULL OR OLD.owner_level = 'workspace')
BEGIN
    INSERT INTO signals (subject, source, workspace, ref, op)
    VALUES (
        CASE WHEN OLD.type = 'user' THEN 'user'
             WHEN OLD.initiative IS NOT NULL THEN 'project:' || OLD.initiative
             ELSE 'workspace:' || OLD.workspace END,
        'memory', OLD.workspace, OLD.name, 'delete'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_superseded
AFTER INSERT ON memory_links
WHEN NEW.kind = 'supersedes'
BEGIN
    INSERT INTO signals (subject, source, workspace, ref, op)
    SELECT CASE WHEN m.type = 'user' THEN 'user'
                WHEN m.initiative IS NOT NULL THEN 'project:' || m.initiative
                ELSE 'workspace:' || m.workspace END,
           'memory', m.workspace, m.name, 'delete'
    FROM memory m
    WHERE m.workspace = COALESCE(NEW.dst_workspace, NEW.workspace)
      AND m.name = NEW.dst_name
      AND m.deleted_at IS NULL
      AND (m.type = 'user' OR m.initiative IS NOT NULL OR m.owner_level = 'workspace');
END;
