"""B3 T1 (plan_id=33, AC-1): the deterministic-oracle re-execution mechanism.

Matchable by ``pytest tests/ -k verifier_oracle -q``.

``gaia.state.gate_oracle.run_oracle_check`` is the machinery the
``verification-oracle`` skill (skills/verification-oracle/SKILL.md) describes:
given a task_gates entry (or a proposed contract verification block) whose
``verification_type`` is ``command`` or ``code``, it RE-EXECUTES the declared
check and compares the ACTUAL exit code against the gate's expected value,
returning an objective pass/fail plus the evidence that produced it.

Coverage, per AC-1:
  * a PASSING oracle re-run for a ``command``-type gate;
  * a FAILING oracle re-run for a ``command``-type gate;
  * a PASSING oracle re-run for a ``code``-type gate;
  * a FAILING oracle re-run for a ``code``-type gate;
  * both the persisted gate shape (``evidence_shape``) and the contract
    envelope shape (``command``) are accepted;
  * a gate-declared ``expected_exit_code`` overrides the exit-0 default;
  * non-deterministic types (``semantic``/``self_review``) are rejected
    without any execution attempt -- this mode does not apply to them.

This is M1 (dormant machinery): the module and skill exist and are fully
tested here, but no agent (in particular no ``gaia-verifier``) calls them yet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from gaia.state.gate_oracle import (  # noqa: E402
    DETERMINISTIC_ORACLE_TYPES,
    OracleVerdict,
    run_oracle_check,
)

_PY = sys.executable


# ---------------------------------------------------------------------------
# command-type gate: passing and failing oracle re-run
# ---------------------------------------------------------------------------

def test_verifier_oracle_command_type_passing_rerun():
    gate = {
        "verification_type": "command",
        "evidence_shape": f'{_PY} -c "import sys; sys.exit(0)"',
    }
    verdict = run_oracle_check(gate)
    assert isinstance(verdict, OracleVerdict)
    assert verdict.ok is True
    assert verdict.verification_type == "command"
    assert verdict.exit_code == 0
    assert verdict.expected_exit_code == 0
    assert verdict.errors == []


def test_verifier_oracle_command_type_failing_rerun():
    gate = {
        "verification_type": "command",
        "evidence_shape": f'{_PY} -c "import sys; sys.exit(1)"',
    }
    verdict = run_oracle_check(gate)
    assert verdict.ok is False
    assert verdict.verification_type == "command"
    assert verdict.exit_code == 1
    assert verdict.expected_exit_code == 0
    assert any("exit code" in e for e in verdict.errors)


# ---------------------------------------------------------------------------
# code-type gate: passing and failing oracle re-run
#
# A "code" gate's check spec is, structurally, the SAME runnable-string shape
# as "command" (gaia.state.__init__: the two are "synonyms for the two shapes
# of a deterministic check") -- here it stands in for a narrower code-level
# check (an assertion), rather than a broader command (a test suite), to
# exercise the type distinction the AC calls out without inventing a second
# execution mechanism.
# ---------------------------------------------------------------------------

def test_verifier_oracle_code_type_passing_rerun():
    gate = {
        "verification_type": "code",
        "evidence_shape": f'{_PY} -c "assert 1 == 1"',
    }
    verdict = run_oracle_check(gate)
    assert verdict.ok is True
    assert verdict.verification_type == "code"
    assert verdict.exit_code == 0
    assert verdict.errors == []


def test_verifier_oracle_code_type_failing_rerun():
    gate = {
        "verification_type": "code",
        "evidence_shape": f'{_PY} -c "assert 1 == 2"',
    }
    verdict = run_oracle_check(gate)
    assert verdict.ok is False
    assert verdict.verification_type == "code"
    assert verdict.exit_code == 1
    assert "AssertionError" in verdict.stderr
    assert any("exit code" in e for e in verdict.errors)


# ---------------------------------------------------------------------------
# Both check-spec shapes are accepted: evidence_shape (gate) and command
# (contract envelope) -- evidence_shape wins when both are present.
# ---------------------------------------------------------------------------

def test_verifier_oracle_accepts_envelope_shape_command_field():
    envelope_block = {
        "method": "code",
        "type": "code",
        "command": f'{_PY} -c "import sys; sys.exit(0)"',
    }
    verdict = run_oracle_check(envelope_block)
    assert verdict.ok is True
    assert verdict.verification_type == "code"


def test_verifier_oracle_evidence_shape_wins_over_command_field():
    gate = {
        "verification_type": "command",
        "evidence_shape": f'{_PY} -c "import sys; sys.exit(0)"',
        "command": f'{_PY} -c "import sys; sys.exit(1)"',
    }
    verdict = run_oracle_check(gate)
    assert verdict.ok is True
    assert verdict.exit_code == 0


# ---------------------------------------------------------------------------
# expected_exit_code overrides the exit-0 default -- the comparison is
# against the gate's declared expectation, not a hardcoded convention.
# ---------------------------------------------------------------------------

def test_verifier_oracle_respects_declared_expected_exit_code():
    gate = {
        "verification_type": "command",
        "evidence_shape": f'{_PY} -c "import sys; sys.exit(2)"',
        "expected_exit_code": 2,
    }
    verdict = run_oracle_check(gate)
    assert verdict.ok is True
    assert verdict.exit_code == 2
    assert verdict.expected_exit_code == 2


# ---------------------------------------------------------------------------
# Non-deterministic types (semantic / self_review) are out of scope for this
# mode -- rejected without attempting execution.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("vtype", ["semantic", "self_review"])
def test_verifier_oracle_rejects_non_deterministic_types(vtype):
    gate = {"verification_type": vtype, "evidence_shape": "irrelevant"}
    verdict = run_oracle_check(gate)
    assert verdict.ok is False
    assert verdict.command == ""
    assert verdict.exit_code is None
    assert any("not a deterministic oracle type" in e for e in verdict.errors)


def test_verifier_oracle_deterministic_types_are_exactly_command_and_code():
    assert set(DETERMINISTIC_ORACLE_TYPES) == {"command", "code"}


# ---------------------------------------------------------------------------
# Missing check spec on a deterministic type is a hard rejection, never an
# assumed pass.
# ---------------------------------------------------------------------------

def test_verifier_oracle_missing_check_spec_is_rejected_not_assumed_pass():
    gate = {"verification_type": "command"}
    verdict = run_oracle_check(gate)
    assert verdict.ok is False
    assert any("no runnable check spec" in e for e in verdict.errors)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
