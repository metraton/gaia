#!/usr/bin/env python3
"""The finalize gate is DECOUPLED from the verifier registry (plan 34 task 7).

Brief: contrato-binding-y-verificacion-por-task-id (plan_id=34, task order_num=7).

Historical note: until task 7 this file asserted the OLD harness-R2 behavior --
a non-verifier COMPLETE was rejected once the verifier registry armed (keyed on
``gaia.state.permissions.verifier_fleet`` / ``is_verifier``). That role/registry
coupling has been REMOVED from the finalize gate: the gate now keys on the
turn's dispatch binding (``plan_task_id``), never on who the agent is. The
positive plan_task_id behavior lives in
``tests/hooks/test_verification_keyed_on_task_id.py`` and
``tests/hooks/test_memory_self_completes.py``; THIS file is the regression guard
proving the decoupling -- an armed verifier registry no longer influences the
gate at all.

The registry infrastructure itself (skill injection, dispatch-side role
detection) is unchanged and still exercised by ``tests/test_verifier_registry.py``
and friends; only the FINALIZE gate stopped consulting it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

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
from gaia.state import permissions as _permissions  # noqa: E402
from gaia.state.permissions import verifier_fleet  # noqa: E402


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


def _complete_envelope(agent_id: str = "a1b2c3"):
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


@pytest.fixture(autouse=True)
def _clean_verifier_cache():
    verifier_fleet.cache_clear()
    yield
    verifier_fleet.cache_clear()


def _seed_fleet(monkeypatch, tmp_path, verifier_names):
    """Point gaia.state.permissions._agents_dir at a synthetic agents/ dir
    carrying ``verifier: true`` for each name -- i.e. an ARMED registry."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    for name in verifier_names:
        (agents_dir / f"{name}.md").write_text(
            f"---\nname: {name}\nverifier: true\n---\nBody.\n", encoding="utf-8",
        )
    (agents_dir / "developer.md").write_text(
        "---\nname: developer\n---\nBody.\n", encoding="utf-8",
    )
    monkeypatch.setattr(_permissions, "_agents_dir", lambda: agents_dir)
    verifier_fleet.cache_clear()


# ---------------------------------------------------------------------------
# An ARMED verifier registry no longer influences the finalize gate.
# ---------------------------------------------------------------------------

class TestArmedRegistryNoLongerGatesComplete:
    def test_armed_registry_allows_unbound_non_verifier_complete_full_verdict(
        self, monkeypatch, tmp_path
    ):
        """The exact case the OLD gate rejected: a non-verifier COMPLETE with the
        registry ARMED. Now that the gate keys on plan_task_id, an UNBOUND turn
        (plan_task_id=None) self-completes regardless of the armed registry."""
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        assert verifier_fleet() == frozenset({"gaia-verifier"})
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=True,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_FULL_VERDICT
        assert gate.anomalies == ()

    def test_armed_registry_allows_unbound_non_verifier_complete_three_case(
        self, monkeypatch, tmp_path
    ):
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer",
            plan_task_id=None, ramp_enabled=False,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_THREE_CASE

    def test_bound_complete_rejected_regardless_of_registry_state(
        self, monkeypatch, tmp_path
    ):
        """And a BOUND COMPLETE is rejected whether the registry is armed or
        empty -- the verdict tracks plan_task_id, not the fleet."""
        for names in ([], ["gaia-verifier"]):
            _seed_fleet(monkeypatch, tmp_path, names)
            gate = evaluate_contract_gate(
                _complete_envelope(), agent_type="developer",
                plan_task_id=44, ramp_enabled=True,
            )
            assert gate.rejected is True, f"fleet={names}: bound turn must be gated"


# ---------------------------------------------------------------------------
# The gate helper does not consult the registry -- it is a pure function of
# (agent_state, plan_task_id).
# ---------------------------------------------------------------------------

class TestBlindCheckIsRegistryBlind:
    def test_helper_signature_has_no_role_parameter(self):
        import inspect
        params = list(inspect.signature(_blind_verification_required).parameters)
        assert params == ["agent_state", "plan_task_id"]

    def test_helper_verdict_independent_of_fleet(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        # Armed registry present, yet the helper's verdict depends only on the
        # binding: bound -> reason, unbound -> None.
        assert _blind_verification_required("COMPLETE", 44) is not None
        assert _blind_verification_required("COMPLETE", None) is None
