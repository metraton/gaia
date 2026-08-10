# gaia.retention -- state-based retention for Gaia's own ephemeral footprint.
#
# Public surface:
#   from gaia.retention.fs_rules import (
#       collectable_turn_scoped, collectable_rejected_turns,
#       resolve_grace_hours,
#   )
#   from gaia.retention.liveness import (
#       SessionLiveness, session_liveness, session_liveness_for_contract,
#       session_dead_past_grace,
#   )
#   from gaia.retention.worktree_reclaim import (
#       worktree_needs_capture, capture_worktree_diff, reclaim_worktree,
#   )
#   from gaia.retention.worktree_collector import (
#       EXPLICIT_DEATH_CUT_REASONS, worktree_collect_reason,
#       list_managed_worktrees, collect_worktrees,
#   )
#   from gaia.retention.branch_disposition import (
#       is_merged_into_remote_main, commits_reachable_from_any_remote,
#       content_already_in_main_via_squash, branch_deletion_verdict,
#   )

from gaia.retention.branch_disposition import (  # noqa: E402  (re-export)
    branch_deletion_verdict,
    commits_reachable_from_any_remote,
    content_already_in_main_via_squash,
    is_merged_into_remote_main,
)
from gaia.retention.fs_rules import (  # noqa: E402  (re-export)
    collectable_rejected_turns,
    collectable_turn_scoped,
    resolve_grace_hours,
)
from gaia.retention.liveness import (  # noqa: E402  (re-export)
    SessionLiveness,
    session_dead_past_grace,
    session_liveness,
    session_liveness_for_contract,
)
from gaia.retention.worktree_collector import (  # noqa: E402  (re-export)
    EXPLICIT_DEATH_CUT_REASONS,
    collect_worktrees,
    list_managed_worktrees,
    worktree_collect_reason,
)
from gaia.retention.worktree_reclaim import (  # noqa: E402  (re-export)
    capture_worktree_diff,
    reclaim_worktree,
    worktree_needs_capture,
)

__all__ = [
    "collectable_turn_scoped",
    "collectable_rejected_turns",
    "resolve_grace_hours",
    "SessionLiveness",
    "session_liveness",
    "session_liveness_for_contract",
    "session_dead_past_grace",
    "worktree_needs_capture",
    "capture_worktree_diff",
    "reclaim_worktree",
    "EXPLICIT_DEATH_CUT_REASONS",
    "worktree_collect_reason",
    "list_managed_worktrees",
    "collect_worktrees",
    "is_merged_into_remote_main",
    "commits_reachable_from_any_remote",
    "content_already_in_main_via_squash",
    "branch_deletion_verdict",
]
