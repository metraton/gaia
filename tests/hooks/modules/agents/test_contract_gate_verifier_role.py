#!/usr/bin/env python3
"""Verifier-role gate (harness R2 -- NEEDS_VERIFICATION / verifier-gated
COMPLETE).

Brief: harness-r2-needs-verification-y-complete-restringido-por-rol-verificador
(plan_id=32, task order_num=4, AC-3 + AC-5).

Covers the locked decisions verbatim:

1. DORMANT until B3: an empty verifier registry means every producer may
   still self-COMPLETE (today's behavior, unchanged) -- no deadlock.
2. PROPOSE, not COMPLETE: a producer may propose
   evidence_report.verification.result at NEEDS_VERIFICATION; the gate never
   treats NEEDS_VERIFICATION as a completed/done state regardless of that
   proposed value.
3. ENFORCE IN BOTH RAMP PATHS: evaluate_contract_gate (ramp-ON) AND
   _three_case_verdict (ramp-OFF) both reject a non-verifier COMPLETE once
   the registry is armed -- ramp-OFF is not a bypass.
4. ARMED-FIRST ORDERING: the check is "is the registry non-empty, and if so
   is this agent in it" -- NOT "is this agent in it" alone (which would
   deadlock every producer the moment the code lands, before any agent has
   opted in).
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
    _three_case_verdict,
    _verifier_role_violation,
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


def _envelope(plan_status: str, agent_id: str = "a1b2c3"):
    return {
        "agent_status": {
            "plan_status": plan_status,
            "agent_id": agent_id,
            "pending_steps": [],
            "next_action": "done" if plan_status == "COMPLETE" else "continue",
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


def _needs_verification_envelope_with_proposed_result(result: str = "pass"):
    """A producer proposing evidence_report.verification.result alongside
    NEEDS_VERIFICATION -- the shape core does not require or enforce
    verification on a non-COMPLETE status, so this is carried through, but
    the gate must never treat it as a completed/done state."""
    env = _envelope("NEEDS_VERIFICATION")
    env["evidence_report"]["verification"] = {
        "method": "test", "result": result, "details": "proposed by producer",
    }
    return env


@pytest.fixture(autouse=True)
def _clean_verifier_cache(monkeypatch):
    """Every test starts with a fresh fleet cache. Since B3 M2 (ARMING), the
    real ``agents/`` tree is itself ARMED (``agents/gaia-verifier.md`` ships
    live) -- so a test exercising DORMANT-registry behavior must explicitly
    seed an isolated, verifier-free ``agents/`` fixture via ``_seed_fleet(...,
    [])`` rather than relying on the live tree being empty. Tests that want
    the ARMED behavior seed ``_seed_fleet(..., ["gaia-verifier"])`` as before."""
    verifier_fleet.cache_clear()
    yield
    verifier_fleet.cache_clear()


def _seed_fleet(monkeypatch, tmp_path, verifier_names):
    """Point gaia.state.permissions._agents_dir at a synthetic agents/ dir
    carrying `verifier: true` for each name in verifier_names."""
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
# 1. DORMANT (empty registry) -- producers may still self-COMPLETE
# ---------------------------------------------------------------------------

class TestDormantRegistryContractGateVerifier:
    """Since B3 M2 (ARMING) the live ``agents/`` tree itself carries
    ``agents/gaia-verifier.md`` (armed), so DORMANT-branch behavior is proven
    against an isolated, verifier-free fixture (``_seed_fleet(..., [])``)
    rather than the live tree -- the dormant CODE PATH is still real
    behavior (a workspace / installed layout with no verifier-marked agent)
    and must stay covered independently of the live tree's current state."""

    def test_empty_registry_allows_producer_complete_full_verdict(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, [])
        assert verifier_fleet() == frozenset()
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer", ramp_enabled=True,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_FULL_VERDICT

    def test_empty_registry_allows_producer_complete_three_case(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, [])
        assert verifier_fleet() == frozenset()
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer", ramp_enabled=False,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_THREE_CASE

    def test_verifier_role_violation_returns_none_when_dormant(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, [])
        assert _verifier_role_violation("COMPLETE", "any-producer") is None


# ---------------------------------------------------------------------------
# 3. ARMED registry -- non-verifier COMPLETE rejected in BOTH ramp paths
# ---------------------------------------------------------------------------

