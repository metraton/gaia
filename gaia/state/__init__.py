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
    "NEEDS_VERIFICATION",
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
# 7) Verification type -- agent_contract_handoff evidence_report.verification.type
#    AND task_gates.verification_type (v34, harness R1-A)
#
# This tuple has TWO consumers that must stay identical:
#   * The pure form-layer validator (gaia.contract.validator.validate_form)
#     enforces it on the *type* field of a contract-envelope verification block,
#     the same way that module consumes VALID_PLAN_STATUSES -- an imported tuple
#     with a byte-identical stdlib fallback.
#   * As of v34 it is ALSO the CHECK on the persisted task_gates.verification_type
#     column, so it IS registered in STATE_MACHINE_REGISTRY below (see the
#     (task_gates, verification_type) entry). tools/state/diff_source_of_truth.py
#     holds the SQL CHECK and this Python tuple enforceably identical.
#
# Ordered, extensible initial set:
#   * "command" / "code" -- DETERMINISTIC oracle types: a third-party verifier
#     runs a declared command/oracle (the two names are synonyms for the two
#     shapes of a deterministic check).
#   * "semantic"    -- requires human / rubric validation; the contract stays
#     open pending that judgement.
#   * "self_review" -- the agent states what it checked and observed.
# ---------------------------------------------------------------------------
VALID_VERIFICATION_TYPES: tuple[str, ...] = (
    "command",
    "code",
    "semantic",
    "self_review",
)

# ---------------------------------------------------------------------------
# 8) Gate lifecycle -- task_gates.status (harness B3/T3)
#
# Same shape as task_gates.verification_type (item 7 above): this tuple is
# the vocabulary a gate's *result* is reported in once a verifier runs it --
# 'pending' (not yet run) -> 'pass' | 'fail'.
#
# As of v36 (scripts/migrations/v35_to_v36.sql) this is ALSO the CHECK on the
# persisted task_gates.status column, backed by the classic table-rebuild
# migration (SQLite cannot ALTER TABLE to add a CHECK to an existing column;
# create-copy-drop-rename, mirroring v34_to_v35.sql). This closes what was
# until then a deliberate, documented asymmetry: every other
# STATE_MACHINE_REGISTRY entry paired a Python tuple with a real SQL CHECK,
# while this one was enforced code-level only (gaia.store.writer
# add_gate_to_task / set_gate_status guards). Both enforcement layers remain
# in place -- the writers' guard is not redundant with the DB CHECK, it gives
# a clean ValueError instead of a raw sqlite3.IntegrityError at the call site.
#
# Consequence for tools/state/diff_source_of_truth.py: with the CHECK now
# present, this pair converges with every other registry entry -- the diff
# tool reports no divergence for ("task_gates", "status") once a DB is at
# v36 or above.
# ---------------------------------------------------------------------------
VALID_GATE_STATUSES: tuple[str, ...] = (
    "pending",
    "pass",
    "fail",
)


# ---------------------------------------------------------------------------
# Convenience: a registry mapping (table, column) -> tuple, used by the
# migration script and the diff tool so neither has to hard-code names.
# VALID_VERIFICATION_TYPES is registered here as of v34: it now backs a real
# persisted CHECK on task_gates.verification_type (harness R1-A), so the diff
# tool holds the SQL CHECK and the Python tuple identical. (It remains ALSO an
# envelope-field enum for the contract validator -- the two uses share one
# SSOT tuple.)
#
# ("task_gates", "status") is backed by a real DB CHECK as of v36 (see the
# VALID_GATE_STATUSES docstring above) -- every entry in this registry now
# pairs a Python tuple with a matching SQL CHECK.
# ---------------------------------------------------------------------------
STATE_MACHINE_REGISTRY: dict[tuple[str, str], tuple[str, ...]] = {
    ("episodes", "plan_status"): VALID_PLAN_STATUSES,
    ("briefs", "status"): VALID_BRIEF_STATUSES,
    ("plans", "status"): VALID_PLAN_LIFECYCLE_STATUSES,
    ("tasks", "status"): VALID_TASK_STATUSES,
    ("acceptance_criteria", "status"): VALID_AC_STATUSES,
    ("milestones", "status"): VALID_MILESTONE_STATUSES,
    ("task_gates", "verification_type"): VALID_VERIFICATION_TYPES,
    ("task_gates", "status"): VALID_GATE_STATUSES,
}


__all__ = [
    "VALID_PLAN_STATUSES",
    "VALID_BRIEF_STATUSES",
    "VALID_PLAN_LIFECYCLE_STATUSES",
    "VALID_TASK_STATUSES",
    "VALID_AC_STATUSES",
    "VALID_MILESTONE_STATUSES",
    "VALID_VERIFICATION_TYPES",
    "VALID_GATE_STATUSES",
    "STATE_MACHINE_REGISTRY",
]
