-- gaia-compat: backward
-- Migration v71 -> v72: kind=tool, a program built for the user.
--
-- binary:   the executable on PATH the row is about (NOT NULL for a tool,
--           verified by the writer; one live tool row per binary and workspace).
-- replaces: the CLI the hooks judge that binary as (optional; only a CLI the
--           classifier models). Read by hooks/modules/security/cli_aliases.py,
--           which replaces the GAIA_CLI_ALIASES environment variable.
--
-- The placement triggers are recreated with the closed kind list that now
-- holds tool, user-owned only, and the two tool-column rules.
--
-- Backward: code older than v72 never writes kind=tool, binary or replaces,
-- so its inserts pass the triggers and read as before.

ALTER TABLE memory ADD COLUMN binary TEXT;
ALTER TABLE memory ADD COLUMN replaces TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_tool_binary
    ON memory(workspace, binary) WHERE binary IS NOT NULL AND deleted_at IS NULL;

DROP TRIGGER IF EXISTS trg_memory_placement_insert;
DROP TRIGGER IF EXISTS trg_memory_placement_update;

CREATE TRIGGER IF NOT EXISTS trg_memory_placement_insert
BEFORE INSERT ON memory
WHEN NEW.kind IS NOT NULL OR NEW.owner_level IS NOT NULL OR NEW.until IS NOT NULL
   OR NEW.binary IS NOT NULL OR NEW.replaces IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind NOT IN ('fact', 'preference', 'decision', 'lesson', 'pending', 'defect', 'tool')
            THEN RAISE(ABORT, 'memory kind must be fact, preference, decision, lesson, pending, defect or tool')
        WHEN NEW.owner_level NOT IN ('user', 'workspace', 'group', 'project', 'gaia')
            THEN RAISE(ABORT, 'memory owner_level must be user, workspace, group, project or gaia')
        WHEN NEW.kind = 'tool' AND NEW.owner_level IS NOT 'user'
            THEN RAISE(ABORT, 'a tool belongs to the user (--type=user --kind=tool --binary=<name>): it is a program built for him, whichever project it serves')
        WHEN NEW.owner_level = 'user' AND NEW.kind NOT IN ('fact', 'preference', 'tool')
            THEN RAISE(ABORT, 'the user owns facts, preferences and tools only; a decision, lesson or pending belongs to a workspace, group or project, a defect to gaia')
        WHEN NEW.owner_level = 'workspace' AND NEW.kind NOT IN ('fact', 'preference', 'decision')
            THEN RAISE(ABORT, 'a workspace owns facts, preferences and decisions; a lesson or pending belongs to a group or project, a defect to gaia')
        WHEN NEW.owner_level IN ('group', 'project') AND NEW.kind = 'defect'
            THEN RAISE(ABORT, 'a defect is Gaia failing in use and belongs to gaia; a bug in the product is this project''s pending')
        WHEN NEW.owner_level = 'gaia' AND NEW.kind NOT IN ('lesson', 'defect')
            THEN RAISE(ABORT, 'gaia owns lessons and defects only; a fact, decision or pending about Gaia belongs to the gaia project')
        WHEN NEW.until IS NOT NULL AND NEW.kind IS NOT 'preference'
            THEN RAISE(ABORT, 'until belongs to a preference only: the date or the memory:<ws>/<slug> row while which it holds')
        WHEN NEW.kind = 'tool' AND NEW.binary IS NULL
            THEN RAISE(ABORT, 'a tool names its program: binary is required')
        WHEN NEW.kind IS NOT 'tool' AND (NEW.binary IS NOT NULL OR NEW.replaces IS NOT NULL)
            THEN RAISE(ABORT, 'binary and replaces belong to kind=tool only')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_placement_update
BEFORE UPDATE OF kind, owner_level, until, binary, replaces ON memory
WHEN NEW.kind IS NOT NULL OR NEW.owner_level IS NOT NULL OR NEW.until IS NOT NULL
   OR NEW.binary IS NOT NULL OR NEW.replaces IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind NOT IN ('fact', 'preference', 'decision', 'lesson', 'pending', 'defect', 'tool')
            THEN RAISE(ABORT, 'memory kind must be fact, preference, decision, lesson, pending, defect or tool')
        WHEN NEW.owner_level NOT IN ('user', 'workspace', 'group', 'project', 'gaia')
            THEN RAISE(ABORT, 'memory owner_level must be user, workspace, group, project or gaia')
        WHEN NEW.kind = 'tool' AND NEW.owner_level IS NOT 'user'
            THEN RAISE(ABORT, 'a tool belongs to the user (--type=user --kind=tool --binary=<name>): it is a program built for him, whichever project it serves')
        WHEN NEW.owner_level = 'user' AND NEW.kind NOT IN ('fact', 'preference', 'tool')
            THEN RAISE(ABORT, 'the user owns facts, preferences and tools only; a decision, lesson or pending belongs to a workspace, group or project, a defect to gaia')
        WHEN NEW.owner_level = 'workspace' AND NEW.kind NOT IN ('fact', 'preference', 'decision')
            THEN RAISE(ABORT, 'a workspace owns facts, preferences and decisions; a lesson or pending belongs to a group or project, a defect to gaia')
        WHEN NEW.owner_level IN ('group', 'project') AND NEW.kind = 'defect'
            THEN RAISE(ABORT, 'a defect is Gaia failing in use and belongs to gaia; a bug in the product is this project''s pending')
        WHEN NEW.owner_level = 'gaia' AND NEW.kind NOT IN ('lesson', 'defect')
            THEN RAISE(ABORT, 'gaia owns lessons and defects only; a fact, decision or pending about Gaia belongs to the gaia project')
        WHEN NEW.until IS NOT NULL AND NEW.kind IS NOT 'preference'
            THEN RAISE(ABORT, 'until belongs to a preference only: the date or the memory:<ws>/<slug> row while which it holds')
        WHEN NEW.kind = 'tool' AND NEW.binary IS NULL
            THEN RAISE(ABORT, 'a tool names its program: binary is required')
        WHEN NEW.kind IS NOT 'tool' AND (NEW.binary IS NOT NULL OR NEW.replaces IS NOT NULL)
            THEN RAISE(ABORT, 'binary and replaces belong to kind=tool only')
    END;
END;