class TestArmedRegistryContractGateVerifierBothPaths:
    def test_armed_registry_rejects_non_verifier_complete_full_verdict(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer", ramp_enabled=True,
        )
        assert gate.rejected is True
        assert gate.mode == GATE_MODE_FULL_VERDICT
        assert "not a seeded verifier" in gate.rejection_reason
        assert any(a["code"] == "VERIFIER_REQUIRED" for a in gate.anomalies)

    def test_armed_registry_rejects_non_verifier_complete_three_case(self, monkeypatch, tmp_path):
        """Ramp-OFF is NOT a bypass -- the identical armed-registry rejection
        must fire in _three_case_verdict too."""
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="developer", ramp_enabled=False,
        )
        assert gate.rejected is True
        assert gate.mode == GATE_MODE_THREE_CASE
        assert "not a seeded verifier" in gate.rejection_reason

    def test_armed_registry_allows_seeded_verifier_complete_full_verdict(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="gaia-verifier", ramp_enabled=True,
        )
        assert gate.rejected is False

    def test_armed_registry_allows_seeded_verifier_complete_three_case(self, monkeypatch, tmp_path):
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = evaluate_contract_gate(
            _complete_envelope(), agent_type="gaia-verifier", ramp_enabled=False,
        )
        assert gate.rejected is False

    def test_three_case_verdict_called_directly_rejects_non_verifier(self, monkeypatch, tmp_path):
        """Exercises _three_case_verdict directly (not only through the
        evaluate_contract_gate dispatcher) so the ramp-OFF path is proven
        independently touched, not assumed inherited from ramp-ON."""
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = _three_case_verdict(_complete_envelope(), "developer")
        assert gate.rejected is True
        assert gate.mode == GATE_MODE_THREE_CASE


# ---------------------------------------------------------------------------
# 4. ARMED-FIRST ORDERING -- registry-non-empty is checked before is_verifier
# ---------------------------------------------------------------------------

class TestArmedFirstOrderingContractGateVerifier:
    def test_dormant_producer_not_in_any_fleet_is_still_allowed(self, monkeypatch, tmp_path):
        """The critical ordering bug this guards against: checking
        is_verifier(agent) alone (with no registry-empty guard) would reject
        EVERY producer once this code lands. Proves a producer with NO
        relationship to a genuinely empty (dormant) verifier fleet still
        passes -- exercised against an isolated fixture since the live tree
        is armed as of B3 M2."""
        _seed_fleet(monkeypatch, tmp_path, [])
        assert verifier_fleet() == frozenset()
        violation = _verifier_role_violation("COMPLETE", "totally-unseeded-agent")
        assert violation is None


# ---------------------------------------------------------------------------
# 2. PROPOSE, NOT COMPLETE -- NEEDS_VERIFICATION with a proposed
# verification.result is never treated as done
# ---------------------------------------------------------------------------

class TestNeedsVerificationProposeNotComplete:
    def test_needs_verification_propose_pass_not_rejected_dormant(self):
        """A producer may propose verification.result='pass' at
        NEEDS_VERIFICATION -- the gate does not reject the shape (dormant
        registry), but this is NOT the same as accepting it as COMPLETE."""
        gate = evaluate_contract_gate(
            _needs_verification_envelope_with_proposed_result("pass"),
            agent_type="developer",
            ramp_enabled=True,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_FULL_VERDICT

    def test_needs_verification_propose_never_treated_as_complete_verdict(self):
        """The verifier-role check ONLY gates plan_status == COMPLETE.
        NEEDS_VERIFICATION never triggers it, proposed result notwithstanding
        -- it is not accepted AS done, it is simply not a violation of the
        COMPLETE-only role gate."""
        assert _verifier_role_violation("NEEDS_VERIFICATION", "developer") is None

    def test_needs_verification_propose_armed_registry_still_not_rejected(self, monkeypatch, tmp_path):
        """Even with the registry ARMED, a non-verifier proposing
        NEEDS_VERIFICATION is not rejected by the verifier-role gate -- only
        an actual COMPLETE from a non-verifier is."""
        _seed_fleet(monkeypatch, tmp_path, ["gaia-verifier"])
        gate = evaluate_contract_gate(
            _needs_verification_envelope_with_proposed_result("pass"),
            agent_type="developer",
            ramp_enabled=True,
        )
        assert gate.rejected is False

    def test_needs_verification_propose_fail_also_not_rejected(self):
        """A producer may equally propose a 'fail' result at
        NEEDS_VERIFICATION -- the shape core never enforces verification on a
        non-COMPLETE status, so neither result value is rejected here; the
        verifier's own judgement (not this gate) decides what happens next."""
        gate = evaluate_contract_gate(
            _needs_verification_envelope_with_proposed_result("fail"),
            agent_type="developer",
            ramp_enabled=True,
        )
        assert gate.rejected is False

    def test_needs_verification_three_case_path_also_not_rejected(self):
        gate = evaluate_contract_gate(
            _needs_verification_envelope_with_proposed_result("pass"),
            agent_type="developer",
            ramp_enabled=False,
        )
        assert gate.rejected is False
        assert gate.mode == GATE_MODE_THREE_CASE
