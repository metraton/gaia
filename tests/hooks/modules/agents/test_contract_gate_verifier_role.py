#!/usr/bin/env python3
"""The finalize gate keys on the turn's binding, never on the agent's role.

An unbound turn (no ``plan_task_id``) self-completes whoever the agent is; a
plan-task-bound turn is gated whoever the agent is.
"""

from __future__ import annotations

import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[4] / "hooks"
PKG_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(HOOKS_DIR), str(PKG_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from adapters.claude_code import (  # noqa: E402
    GATE_MODE_FULL_VERDICT,
    GATE_MODE_THREE_CASE,
    _blind_verification_required,
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


def _complete_envelope(agent_id: str = "a1b2c30f1e2d3c4b5"):
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
                "method": "test", "result": "pass", "details": "suite green",
            },
        },
        "consolidation_report": None,
        "approval_request": None,
    }


class TestGateIgnoresAgentRole:
    def test_unbound_non_verifier_complete_is_accepted_full_verdict(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=True,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_FULL_VERDICT
        assert gate.anomalies == ()

    def test_unbound_non_verifier_complete_is_accepted_three_case(self):
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=False,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_THREE_CASE

    def test_bound_complete_is_rejected_for_any_agent(self):
        for agent_type in ("developer", "gaia-verifier"):
            gate = evaluate_contract_gate(
                _complete_envelope(), agent_type=agent_type,
                plan_task_id=44, ramp_enabled=True,
            )
            assert gate.rejected is True, f"{agent_type}: bound turn must be gated"


class TestBlindCheckHasNoRoleInput:
    def test_helper_signature_has_no_role_parameter(self):
        import inspect
        params = list(inspect.signature(_blind_verification_required).parameters)
        assert params == ["agent_state", "plan_task_id"]

    def test_helper_verdict_depends_only_on_binding(self):
        assert _blind_verification_required("COMPLETE", 44) is not None
        assert _blind_verification_required("COMPLETE", None) is None
