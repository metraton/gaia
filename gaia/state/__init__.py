"""
gaia.state -- Single source of truth for canonical state machines.

This module centralizes the four canonical state machines used across the
Gaia substrate, exposing both Python tuples (for runtime validation) and
SQL CHECK clauses (for DB enforcement). The schema migration reads from
here to generate CHECK constraints; runtime validators import from here
to enforce the same enums; the diff tool compares both representations
and fails if they ever drift.

State machines exposed:

* ``VALID_PLAN_STATUSES`` -- Contract workflow ``episodes.plan_status``
  (also re-exported by ``hooks.modules.agents.response_contract`` for
  backward compatibility with existing imports).
* ``VALID_BRIEF_STATUSES`` -- Brief lifecycle ``briefs.status``
  (also re-exported by ``gaia.briefs.store`` as ``VALID_STATUSES``).
* ``VALID_PLAN_LIFECYCLE_STATUSES`` -- Plan lifecycle ``plans.status``.
* ``VALID_TASK_STATUSES`` -- Task lifecycle ``tasks.status``.

The tuple form (vs. set / frozenset) preserves a deterministic order for
generated SQL CHECK clauses, which keeps schema diffs stable across
re-runs.

Patterns:
  * Adding a new value -> append to the relevant tuple, run
    ``tools/state/diff_source_of_truth.py`` to confirm DB+Python drift,
    then ship a new schema migration that re-applies the CHECK clause.
  * Removing or renaming a value is **out of scope** for this module --
    a separate brief must own data backfill + migration.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1) Contract workflow -- episodes.plan_status
#
# Canonical source: hooks/modules/agents/response_contract.VALID_PLAN_STATUSES
# That module re-exports this tuple to avoid duplicating values.
# ---------------------------------------------------------------------------
VALID_PLAN_STATUSES: tuple[str, ...] = (
    "IN_PROGRESS",
    "APPROVAL_REQUEST",
    "COMPLETE",
    "BLOCKED",
    "NEEDS_INPUT",
)

# ---------------------------------------------------------------------------
# 2) Brief lifecycle -- briefs.status
#
# Canonical source: gaia.briefs.store.VALID_STATUSES (kept in sync via
# re-export).
# ---------------------------------------------------------------------------
VALID_BRIEF_STATUSES: tuple[str, ...] = (
    "draft",
    "open",
    "in-progress",
    "closed",
    "archived",
)

# ---------------------------------------------------------------------------
# 3) Plan lifecycle -- plans.status
#
# Owned by this module (no pre-existing duplicate to re-export).
# ---------------------------------------------------------------------------
VALID_PLAN_LIFECYCLE_STATUSES: tuple[str, ...] = (
    "draft",
    "active",
    "closed",
)

# ---------------------------------------------------------------------------
# 4) Task lifecycle -- tasks.status
#
# Owned by this module (no pre-existing duplicate to re-export).
# ---------------------------------------------------------------------------
VALID_TASK_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "skipped",
)

# ---------------------------------------------------------------------------
# 5) Acceptance criteria lifecycle -- acceptance_criteria.status (v5; v21 adds 'descoped')
#
# 'blocked' replaces 'skipped' from the task enum: an AC is not "skipped"
# but can be "blocked" (stuck, needs action) before reaching "done".
# Reopen (done -> pending) is intentionally allowed for AC revision.
#
# 'descoped' (v21) is a HARD-TERMINAL status for an AC deliberately removed
# from scope. It is distinct from a task's reopenable 'skipped': there is NO
# legal transition OUT of 'descoped' (see transitions.AC_LIFECYCLE_TRANSITIONS).
# Together with 'done' it forms the TERMINAL set used by verify_brief; adding it
# closes the "false done" gap where a discarded AC had no honest terminal state.
# ---------------------------------------------------------------------------
VALID_AC_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "blocked",
    "descoped",
)

# ---------------------------------------------------------------------------
# 6) Milestone lifecycle -- milestones.status (v5)
#
# Same enum as AC: milestones can be pending, completed (done), or blocked.
# ---------------------------------------------------------------------------
VALID_MILESTONE_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "blocked",
)


# ---------------------------------------------------------------------------
# Convenience: a registry mapping (table, column) -> tuple, used by the
# migration script and the diff tool so neither has to hard-code names.
# ---------------------------------------------------------------------------
STATE_MACHINE_REGISTRY: dict[tuple[str, str], tuple[str, ...]] = {
    ("episodes", "plan_status"): VALID_PLAN_STATUSES,
    ("briefs", "status"): VALID_BRIEF_STATUSES,
    ("plans", "status"): VALID_PLAN_LIFECYCLE_STATUSES,
    ("tasks", "status"): VALID_TASK_STATUSES,
    ("acceptance_criteria", "status"): VALID_AC_STATUSES,
    ("milestones", "status"): VALID_MILESTONE_STATUSES,
}


__all__ = [
    "VALID_PLAN_STATUSES",
    "VALID_BRIEF_STATUSES",
    "VALID_PLAN_LIFECYCLE_STATUSES",
    "VALID_TASK_STATUSES",
    "VALID_AC_STATUSES",
    "VALID_MILESTONE_STATUSES",
    "STATE_MACHINE_REGISTRY",
]
