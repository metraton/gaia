-- gaia-compat: backward
-- Migration v73 -> v74: the declaration channel.
--
-- memory.effect: what a user's tool does (read | local | runs-project-code),
--   kind=tool only. Written only by `gaia memory declare`, never by add.
-- command_declarations: an exact literal command form a project declares
--   with an effect, matched word for word (gaia/store/declarations.py).
-- Both are recorded, not read by the validator yet (no hook reads them).
--
-- Backward: older code never writes effect or the new table.

ALTER TABLE memory ADD COLUMN effect TEXT;

CREATE TRIGGER IF NOT EXISTS trg_memory_effect_insert
BEFORE INSERT ON memory
WHEN NEW.effect IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind IS NOT 'tool'
            THEN RAISE(ABORT, 'effect belongs to kind=tool only')
        WHEN NEW.effect NOT IN ('read', 'local', 'runs-project-code')
            THEN RAISE(ABORT, 'a tool effect is read, local or runs-project-code')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_effect_update
BEFORE UPDATE OF effect, kind ON memory
WHEN NEW.effect IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind IS NOT 'tool'
            THEN RAISE(ABORT, 'effect belongs to kind=tool only')
        WHEN NEW.effect NOT IN ('read', 'local', 'runs-project-code')
            THEN RAISE(ABORT, 'a tool effect is read, local or runs-project-code')
    END;
END;

CREATE TABLE IF NOT EXISTS command_declarations (
    workspace   TEXT NOT NULL,
    project     TEXT NOT NULL,
    command     TEXT NOT NULL,
    effect      TEXT NOT NULL CHECK (effect IN ('read', 'local', 'runs-project-code')),
    declared_at TEXT NOT NULL,
    PRIMARY KEY (workspace, project, command)
);
