"""
gaia.state -- Single source of truth for the status vocabularies of the Gaia substrate.

Each vocabulary is a tuple, so the SQL CHECK clauses generated from it keep a
deterministic order. ``STATE_MACHINE_REGISTRY`` maps the ``(table, column)``
pairs whose CHECK is generated from a tuple, and ``tools/state/diff_source_of_truth.py``
compares each tuple with the CHECK the database carries.

Adding a value: append it to the tuple, run the diff tool, then ship a schema
migration that re-applies the CHECK clause. Removing or renaming a value needs
a data backfill and its own migration.
"""

from __future__ import annotations

# Contract workflow -- episodes.plan_status. hooks/modules/agents/response_contract
# builds its VALID_PLAN_STATUSES set from this tuple.
VALID_PLAN_STATUSES: tuple[str, ...] = (
    "IN_PROGRESS",
    "APPROVAL_REQUEST",
    "COMPLETE",
    "BLOCKED",
    "NEEDS_INPUT",
    "NEEDS_VERIFICATION",
)

# TERMINAL_PLAN_STATUSES and CLOSED_TURN_PLAN_STATUSES cut VALID_PLAN_STATUSES
# for two different questions, and TERMINAL is a strict subset of CLOSED:
#
#   TERMINAL -- may a later, truer verdict replace this row's verdict? Only
#       COMPLETE answers no.
#   CLOSED   -- did the agent whose turn this is declare it over? Every declared
#       close answers yes.
#
# Merging them breaks both directions: `gaia contract reconcile --harness-id`
# stops finding the row it repairs when the verdict guard is the turn guard
# (tests/cli/test_contract_reconcile.py::test_reconcile_addresses_by_harness_id_too),
# and an agent's next assignment merges its evidence into a record it already
# closed when the turn guard is the verdict guard.

# The write-once boundary of a verdict. A COMPLETE row must not regress to
# another value (a retried finalize, a racing SubagentStop backstop, a resumed
# draft's stale envelope), while every other value stays convergeable so a
# born DISPATCHED row, a reaped or salvaged row and an idempotent re-finalize
# can still land. Not the "is the turn over" test; that is CLOSED_TURN_PLAN_STATUSES.
# Consumed by gaia.store.writer.finalize_agent_contract_handoff and
# gaia.store.writer.stamp_harness_agent_id.
TERMINAL_PLAN_STATUSES: tuple[str, ...] = (
    "COMPLETE",
)

# The boundary of a turn: an agent that declared any close and writes again
# starts its next contract. IN_PROGRESS is excluded because `gaia contract
# finalize` refuses it (bin/cli/contract.py::cmd_finalize), so a row carrying it
# was cut and reaped, not closed. DISPATCHED is a row state, absent from
# VALID_PLAN_STATUSES. Consumed by gaia.store.writer.open_contract_continuation,
# gaia.store.writer.mirror_partial_contract_handoff and gaia.contract.drafts.
CLOSED_TURN_PLAN_STATUSES: tuple[str, ...] = tuple(
    status for status in VALID_PLAN_STATUSES if status != "IN_PROGRESS"
)

# agent_contract_handoffs.cut_reason: how a row was closed by something other
# than the agent's own `gaia contract finalize`. A row is stamped NEVER_FINALIZED
# at birth and only finalize_agent_contract_handoff called without a cut_reason
# clears it, so a harness cut that fires no SubagentStop stays marked. The other
# values record which lane closed the row:
#   REAPED              -- a closure converged a born row the agent never finalized.
#   BACKSTOP_CAPTURE    -- the SubagentStop backstop wrote the row because none existed.
#   SALVAGED_TRUNCATION -- a max_tokens truncation was rescued from the on-disk draft.
# The column carries no CHECK: a migrated database's ALTER TABLE ADD COLUMN could
# not carry one, and the two install shapes would disagree. This tuple is the
# enforced vocabulary.
CUT_REASON_NEVER_FINALIZED: str = "never_finalized"
CUT_REASON_REAPED: str = "reaped"
CUT_REASON_BACKSTOP_CAPTURE: str = "backstop_capture"
CUT_REASON_SALVAGED_TRUNCATION: str = "salvaged_truncation"

