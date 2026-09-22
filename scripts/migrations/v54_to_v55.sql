-- Migration v54 -> v55: the workspace directory, recorded by the scan.
--
-- Worktrees are created under <workspace root>/.project-worktrees, and a
-- workspace name says nothing reliable about which directory it is.
--
-- Structure only: existing rows keep NULL until their next `gaia scan`, and a
-- worktree cannot be created for a workspace whose root is still NULL.

ALTER TABLE workspaces ADD COLUMN root_path TEXT;
