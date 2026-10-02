-- gaia-compat: backward
-- Migration v64 -> v65: a brief remembers the project it was created for.
--
-- briefs.project holds that project's project_identity, so `gaia project move`
-- carries the brief with the project; NULL is a workspace-level brief, which
-- stays in its workspace.
--
-- Backward: one nullable column older code neither reads nor has to write;
-- every existing row stays NULL.

ALTER TABLE briefs ADD COLUMN project TEXT;
