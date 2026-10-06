-- gaia-compat: backward
-- Migration v60 -> v61: memory links that cross owners.
--
-- A type=user row lives in the _gaia_user scope while the row it replaces may
-- still sit under the workspace it was first written in, and a link could only
-- join two rows of one workspace, so that lineage was refused. `dst_workspace`
-- names the dst row's workspace when it differs from the link's own; NULL keeps
-- the old meaning, so every existing edge is unchanged.
--
-- Backward: a nullable column older code neither reads nor has to write.

ALTER TABLE memory_links ADD COLUMN dst_workspace TEXT;

CREATE INDEX IF NOT EXISTS idx_memory_links_dst_workspace
    ON memory_links(dst_workspace, dst_name, kind) WHERE dst_workspace IS NOT NULL;
