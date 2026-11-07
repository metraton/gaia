-- gaia-compat: backward
-- Migration v74 -> v75: the surface a preference governs.
--
-- memory.surface: conversation | sent | document, kind=preference only; the
--   human-register surface whose output the preference sets the form of.
--   NULL = no surface named. A conversation preference travels beside every
--   prompt (hooks/modules/session/session_lifecycle.py::preferences_line).
--
-- Backward: older code never writes surface and reads nothing from it.

ALTER TABLE memory ADD COLUMN surface TEXT;

CREATE TRIGGER IF NOT EXISTS trg_memory_surface_insert
BEFORE INSERT ON memory
WHEN NEW.surface IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind IS NOT 'preference'
            THEN RAISE(ABORT, 'surface belongs to kind=preference only')
        WHEN NEW.surface NOT IN ('conversation', 'sent', 'document')
            THEN RAISE(ABORT, 'a preference surface is conversation, sent or document')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_memory_surface_update
BEFORE UPDATE OF surface, kind ON memory
WHEN NEW.surface IS NOT NULL
BEGIN
    SELECT CASE
        WHEN NEW.kind IS NOT 'preference'
            THEN RAISE(ABORT, 'surface belongs to kind=preference only')
        WHEN NEW.surface NOT IN ('conversation', 'sent', 'document')
            THEN RAISE(ABORT, 'a preference surface is conversation, sent or document')
    END;
END;
