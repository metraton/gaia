#!/usr/bin/env python3
"""A memory turn (kind=memory, no plan_task_id) self-completes; verification.type
'none' is a first-class ENVELOPE type without contaminating the task_gates SSOT
(plan 34 task 7).

Brief: contrato-binding-y-verificacion-por-task-id (plan_id=34, task order_num=7).

Covered verbatim:

1. UNBOUND MEMORY SELF-COMPLETES: a turn whose kind is 'memory' and whose
   binding carries NO plan_task_id reaches COMPLETE with no verifier in the
   loop -- the finalize gate keys on plan_task_id, not on kind or role.
2. verification.type 'none' ACCEPTED IN THE ENVELOPE: an envelope declaring
   verification.type == 'none' validates cleanly (it demands no extra field);
   'none' names "no external oracle was required", the honest verification of
   an unbound turn.
3. task_gates CHECK INTACT: the shared SSOT VALID_VERIFICATION_TYPES -- the
   tuple backing the persisted CHECK on task_gates.verification_type -- stays
   EXACTLY command / code / semantic / self_review. 'none' lives ONLY in the
   envelope enum (ENVELOPE_VERIFICATION_TYPES), never in the task_gates SSOT.
"""

from __future__ import annotations

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
PKG_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(HOOKS_DIR), str(PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import evaluate_contract_gate  # noqa: E402
from gaia.contract.validator import (  # noqa: E402
    ENVELOPE_VERIFICATION_TYPES,
    VALID_VERIFICATION_TYPES,
    FormErrorCode,
    validate_form,
)
from gaia.state import VALID_VERIFICATION_TYPES as STATE_VERIFICATION_TYPES  # noqa: E402

_EVIDENCE_KEYS = (
    "patterns_checked",
    "files_checked",
    "commands_run",
    "key_outputs",
    "verbatim_outputs",
    "cross_layer_impacts",
    "open_gaps",
)


def _evidence():
    return {k: [] for k in _EVIDENCE_KEYS}


def _memory_complete_envelope(agent_id: str = "a1b2c3", vtype: str = "none"):
    """A COMPLETE envelope for a memory turn: verification.type 'none' (no
    external oracle) plus the result=='pass' that any COMPLETE requires."""
    return {
        "agent_status": {
            "agent_state": "COMPLETE",
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            **_evidence(),
            "verification": {
                "method": "self-attested",
                "type": vtype,
                "result": "pass",
                "details": "memory write recorded; no plan-task oracle applies",
            },
        },
        "consolidation_report": None,
        "approval_request": None,
    }


# ---------------------------------------------------------------------------
# 1. An unbound memory turn reaches COMPLETE without a verifier
# ---------------------------------------------------------------------------

class TestMemoryTurnSelfCompletes:
    def test_unbound_memory_complete_allowed_full_verdict(self):
        gate = evaluate_contract_gate(
            _memory_complete_envelope(), agent_type="gaia-operator",
            plan_task_id=None, ramp_enabled=True,
        )
        assert gate.rejected is False, gate.rejection_reason
        assert gate.anomalies == ()

    def test_unbound_memory_complete_allowed_three_case(self):
        gate = evaluate_contract_gate(
            _memory_complete_envelope(), agent_type="gaia-operator",
            plan_task_id=None, ramp_enabled=False,
        )
        assert gate.rejected is False

    def test_memory_complete_allowed_even_for_non_verifier(self):
        """The emitting agent is a plain producer, not a seeded verifier -- and
        the memory turn still self-completes, because the decision is keyed on
        the (absent) plan_task_id, not on whether the agent is a verifier."""
        gate = evaluate_contract_gate(
            _memory_complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=True,
        )
        assert gate.rejected is False


# ---------------------------------------------------------------------------
# 2. verification.type 'none' is accepted in the envelope
# ---------------------------------------------------------------------------

class TestVerificationTypeNoneAcceptedInEnvelope:
    def test_none_type_validates_clean(self):
        result = validate_form(_memory_complete_envelope())
        assert result.ok is True, result.error_summary()
        assert result.errors == ()

    def test_none_type_fires_no_verification_shape_error(self):
        """'none' demands no extra field, so it never trips VERIFICATION_SHAPE
        (unlike command/semantic/self_review, which each require their field)."""
        result = validate_form(_memory_complete_envelope())
        assert FormErrorCode.VERIFICATION_SHAPE not in result.codes

    def test_none_is_in_the_envelope_enum(self):
        assert "none" in ENVELOPE_VERIFICATION_TYPES


# ---------------------------------------------------------------------------
# 3. The task_gates SSOT CHECK stays intact at its four types
# ---------------------------------------------------------------------------

class TestTaskGatesCheckIntact:
    def test_ssot_still_exactly_four_types(self):
        assert set(VALID_VERIFICATION_TYPES) == {
            "command", "code", "semantic", "self_review",
        }
        assert len(VALID_VERIFICATION_TYPES) == 4

    def test_none_not_in_task_gates_ssot(self):
        """'none' is envelope-only -- it must NOT leak into the SSOT that backs
        the persisted CHECK on task_gates.verification_type."""
        assert "none" not in VALID_VERIFICATION_TYPES
        assert "none" not in STATE_VERIFICATION_TYPES

    def test_validator_mirror_still_matches_state_ssot(self):
        """The validator's VALID_VERIFICATION_TYPES stays byte-identical to the
        gaia.state SSOT -- the envelope extension did not perturb the mirror."""
        assert VALID_VERIFICATION_TYPES == STATE_VERIFICATION_TYPES

    def test_envelope_enum_is_ssot_plus_none_only(self):
        """The envelope enum is exactly the SSOT four PLUS 'none' -- nothing
        more, nothing less."""
        assert set(ENVELOPE_VERIFICATION_TYPES) == set(VALID_VERIFICATION_TYPES) | {"none"}
