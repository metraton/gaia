"""AC-3 (harness R1-A): the pure structural gate-validation function.

Matchable by ``pytest tests/ -k task_gates_validation -q``.

gaia.state.gate_validation.validate_gate is DB-free and LLM-free. It REJECTS a
gate whose verification_type is absent or outside VALID_VERIFICATION_TYPES, and
a well-typed gate missing the evidence field(s) its type requires; it ACCEPTS a
well-formed gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state import VALID_VERIFICATION_TYPES
from gaia.state.gate_validation import (
    REQUIRED_EVIDENCE_FIELDS_BY_TYPE,
    validate_gate,
)


def _well_formed(vtype: str) -> dict:
    """A structurally complete gate for ``vtype``."""
    gate = {"verification_type": vtype, "evidence_type": "descriptor",
            "artifact_path": "evidence/x.txt", "status": "pending"}
    for req in REQUIRED_EVIDENCE_FIELDS_BY_TYPE.get(vtype, ()):
        gate[req] = "the check spec"
    return gate


# --- ACCEPT ----------------------------------------------------------------

def test_task_gates_validation_accepts_well_formed_each_type():
    for vtype in VALID_VERIFICATION_TYPES:
        res = validate_gate(_well_formed(vtype))
        assert res.ok, f"{vtype}: expected accept, got errors {res.errors}"
        assert res.errors == []


# --- REJECT: type problems -------------------------------------------------

def test_task_gates_validation_rejects_absent_type():
    res = validate_gate({"evidence_shape": "x"})
    assert not res.ok
    assert any("verification_type" in e for e in res.errors)


def test_task_gates_validation_rejects_empty_type():
    res = validate_gate({"verification_type": "   ", "evidence_shape": "x"})
    assert not res.ok
    assert any("verification_type" in e for e in res.errors)


def test_task_gates_validation_rejects_invalid_type():
    res = validate_gate({"verification_type": "not_a_type", "evidence_shape": "x"})
    assert not res.ok
    assert any("not one of" in e for e in res.errors)


def test_task_gates_validation_rejects_non_mapping():
    res = validate_gate("not a dict")
    assert not res.ok
    assert any("mapping" in e for e in res.errors)


# --- REJECT: missing required evidence for the type ------------------------

def test_task_gates_validation_rejects_missing_required_evidence():
    for vtype in VALID_VERIFICATION_TYPES:
        required = REQUIRED_EVIDENCE_FIELDS_BY_TYPE.get(vtype, ())
        if not required:
            continue
        # Omit every required evidence field for this type.
        gate = {"verification_type": vtype, "evidence_type": "descriptor",
                "status": "pending"}
        res = validate_gate(gate)
        assert not res.ok, f"{vtype}: expected reject on missing evidence"
        for req in required:
            assert any(req in e for e in res.errors), (
                f"{vtype}: no error mentions missing {req!r}; got {res.errors}"
            )


def test_task_gates_validation_rejects_empty_required_evidence():
    # A present-but-empty required field is still a rejection.
    vtype = "command"
    gate = {"verification_type": vtype, "evidence_shape": "   "}
    res = validate_gate(gate)
    assert not res.ok
    assert any("evidence_shape" in e for e in res.errors)


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
