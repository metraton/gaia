-- gaia-compat: backward
-- Migration v67 -> v68: signals, the change feed a working session reads.
--
-- A curated memory write (a user row, or a row keyed by an initiative) lands
-- one pointer row in `signals`; the per-prompt hook reads the rows other
-- sessions wrote since its own cursor and shows them as one line. Telemetry
-- columns are outside the UPDATE trigger's column list, so reads never fire it.
--
-- Backward: a new table and triggers that only record. Code older than v68
-- writes memory as before; its rows carry no session and read as unattributed.

CREATE TABLE IF NOT EXISTS signals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    subject    TEXT NOT NULL,
    source     TEXT NOT NULL,
    workspace  TEXT,
    ref        TEXT,
    op         TEXT NOT NULL CHECK (op IN ('insert', 'update', 'delete')),
    session_id TEXT
);

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_insert
AFTER INSERT ON memory
WHEN NEW.type = 'user' OR NEW.initiative IS NOT NULL
BEGIN
    INSERT INTO signals (subject, source, workspace, ref, op)
    VALUES (
        CASE WHEN NEW.type = 'user' THEN 'user' ELSE 'project:' || NEW.initiative END,
        'memory', NEW.workspace, NEW.name, 'insert'
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_update
AFTER UPDATE ON memory
WHEN (NEW.type = 'user' OR NEW.initiative IS NOT NULL
      OR OLD.type = 'user' OR OLD.initiative IS NOT NULL)
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
            WHEN OLD.type = 'user' THEN 'user'
            ELSE 'project:' || OLD.initiative
        END,
        'memory', NEW.workspace, NEW.name,
        CASE WHEN OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL
             THEN 'delete' ELSE 'update' END
    );
END;

CREATE TRIGGER IF NOT EXISTS trg_signals_memory_delete
AFTER DELETE ON memory
WHEN OLD.deleted_at IS NULL AND (OLD.type = 'user' OR OLD.initiative IS NOT NULL)
BEGIN
    INSERT INTO signals (subject, source, workspace, ref, op)
    VALUES (
        CASE WHEN OLD.type = 'user' THEN 'user' ELSE 'project:' || OLD.initiative END,
        'memory', OLD.workspace, OLD.name, 'delete'
    );
END;
