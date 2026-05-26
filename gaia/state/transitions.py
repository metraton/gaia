"""
gaia.state.transitions -- Legal transition tables for the four state machines.

Each table maps ``current_status -> set(allowed_next_statuses)``. Functions
in this module raise ``ValueError`` on illegal transitions; callers are
responsible for catching and surfacing the message.

The Contract workflow transitions live in
``hooks.modules.agents.state_tracker._LEGAL_TRANSITIONS`` and are imported
here for symmetry; the brief lifecycle transitions live in
``gaia.briefs.store._LEGAL_TRANSITIONS`` and are likewise imported.

The plan and task lifecycle transitions are defined here directly (the
underlying tables ``plans`` and ``tasks`` have no CLI surface yet -- a
separate brief, ``cli-completion``, will add ``gaia plan`` and
``gaia task`` commands that consume these helpers).
"""

from __future__ import annotations

from typing import Mapping

# ---------------------------------------------------------------------------
# Plan lifecycle (plans.status: draft, active, closed)
# ---------------------------------------------------------------------------
PLAN_LIFECYCLE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "draft": frozenset({"active"}),
    "active": frozenset({"closed", "draft"}),  # allow rollback to draft
    "closed": frozenset({"active"}),           # allow reopen
}


def assert_legal_plan_lifecycle(
    old_status: str,
    new_status: str,
) -> None:
    """Raise ``ValueError`` if the plan lifecycle transition is illegal.

    A no-op transition (``old_status == new_status``) is treated as legal
    so callers can use this helper from idempotent set-status flows.
    """
    if old_status == new_status:
        return
    allowed = PLAN_LIFECYCLE_TRANSITIONS.get(old_status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"illegal plan lifecycle transition '{old_status}' -> "
            f"'{new_status}'; allowed from '{old_status}': "
            f"{sorted(allowed) or '(none)'}"
        )


# ---------------------------------------------------------------------------
# Task lifecycle (tasks.status: pending, done, skipped)
# ---------------------------------------------------------------------------
TASK_LIFECYCLE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending": frozenset({"done", "skipped"}),
    "done": frozenset({"pending"}),     # allow reopen for retry
    "skipped": frozenset({"pending"}),  # allow reopen for retry
}


def assert_legal_task_lifecycle(
    old_status: str,
    new_status: str,
) -> None:
    """Raise ``ValueError`` if the task lifecycle transition is illegal."""
    if old_status == new_status:
        return
    allowed = TASK_LIFECYCLE_TRANSITIONS.get(old_status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"illegal task lifecycle transition '{old_status}' -> "
            f"'{new_status}'; allowed from '{old_status}': "
            f"{sorted(allowed) or '(none)'}"
        )


# ---------------------------------------------------------------------------
# Acceptance criteria lifecycle (acceptance_criteria.status: pending, done, blocked)
# ---------------------------------------------------------------------------
AC_LIFECYCLE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending": frozenset({"done", "blocked"}),
    "done": frozenset({"pending"}),      # allow reopen for AC revision
    "blocked": frozenset({"pending"}),   # allow unblock
}


def assert_legal_ac_lifecycle(
    old_status: str,
    new_status: str,
) -> None:
    """Raise ``ValueError`` if the AC lifecycle transition is illegal."""
    if old_status == new_status:
        return
    allowed = AC_LIFECYCLE_TRANSITIONS.get(old_status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"illegal AC lifecycle transition '{old_status}' -> "
            f"'{new_status}'; allowed from '{old_status}': "
            f"{sorted(allowed) or '(none)'}"
        )


# ---------------------------------------------------------------------------
# Milestone lifecycle (milestones.status: pending, done, blocked)
# ---------------------------------------------------------------------------
MILESTONE_LIFECYCLE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending": frozenset({"done", "blocked"}),
    "done": frozenset({"pending"}),      # allow reopen
    "blocked": frozenset({"pending"}),   # allow unblock
}


def assert_legal_milestone_lifecycle(
    old_status: str,
    new_status: str,
) -> None:
    """Raise ``ValueError`` if the milestone lifecycle transition is illegal."""
    if old_status == new_status:
        return
    allowed = MILESTONE_LIFECYCLE_TRANSITIONS.get(old_status, frozenset())
    if new_status not in allowed:
        raise ValueError(
            f"illegal milestone lifecycle transition '{old_status}' -> "
            f"'{new_status}'; allowed from '{old_status}': "
            f"{sorted(allowed) or '(none)'}"
        )


__all__ = [
    "PLAN_LIFECYCLE_TRANSITIONS",
    "TASK_LIFECYCLE_TRANSITIONS",
    "AC_LIFECYCLE_TRANSITIONS",
    "MILESTONE_LIFECYCLE_TRANSITIONS",
    "assert_legal_plan_lifecycle",
    "assert_legal_task_lifecycle",
    "assert_legal_ac_lifecycle",
    "assert_legal_milestone_lifecycle",
]
