"""The optional work_phase axis: observable WORK cycle, orthogonal to agent_state.

``work_phase`` names WHERE the producer is in the work cycle (framing ->
investigating -> planning -> executing -> verifying) -- a signal that is
deliberately separate from ``agent_state`` (the communication state machine
IN_PROGRESS/BLOCKED/NEEDS_INPUT/APPROVAL_REQUEST/NEEDS_VERIFICATION/COMPLETE),
which stays a pure input to routing and the finalize/verification gate. Two
properties carry the whole design, mirroring the failure_report axis:

    ABSENCE is never an error, on any agent_state. A turn with no
    distinguishable work phase (a single read-only lookup) never sets the
    field, and an omitted key or an explicit null both reach no check at all.

    PRESENCE is validated in full. A declared work_phase outside
    VALID_WORK_PHASES is a WORK_PHASE_SHAPE rejection -- a typo does not
    silently pass as a null-equivalent.

The two entry points (the parsed dict straight into the core, and the same
envelope re-extracted from a fence) are asserted to reach the identical
verdict.
"""

import sys
from pathlib import Path

import pytest

from gaia.contract.validator import (
    VALID_PLAN_STATUSES,
    VALID_WORK_PHASES,
    FormErrorCode,
    validate_form,
)

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents.contract_validator import parse_contract  # noqa: E402


def _valid_envelope() -> dict:
    return {
        "agent_status": {
            "agent_state": "IN_PROGRESS",
            "agent_id": "a1b2c30f1e2d3c4b5",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
        },
        "consolidation_report": None,
        "approval_request": None,
    }


def _envelope_with_phase(phase) -> dict:
    env = _valid_envelope()
    env["work_phase"] = phase
    return env


def _codes(envelope: dict):
    return validate_form(envelope).codes


# ---------------------------------------------------------------------------
# Absence is never an error -- the property that protects a trivial turn from
# added ritual and every already-persisted row before this field existed.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("state", VALID_PLAN_STATUSES)
def test_absent_work_phase_is_valid_on_every_agent_state(state):
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = state
    if state == "COMPLETE":
        env["agent_status"]["next_action"] = "done"
        env["evidence_report"]["verification"] = {"method": "test", "result": "pass"}
    if state == "APPROVAL_REQUEST":
        env["approval_request"] = {"exact_content": "git -C /repo push"}

    assert "work_phase" not in env
    assert FormErrorCode.WORK_PHASE_SHAPE not in _codes(env)


def test_explicit_null_work_phase_is_valid():
    """Null is how the CLI seeds the slot at init (_initial_envelope); the
    field must read the same way as an omitted key, not as a malformed value."""
    assert validate_form(_envelope_with_phase(None)).ok is True


# ---------------------------------------------------------------------------
# Presence: every declared enum value is accepted, on every agent_state.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phase", VALID_WORK_PHASES)
def test_each_valid_phase_is_accepted(phase):
    assert validate_form(_envelope_with_phase(phase)).ok is True


def test_valid_phase_accepted_on_a_complete_turn():
    """A turn can close COMPLETE while still recording the phase it was in --
    the axis is orthogonal to the outcome, not a contradiction of it."""
    env = _envelope_with_phase("verifying")
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    env["evidence_report"]["verification"] = {"method": "test", "result": "pass"}

    assert validate_form(env).ok is True


def test_phase_value_is_case_insensitive():
    assert validate_form(_envelope_with_phase("PLANNING")).ok is True


# ---------------------------------------------------------------------------
# Presence: an out-of-enum value rejects with WORK_PHASE_SHAPE and nothing else.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "phase", ["framed", "done", "IN_PROGRESS", "", "   ", 42, ["planning"]]
)
def test_malformed_work_phase_rejects(phase):
    result = validate_form(_envelope_with_phase(phase))

    assert result.ok is False
    assert result.codes == [FormErrorCode.WORK_PHASE_SHAPE]
    assert any(err.field == "work_phase" for err in result.errors)


# ---------------------------------------------------------------------------
# One core, two paths: the fence-extracted dict reaches the same verdict.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phase", [None, "framing", "investigating", "bogus"])
def test_fence_path_and_direct_path_agree(phase):
    import json

    env = _envelope_with_phase(phase)
    direct = validate_form(env)

    parsed = parse_contract(
        "```agent_contract_handoff\n" + json.dumps(env) + "\n```\n"
    )
    assert parsed is not None
    parsed.pop("_contract_tag", None)
    fenced = validate_form(parsed)

    assert (direct.ok, direct.codes) == (fenced.ok, fenced.codes)
