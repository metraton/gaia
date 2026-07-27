"""
gaia.state.task_closure -- Pure derivation of whether a task's persisted gates
constitute an approving verdict.

A task carries zero or more gates (``task_gates`` rows), each holding a
``status`` drawn from ``gaia.state.VALID_GATE_STATUSES`` ('pending' -> 'pass' |
'fail'). This module answers ONE question about a collection of those rows:
does their state amount to an approving verdict for the task they belong to?

It is a READ ONLY. It derives; it never decides what to do with the answer, and
it never writes: no task status, no gate status, no event. ``tasks.status`` has
exactly one writer (``gaia.store.writer.set_task_status``) and this module is
not a second one -- it holds no DB connection at all. That is visible in the
body: the only names it touches are the mappings handed to it.

Purity, in the same sense as ``gaia.state.gate_validation``: no DB access, no
subprocess, no filesystem, no environment read, no LLM. In particular it reads
NO dispatch coordinate -- not ``GAIA_DISPATCH_AGENT``, not an agent name, not a
session or task id -- so the verdict depends only on the gate rows passed in.
Two consequences that are the point of building it this way: the derivation is
idempotent (same rows in, equal verdict out, however many times it is called,
and the input is never mutated), and whichever seam ends up invoking it is
interchangeable, because the primitive cannot tell which one did.

FAIL CLOSED is the governing rule; every branch below resolves toward
"not approving" unless the evidence is positive and complete:

  * Every gate exactly 'pass' (and at least one gate) -> approving.
  * Any gate 'pending' or 'fail' -> not approving.
  * ZERO gates -> NOT approving. This is decided explicitly, ahead of the
    aggregate, and the explicitness is load-bearing: ``all()`` over an empty
    collection is True in Python, so a naive aggregate would report a task with
    no gates at all as approved -- approval derived from the absence of
    evidence. An empty gate set carries no verdict, and no verdict is not an
    approving one.
  * A status outside the vocabulary (None, '', 'PASS', 'skipped', a non-string)
    -> not approving, reported as its own reason. Such a value cannot reach the
    column through the writers (the DB CHECK and
    ``gaia.store.writer._assert_valid_gate_status`` both reject it), so seeing
    one means the rows did not come from that path; it is never coerced toward
    'pass'.
  * A malformed element, or a collection that is not a sequence of mappings ->
    not approving, reported as its own reason rather than raised, mirroring how
    ``gate_validation.validate_gate`` handles a non-mapping gate.

``GateVerdict.reasons`` exists because a caller that refuses to close a task
has to be able to say WHY in terms the operator can act on -- which gates are
outstanding, or that there are none to begin with. Those two situations demand
different corrections, so they are never collapsed into one bare False.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

# SSOT for the gate-status vocabulary -- imported with a byte-identical stdlib
# fallback so this pure module never hard-fails on an import edge (mirrors the
# pattern in gaia.state.gate_validation and gaia.contract.validator).
try:
    from gaia.state import VALID_GATE_STATUSES as _CANONICAL_GATE_STATUSES
    VALID_GATE_STATUSES: tuple[str, ...] = tuple(_CANONICAL_GATE_STATUSES)
except Exception:  # pragma: no cover - defensive fallback only
    VALID_GATE_STATUSES = ("pending", "pass", "fail")

# The ONE status that counts as evidence in favour of closing. Named rather
# than inlined so the fail-closed asymmetry is explicit: everything else,
# in-vocabulary or not, counts against.
APPROVING_GATE_STATUS = "pass"

# Bucket keys used in GateVerdict.status_counts for rows whose status is not a
# vocabulary member. Angle-bracketed so they can never collide with a real
# status value.
MALFORMED_GATE_KEY = "<malformed>"
OFF_VOCABULARY_GATE_KEY = "<off-vocabulary>"

# The empty-set reason is a module constant because a caller rendering an
# operator-facing message needs to distinguish "gates outstanding" from "no
# gates at all" without string-matching a sentence written inline.
EMPTY_GATE_SET_REASON = (
    "task has zero gates: an empty gate set is not an approving verdict "
    "(no gate has been declared, so nothing has been verified)"
)


@dataclass(frozen=True)
class GateVerdict:
    """Derived verdict over one task's gate rows.

    ``approving`` is True only when there is at least one gate AND every gate's
    status is exactly ``APPROVING_GATE_STATUS``. ``gate_count`` is how many
    rows were considered. ``status_counts`` buckets those rows by observed
    status, with ``MALFORMED_GATE_KEY`` / ``OFF_VOCABULARY_GATE_KEY`` for rows
    that carry no usable status. ``reasons`` carries every reason
    ``approving`` is False, and is empty when it is True.
    """

    approving: bool
    gate_count: int
    status_counts: dict[str, int] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _as_gate_sequence(gates: object) -> list[object] | None:
    """Return ``gates`` as a list of elements, or None when it is not a
    sequence of gate rows.

    A str/bytes and a Mapping are rejected rather than iterated: both are
    technically iterable but iterating them yields characters or keys, which
    would silently produce a bogus per-gate analysis instead of an honest
    rejection.
    """
    if gates is None or isinstance(gates, (str, bytes, Mapping)):
        return None
    if isinstance(gates, Sequence):
        return list(gates)
    return None


def derive_gate_verdict(gates: object) -> GateVerdict:
    """Derive whether ``gates`` constitutes an approving verdict.

    ``gates`` is a sequence of gate mappings in the ``task_gates`` shape -- the
    exact shape ``gaia.store.writer.list_task_gates`` returns, so no
    translation is needed between the read path and this derivation. Only
    ``status`` is consulted; ``verification_type``, ``evidence_shape`` and the
    rest are irrelevant to the question of whether a verdict was reached (WHAT
    the check was is ``gate_validation``'s and ``gate_oracle``'s business, not
    this module's).

    Returns a :class:`GateVerdict`. Never raises for a malformed input, never
    writes, never reads anything outside the argument.
    """
    sequence = _as_gate_sequence(gates)
    if sequence is None:
        return GateVerdict(
            approving=False,
            gate_count=0,
            status_counts={},
            reasons=[
                "gates must be a sequence of gate mappings, got "
                f"{type(gates).__name__}"
            ],
        )

    if not sequence:
        return GateVerdict(
            approving=False,
            gate_count=0,
            status_counts={},
            reasons=[EMPTY_GATE_SET_REASON],
        )

    status_counts: dict[str, int] = {}
    reasons: list[str] = []

    for index, gate in enumerate(sequence):
        if not isinstance(gate, Mapping):
            status_counts[MALFORMED_GATE_KEY] = (
                status_counts.get(MALFORMED_GATE_KEY, 0) + 1
            )
            reasons.append(
                f"gate at index {index} is not a mapping "
                f"(got {type(gate).__name__})"
            )
            continue

        status = gate.get("status")
        if isinstance(status, str) and status in VALID_GATE_STATUSES:
            status_counts[status] = status_counts.get(status, 0) + 1
            continue

        status_counts[OFF_VOCABULARY_GATE_KEY] = (
            status_counts.get(OFF_VOCABULARY_GATE_KEY, 0) + 1
        )
        reasons.append(
            f"gate {_gate_label(gate, index)} carries status {status!r}, "
            f"which is outside {list(VALID_GATE_STATUSES)}"
        )

    outstanding = _outstanding_summary(status_counts)
    if outstanding:
        reasons.append(outstanding)

    # Both conjuncts on purpose, and neither is redundant under the fail-closed
    # rule: the count is the positive evidence (every single row passed), while
    # "no reasons" keeps any future reason type -- one that does not move a
    # count -- from being silently outvoted by a passing tally.
    approving = (
        not reasons
        and status_counts.get(APPROVING_GATE_STATUS, 0) == len(sequence)
    )

    return GateVerdict(
        approving=approving,
        gate_count=len(sequence),
        status_counts=status_counts,
        reasons=reasons,
    )


def _gate_label(gate: Mapping, index: int) -> str:
    """Name a gate by its persisted id when it has one, else by position."""
    gate_id = gate.get("id")
    if isinstance(gate_id, int):
        return f"id={gate_id}"
    return f"at index {index}"


def _outstanding_summary(status_counts: dict[str, int]) -> str | None:
    """Summarize the in-vocabulary, not-yet-passing gates as one reason.

    One aggregate line rather than one reason per gate: an operator deciding
    what to do next needs the count of what is outstanding and in which state,
    not a row-by-row transcript. Returns None when nothing in-vocabulary is
    outstanding.
    """
    outstanding = {
        status: count
        for status, count in status_counts.items()
        if status in VALID_GATE_STATUSES and status != APPROVING_GATE_STATUS
    }
    if not outstanding:
        return None
    breakdown = ", ".join(
        f"{status}={outstanding[status]}" for status in sorted(outstanding)
    )
    total = sum(outstanding.values())
    return (
        f"{total} gate(s) have not passed ({breakdown}); an approving verdict "
        f"requires every gate at {APPROVING_GATE_STATUS!r}"
    )


__all__ = [
    "APPROVING_GATE_STATUS",
    "EMPTY_GATE_SET_REASON",
    "GateVerdict",
    "MALFORMED_GATE_KEY",
    "OFF_VOCABULARY_GATE_KEY",
    "VALID_GATE_STATUSES",
    "derive_gate_verdict",
]
