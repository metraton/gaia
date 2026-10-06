-- gaia-compat: backward
-- Migration v65 -> v66: workspaces.status gains 'retired'.
--
-- A retired workspace is hidden from listings and keeps its rows; `gaia
-- workspace retire` and `gaia workspace curate` set it, `gaia workspace
-- declare` clears it. Every listing now filters on status, hence the index.
-- Sources retired before v66 are still 'active'; `gaia workspace curate`
-- marks them, so this file rewrites no row.
--
-- Backward: older code compares status only with 'missing', so it reads a
-- retired row as it read the same row before.

CREATE INDEX IF NOT EXISTS idx_workspaces_status ON workspaces(status);