CUT_REASONS: tuple[str, ...] = (
    CUT_REASON_NEVER_FINALIZED,
    CUT_REASON_REAPED,
    CUT_REASON_BACKSTOP_CAPTURE,
    CUT_REASON_SALVAGED_TRUNCATION,
)

# Brief lifecycle -- briefs.status. gaia.briefs.store imports it as VALID_STATUSES.
VALID_BRIEF_STATUSES: tuple[str, ...] = (
    "draft",
    "open",
    "in-progress",
    "closed",
    "archived",
)

# Plan lifecycle -- plans.status.
VALID_PLAN_LIFECYCLE_STATUSES: tuple[str, ...] = (
    "draft",
    "active",
    "closed",
)

# Task lifecycle -- tasks.status.
VALID_TASK_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "skipped",
)

# Acceptance criteria lifecycle -- acceptance_criteria.status. 'descoped' is
# hard-terminal: no transition leaves it (transitions.AC_LIFECYCLE_TRANSITIONS).
# Together with 'done' it forms the terminal set verify_brief uses.
VALID_AC_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "blocked",
    "descoped",
)

# Milestone lifecycle -- milestones.status.
VALID_MILESTONE_STATUSES: tuple[str, ...] = (
    "pending",
    "done",
    "blocked",
)

# Verification type -- the type of a contract envelope's evidence_report.verification
# and the task_gates.verification_type column. Two consumers must stay identical:
# gaia.contract.validator.validate_form (an imported tuple with a stdlib
# fallback) and the persisted CHECK, which the registry below holds equal.
# "command" and "code" are deterministic oracle types; "semantic" needs a rubric
# judgement; "self_review" is the agent stating what it checked.
VALID_VERIFICATION_TYPES: tuple[str, ...] = (
    "command",
    "code",
    "semantic",
    "self_review",
)

# Gate result -- task_gates.status: 'pending' until a verifier runs it, then
# 'pass' or 'fail'. gaia.store.writer.set_gate_status validates it ahead of the
# column's CHECK so the caller gets a ValueError, not a sqlite3.IntegrityError.
VALID_GATE_STATUSES: tuple[str, ...] = (
    "pending",
    "pass",
    "fail",
)


# Why a gate failed (task_gates.fail_cause): required with 'fail', absent
# otherwise, so a failure says whether the product, the environment, the check
# itself, or the requirement is what has to move.
VALID_GATE_FAIL_CAUSES: tuple[str, ...] = (
    "product",
    "environment",
    "broken_test",
    "requirement_changed",
)

# Direction of an evidence row (evidence.polarity). Only 'positive'
# evidence can accept an AC; 'negative' is recorded so a refutation is kept.
VALID_EVIDENCE_POLARITIES: tuple[str, ...] = (
    "positive",
    "negative",
)


# (table, column) -> tuple, read by the migration script and the diff tool.
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
    "TERMINAL_PLAN_STATUSES",
    "CLOSED_TURN_PLAN_STATUSES",
    "CUT_REASON_NEVER_FINALIZED",
    "CUT_REASON_REAPED",
    "CUT_REASON_BACKSTOP_CAPTURE",
    "CUT_REASON_SALVAGED_TRUNCATION",
    "CUT_REASONS",
    "VALID_BRIEF_STATUSES",
    "VALID_PLAN_LIFECYCLE_STATUSES",
    "VALID_TASK_STATUSES",
    "VALID_AC_STATUSES",
    "VALID_MILESTONE_STATUSES",
    "VALID_VERIFICATION_TYPES",
    "VALID_GATE_STATUSES",
    "VALID_GATE_FAIL_CAUSES",
    "VALID_EVIDENCE_POLARITIES",
    "STATE_MACHINE_REGISTRY",
]
