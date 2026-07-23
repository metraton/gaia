#!/usr/bin/env python3
"""Finalize gate keyed on plan_task_id, NOT on role or kind (plan 34 task 7).

Brief: contrato-binding-y-verificacion-por-task-id (plan_id=34, task order_num=7).

This is the core of the redesign: the SubagentStop finalize gate that decides
whether a turn may self-COMPLETE is a pure function of the turn's DISPATCH
BINDING -- specifically, whether it carries a ``plan_task_id`` -- and NOT of the
emitting agent's role or the turn's ``kind`` label.

Locked decisions covered verbatim:

1. BOUND -> BLIND VERIFICATION: a turn WHOSE BINDING CARRIES a plan_task_id
   (a plan-task-bound producer turn) may NOT self-COMPLETE. A COMPLETE is
   rejected -- the producer must report NEEDS_VERIFICATION so an independent
   verifier confirms the increment.
2. UNBOUND -> SELF-COMPLETE: a turn with NO plan_task_id (investigation /
   memory / a free-standing verifier turn) reaches COMPLETE with no verifier
   in the loop.
3. DECISION BY plan_task_id, NOT ROLE, NOT KIND: holding the emitting agent
   and the turn's kind constant, the outcome flips solely on the presence of
   plan_task_id. Role and kind never enter the decision.
4. BOTH RAMP PATHS: the gate enforces identically in ramp-ON (full-verdict)
   and ramp-OFF (three-case) -- ramp-OFF is not a bypass.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
PKG_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(HOOKS_DIR), str(PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    GATE_MODE_FULL_VERDICT,
    GATE_MODE_THREE_CASE,
    _blind_verification_required,
    _three_case_verdict,
    evaluate_contract_gate,
)

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


def _envelope(agent_state: str, agent_id: str = "a1b2c3"):
    return {
        "agent_status": {
            "agent_state": agent_state,
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "done" if agent_state == "COMPLETE" else "continue",
        },
        "evidence_report": _evidence(),
        "consolidation_report": None,
        "approval_request": None,
    }


def _complete_envelope(agent_id: str = "a1b2c3"):
    env = _envelope("COMPLETE", agent_id)
    env["evidence_report"]["verification"] = {
        "method": "test", "result": "pass", "details": "suite green",
    }
    return env


# ---------------------------------------------------------------------------
# 1. BOUND turn (plan_task_id present) may NOT self-COMPLETE
# ---------------------------------------------------------------------------

class TestBoundTurnCannotSelfComplete:
    def test_bound_complete_rejected_full_verdict(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=44, ramp_enabled=True,
        )
        assert gate.rejected is True
        assert gate.mode == GATE_MODE_FULL_VERDICT
        assert "plan_task_id=44" in gate.rejection_reason
        assert any(a["code"] == "BLIND_VERIFICATION_REQUIRED" for a in gate.anomalies)

    def test_bound_complete_rejected_three_case(self):
        """Ramp-OFF is NOT a bypass -- the bound-COMPLETE rejection fires in the
        three-case path too."""
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=44, ramp_enabled=False,
        )
        assert gate.rejected is True
        assert gate.mode == GATE_MODE_THREE_CASE
        assert "plan_task_id=44" in gate.rejection_reason

    def test_bound_complete_rejected_three_case_direct(self):
        gate = _three_case_verdict(_complete_envelope(), "developer", 44)
        assert gate.rejected is True
        assert gate.mode == GATE_MODE_THREE_CASE

    def test_blind_verification_required_returns_reason_when_bound(self):
        reason = _blind_verification_required("COMPLETE", 44)
        assert reason is not None
        assert "NEEDS_VERIFICATION" in reason


# ---------------------------------------------------------------------------
# 2. UNBOUND turn (no plan_task_id) reaches COMPLETE without a verifier
# ---------------------------------------------------------------------------

class TestUnboundTurnSelfCompletes:
    def test_unbound_complete_allowed_full_verdict(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=True,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_FULL_VERDICT
        assert gate.anomalies == ()

    def test_unbound_complete_allowed_three_case(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=False,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_THREE_CASE

    def test_plan_task_id_defaults_to_unbound(self):
        """Omitting plan_task_id entirely is treated as unbound (self-COMPLETE
        allowed) -- the backward-compatible default for callers that pass no
        binding."""
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer", ramp_enabled=True,
        )
        assert gate.rejected is False

    def test_blind_verification_required_returns_none_when_unbound(self):
        assert _blind_verification_required("COMPLETE", None) is None


# ---------------------------------------------------------------------------
# 3. The decision is by plan_task_id, NOT role, NOT kind
# ---------------------------------------------------------------------------

class TestDecisionKeyedOnTaskIdNotRoleNotKind:
    @pytest.mark.parametrize(
        "agent_type",
        ["developer", "gaia-verifier", "gaia-system", "some-unseeded-agent"],
    )
    def test_outcome_independent_of_agent_role(self, agent_type):
        """Holding the binding constant, the outcome is identical across every
        agent identity -- including the seeded verifier. A BOUND COMPLETE is
        rejected for ALL of them; an UNBOUND COMPLETE is accepted for ALL of
        them. Role never enters the decision."""
        bound = evaluate_contract_gate(
            _complete_envelope(), agent_type=agent_type,
            plan_task_id=44, ramp_enabled=True,
        )
        unbound = evaluate_contract_gate(
            _complete_envelope(), agent_type=agent_type,
            plan_task_id=None, ramp_enabled=True,
        )
        assert bound.rejected is True, f"{agent_type}: bound turn must not self-COMPLETE"
        assert unbound.rejected is False, f"{agent_type}: unbound turn must self-COMPLETE"

    def test_outcome_flips_solely_on_binding_same_agent(self):
        """Same agent, same envelope: the ONLY variable is plan_task_id, and it
        is what flips the verdict -- the definition of 'keyed on plan_task_id'."""
        rejected = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=7, ramp_enabled=True,
        )
        accepted = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=True,
        )
        assert rejected.rejected is True
        assert accepted.rejected is False

    def test_blind_check_ignores_agent_type_entirely(self):
        """_blind_verification_required has no agent_type / kind parameter at
        all -- its signature is (agent_state, plan_task_id). The decision cannot
        depend on role or kind because neither is an input."""
        import inspect
        params = list(inspect.signature(_blind_verification_required).parameters)
        assert params == ["agent_state", "plan_task_id"]


# ---------------------------------------------------------------------------
# 4. Only COMPLETE is gated; a bound producer proposing NEEDS_VERIFICATION is
#    never a violation (propose, not complete).
# ---------------------------------------------------------------------------

class TestOnlyCompleteIsGated:
    def test_bound_needs_verification_is_not_a_violation(self):
        env = _envelope("NEEDS_VERIFICATION")
        env["evidence_report"]["verification"] = {
            "method": "test", "result": "pass", "details": "proposed by producer",
        }
        gate = evaluate_contract_gate(
            env, agent_type="developer", plan_task_id=44, ramp_enabled=True,
        )
        assert gate.rejected is False

    def test_bound_in_progress_is_not_a_violation(self):
        gate = evaluate_contract_gate(
            _envelope("IN_PROGRESS"), agent_type="developer",
            plan_task_id=44, ramp_enabled=True,
        )
        assert gate.rejected is False

    def test_blind_check_none_for_non_complete_states(self):
        for state in ("IN_PROGRESS", "NEEDS_VERIFICATION", "BLOCKED", "NEEDS_INPUT"):
            assert _blind_verification_required(state, 44) is None
