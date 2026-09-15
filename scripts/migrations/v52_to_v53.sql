-- Migration v52 -> v53: contract-scoped worktree-capture evidence.
--
-- A turn without a brief still has an agent_contract_handoffs row -- every
-- turn is born with one. This column gives capture-before-recycle
-- (gaia.retention.worktree_reclaim) a home for a dirty worktree's diff when
-- no brief/AC exists to attribute it to, instead of refusing the release.

ALTER TABLE agent_contract_handoffs ADD COLUMN worktree_capture_json TEXT;
