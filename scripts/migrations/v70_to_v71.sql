-- gaia-compat: backward
-- Migration v70 -> v71: memory rows carry their kind, their owner level and,
-- for a preference, the moment it stops holding.
--
-- kind:        fact | preference | decision | lesson | pending | defect
-- owner_level: user | workspace | group | project | gaia; NULL for a legacy
--              row with neither initiative nor project_ref, which is listed
--              for hand placement, never absorbed by its workspace.
-- until:       a preference's expiry -- an ISO date, or memory:<ws>/<slug>
--              (holds while that row is live).
--
-- The backfill is mechanical: the claim word a description opens with, else
-- the type's default; the owner from the type, the host initiative, the
-- project anchor, or an initiative that names a registered project. The
-- triggers are created after the backfill, so rows the matrix forbids stay
-- readable and are counted for curation; they refuse a new placement the
-- matrix forbids and a placement change on an existing row.
--
-- Backward: code older than v71 never writes kind, owner_level or until, so
-- its inserts pass the triggers (NULL kind is not judged) and read as before.

ALTER TABLE memory ADD COLUMN kind TEXT;
ALTER TABLE memory ADD COLUMN owner_level TEXT;
ALTER TABLE memory ADD COLUMN until TEXT;

UPDATE memory SET kind = CASE
    WHEN type = 'project' AND lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('bug', 'defect', 'defecto', 'pending', 'pendiente') THEN 'pending'
    WHEN lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('preference', 'preferencia', 'regla', 'principio', 'requisito') THEN 'preference'
    WHEN lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('fact', 'hecho', 'measured', 'medido') THEN 'fact'
    WHEN lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('decision', 'decisión') THEN 'decision'
    WHEN lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('lesson', 'aprendizaje') THEN 'lesson'
    WHEN lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('pending', 'pendiente') THEN 'pending'
    WHEN lower(rtrim(substr(description, 1, instr(description || ' ', ' ') - 1), ':'))
         IN ('bug', 'defect', 'defecto') THEN 'defect'
    WHEN type = 'decision' THEN 'decision'
    WHEN type = 'negative' THEN 'lesson'
    WHEN type = 'feedback' THEN 'defect'
    WHEN type = 'project' AND class = 'thread' THEN 'pending'
    ELSE 'fact'
END
WHERE kind IS NULL;

UPDATE memory SET owner_level = CASE
    WHEN type = 'user' THEN 'user'
    WHEN initiative = 'gaia_system' THEN 'gaia'
    WHEN project_ref IS NOT NULL THEN 'project'
    WHEN initiative IS NOT NULL AND EXISTS (
        SELECT 1 FROM projects p
        WHERE lower(replace(replace(p.name, '-', '_'), '.', '_')) = memory.initiative
    ) THEN 'project'
    WHEN initiative IS NOT NULL THEN 'group'
END
WHERE owner_level IS NULL;

CREATE TRIGGER IF NOT EXISTS trg_memory_placement_insert
BEFORE INSERT ON memory
WHEN NEW.kind IS NOT NULL OR NEW.owner_level IS NOT NULL OR NEW.until IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind NOT IN ('fact', 'preference', 'decision', 'lesson', 'pending', 'defect')
            THEN RAISE(ABORT, 'memory kind must be fact, preference, decision, lesson, pending or defect')
        WHEN NEW.owner_level NOT IN ('user', 'workspace', 'group', 'project', 'gaia')
            THEN RAISE(ABORT, 'memory owner_level must be user, workspace, group, project or gaia')
        WHEN NEW.owner_level = 'user' AND NEW.kind NOT IN ('fact', 'preference')
            THEN RAISE(ABORT, 'the user owns facts and preferences only; a decision, lesson or pending belongs to a workspace, group or project, a defect to gaia')
        WHEN NEW.owner_level = 'workspace' AND NEW.kind NOT IN ('fact', 'preference', 'decision')
            THEN RAISE(ABORT, 'a workspace owns facts, preferences and decisions; a lesson or pending belongs to a group or project, a defect to gaia')
        WHEN NEW.owner_level IN ('group', 'project') AND NEW.kind = 'defect'
            THEN RAISE(ABORT, 'a defect is Gaia failing in use and belongs to gaia; a bug in the product is this project''s pending')
        WHEN NEW.owner_level = 'gaia' AND NEW.kind NOT IN ('lesson', 'defect')
            THEN RAISE(ABORT, 'gaia owns lessons and defects only; a fact, decision or pending about Gaia belongs to the gaia project')
        WHEN NEW.until IS NOT NULL AND NEW.kind IS NOT 'preference'
            THEN RAISE(ABORT, 'until belongs to a preference only: the date or the memory:<ws>/<slug> row while which it holds')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_placement_update
BEFORE UPDATE OF kind, owner_level, until ON memory
WHEN NEW.kind IS NOT NULL OR NEW.owner_level IS NOT NULL OR NEW.until IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind NOT IN ('fact', 'preference', 'decision', 'lesson', 'pending', 'defect')
            THEN RAISE(ABORT, 'memory kind must be fact, preference, decision, lesson, pending or defect')
        WHEN NEW.owner_level NOT IN ('user', 'workspace', 'group', 'project', 'gaia')
            THEN RAISE(ABORT, 'memory owner_level must be user, workspace, group, project or gaia')
        WHEN NEW.owner_level = 'user' AND NEW.kind NOT IN ('fact', 'preference')
            THEN RAISE(ABORT, 'the user owns facts and preferences only; a decision, lesson or pending belongs to a workspace, group or project, a defect to gaia')
        WHEN NEW.owner_level = 'workspace' AND NEW.kind NOT IN ('fact', 'preference', 'decision')
            THEN RAISE(ABORT, 'a workspace owns facts, preferences and decisions; a lesson or pending belongs to a group or project, a defect to gaia')
        WHEN NEW.owner_level IN ('group', 'project') AND NEW.kind = 'defect'
            THEN RAISE(ABORT, 'a defect is Gaia failing in use and belongs to gaia; a bug in the product is this project''s pending')
        WHEN NEW.owner_level = 'gaia' AND NEW.kind NOT IN ('lesson', 'defect')
            THEN RAISE(ABORT, 'gaia owns lessons and defects only; a fact, decision or pending about Gaia belongs to the gaia project')
        WHEN NEW.until IS NOT NULL AND NEW.kind IS NOT 'preference'
            THEN RAISE(ABORT, 'until belongs to a preference only: the date or the memory:<ws>/<slug> row while which it holds')
    END;
END;
