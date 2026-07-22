"""
gaia.state.gate_validation -- Pure structural validation of a task gate.

A task gate (task_gates row / planner-authored typed gate, harness R1-A) is
structurally valid when:

  1. Its ``verification_type`` is present AND is one of
     ``gaia.state.VALID_VERIFICATION_TYPES`` (the same SSOT tuple the DB CHECK
     on ``task_gates.verification_type`` is registered against).
  2. The evidence field(s) that its ``verification_type`` requires are present
     and non-empty.

This function is PURE and DETERMINISTIC in the R3 style: no DB access, no LLM,
no I/O. It validates a gate mapping already in memory and returns an
accept/reject verdict with reasons. It does NOT persist, and the CLI writer
(``gaia.store.writer.add_gate_to_task``) does NOT call it -- the gate is
persisted as given (advisory-vs-blocking is a later, out-of-scope decision).

The per-type required-evidence matrix mirrors, structurally, the R3
contract-envelope verification shape (``gaia.contract.validator``): each
verification type declares what a verifier needs to run the check. On the
contract envelope those live in distinct named fields (``command`` for the
deterministic types, ``requires_human`` for semantic, ``reviewed`` for
self_review); on the persisted gate they map onto the single ``evidence_shape``
column, which carries the specification of the check (the runnable
command/oracle, the rubric, or the review statement). The matrix is data-driven
(``REQUIRED_EVIDENCE_FIELDS_BY_TYPE``) so it is the extension point when the
per-type shape is refined.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# SSOT for the type enum -- imported with a byte-identical stdlib fallback so
# this pure module never hard-fails on an import edge (mirrors the pattern in
# gaia.contract.validator).
try:
    from gaia.state import VALID_VERIFICATION_TYPES as _CANONICAL_VERIFICATION_TYPES
    VALID_VERIFICATION_TYPES: tuple[str, ...] = tuple(_CANONICAL_VERIFICATION_TYPES)
except Exception:  # pragma: no cover - defensive fallback only
    VALID_VERIFICATION_TYPES = ("command", "code", "semantic", "self_review")


# Per-type required evidence fields on the GATE (task_gates columns). Each
# listed field must be present and non-empty for a gate of that type to be
# structurally valid. Data-driven: refine per-type requirements here.
REQUIRED_EVIDENCE_FIELDS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "command": ("evidence_shape",),
    "code": ("evidence_shape",),
    "semantic": ("evidence_shape",),
    "self_review": ("evidence_shape",),
}


@dataclass(frozen=True)
class GateValidationResult:
    """Verdict of :func:`validate_gate`.

    ``ok`` is True only when there are zero errors. ``errors`` is a list of
    human-readable reasons (empty when ``ok``).
    """

    ok: bool
    errors: list[str] = field(default_factory=list)


def _is_nonempty(value: object) -> bool:
    """True when ``value`` is a non-empty, non-whitespace-only string.

    A non-string truthy value (e.g. a dict shape) also counts as present.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def validate_gate(gate: object) -> GateValidationResult:
    """Validate the structure of a single gate mapping.

    Accepts a mapping (dict) with the task_gates field names
    (``verification_type``, ``evidence_type``, ``evidence_shape``,
    ``artifact_path``, ``status``). Returns a :class:`GateValidationResult`.

    Rejects when:
      * ``gate`` is not a mapping;
      * ``verification_type`` is absent/empty;
      * ``verification_type`` is not in VALID_VERIFICATION_TYPES;
      * a required evidence field for the type is absent/empty.
    """
    errors: list[str] = []

    if not isinstance(gate, dict):
        return GateValidationResult(
            ok=False,
            errors=[f"gate must be a mapping, got {type(gate).__name__}"],
        )

    vtype = gate.get("verification_type")

    if not _is_nonempty(vtype):
        errors.append("verification_type is required and must be non-empty")
        return GateValidationResult(ok=False, errors=errors)

    if vtype not in VALID_VERIFICATION_TYPES:
        errors.append(
            f"verification_type {vtype!r} is not one of "
            f"{list(VALID_VERIFICATION_TYPES)}"
        )
        # Cannot check per-type evidence for an unknown type.
        return GateValidationResult(ok=False, errors=errors)

    for required_field in REQUIRED_EVIDENCE_FIELDS_BY_TYPE.get(vtype, ()):
        if not _is_nonempty(gate.get(required_field)):
            errors.append(
                f"verification_type {vtype!r} requires a non-empty "
                f"{required_field!r}"
            )

    return GateValidationResult(ok=not errors, errors=errors)
