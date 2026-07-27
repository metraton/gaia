"""The optional failure_report axis: advisory, additive, shape-checked on presence.

The block lets a turn state a concrete defect it suffered -- what it attempted,
what broke, and the observed proof -- without that report ever becoming a
requirement. Two properties carry the whole design and are asserted separately
here, because passing one while failing the other is exactly the regression
that would matter:

    ABSENCE is never an error, on any agent_state. Terminal rows persisted
    before the field existed must keep the verdict they had, so the check is
    gated purely on presence -- an omitted key and an explicit null both reach
    no check at all.

    PRESENCE is validated in full. Once a turn declares the block it must be
    consumable by a writer, so a missing sub-field, an evidence list with
    nothing in it, or an out-of-enum severity is a FAILURE_REPORT_SHAPE
    rejection -- the same "optional to declare, well-formed once declared"
    contract VERIFICATION_SHAPE already applies to verification.type.

The two entry points (the parsed dict straight into the core, and the same
envelope re-extracted from a fence) are asserted to reach the identical
verdict: a field accepted on one path and rejected on the other is a contract
that builds one way and fails the other.
"""

import sys
from pathlib import Path

import pytest

from gaia.contract.validator import (
    VALID_FAILURE_SEVERITIES,
    VALID_PLAN_STATUSES,
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


def _well_formed_report() -> dict:
    return {
        "attempted": "gaia contract finalize --plan-task-id 65",
        "symptom": "the CLI rejected the finalize and the row never landed",
        "evidence": ["Rejected: agent_id mismatch: the draft is keyed to 'a1b2'"],
    }


def _envelope_with_report(report) -> dict:
    env = _valid_envelope()
    env["failure_report"] = report
    return env


def _codes(envelope: dict):
    return validate_form(envelope).codes


# ---------------------------------------------------------------------------
# Absence is never an error -- the property that protects persisted history.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("state", VALID_PLAN_STATUSES)
def test_absent_failure_report_is_valid_on_every_agent_state(state):
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = state
    if state == "COMPLETE":
        env["agent_status"]["next_action"] = "done"
        env["evidence_report"]["verification"] = {"method": "test", "result": "pass"}
    if state == "APPROVAL_REQUEST":
        env["approval_request"] = {"exact_content": "git -C /repo push"}

    assert "failure_report" not in env
    assert FormErrorCode.FAILURE_REPORT_SHAPE not in _codes(env)


def test_explicit_null_failure_report_is_valid():
    """Null is how consolidation_report/approval_request are habitually left;
    the new slot must read the same way rather than as a malformed object."""
    assert validate_form(_envelope_with_report(None)).ok is True


# ---------------------------------------------------------------------------
# Presence: a well-formed block is accepted.
# ---------------------------------------------------------------------------
def test_well_formed_failure_report_is_accepted():
    assert validate_form(_envelope_with_report(_well_formed_report())).ok is True


def test_well_formed_failure_report_accepted_on_a_complete_turn():
    """A turn can finish successfully AND still report a defect it survived --
    the axis is orthogonal to the outcome, not a contradiction of it."""
    env = _envelope_with_report(_well_formed_report())
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    env["evidence_report"]["verification"] = {"method": "test", "result": "pass"}

    assert validate_form(env).ok is True


@pytest.mark.parametrize("severity", VALID_FAILURE_SEVERITIES)
def test_optional_fields_are_accepted(severity):
    report = _well_formed_report()
    report["severity"] = severity
    report["component"] = "bin/cli/contract.py"

    assert validate_form(_envelope_with_report(report)).ok is True


# ---------------------------------------------------------------------------
# Presence: every malformed shape rejects with FAILURE_REPORT_SHAPE and nothing
# else, so one defect never fans out into several codes.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutate,expected_field",
    [
        (lambda r: "not an object", "failure_report"),
        (lambda r: {**r, "attempted": ""}, "failure_report.attempted"),
        (lambda r: {k: v for k, v in r.items() if k != "attempted"},
         "failure_report.attempted"),
        (lambda r: {**r, "symptom": "   "}, "failure_report.symptom"),
        (lambda r: {k: v for k, v in r.items() if k != "symptom"},
         "failure_report.symptom"),
        (lambda r: {**r, "evidence": []}, "failure_report.evidence"),
        (lambda r: {**r, "evidence": ["", "  "]}, "failure_report.evidence"),
        (lambda r: {**r, "evidence": "a string, not a list"},
         "failure_report.evidence"),
        (lambda r: {k: v for k, v in r.items() if k != "evidence"},
         "failure_report.evidence"),
        (lambda r: {**r, "severity": "catastrophic"}, "failure_report.severity"),
    ],
)
def test_malformed_failure_report_rejects(mutate, expected_field):
    env = _envelope_with_report(mutate(_well_formed_report()))

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.FAILURE_REPORT_SHAPE]
    assert any(err.field == expected_field for err in result.errors), (
        f"expected an error on {expected_field}, got "
        f"{[err.field for err in result.errors]}"
    )


def test_evidence_requirement_is_what_keeps_the_block_from_being_prose():
    """A report that states a failure but cites nothing observed is rejected --
    the block carries proof, not a claim."""
    report = {
        "attempted": "ran the migration",
        "symptom": "it seemed to hang for a while",
        "evidence": [],
    }

    result = validate_form(_envelope_with_report(report))

    assert result.ok is False
    assert result.codes == [FormErrorCode.FAILURE_REPORT_SHAPE]


# ---------------------------------------------------------------------------
# One core, two paths: the fence-extracted dict reaches the same verdict.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "report",
    [None, _well_formed_report(), {"attempted": "x"}, "not an object"],
)
def test_fence_path_and_direct_path_agree(report):
    import json

    env = _envelope_with_report(report)
    direct = validate_form(env)

    parsed = parse_contract(
        "```agent_contract_handoff\n" + json.dumps(env) + "\n```\n"
    )
    assert parsed is not None
    parsed.pop("_contract_tag", None)
    fenced = validate_form(parsed)

    assert (direct.ok, direct.codes) == (fenced.ok, fenced.codes)
