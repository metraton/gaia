"""Tests for :func:`tests.evals.graders.contract_grader` (T3b).

Covers the five fixture classes called out in the plan:

(a) well-formed ``IN_PROGRESS`` contract
(b) well-formed ``APPROVAL_REQUEST`` with a valid ``approval_request`` shape
(c) malformed JSON in the fenced block
(d) missing required top-level keys
(e) wrong plan_status for the expected scenario
"""

from __future__ import annotations

import json
import textwrap

import pytest

from tests.evals.graders import GradeResult, contract_grader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap(contract_obj_or_str) -> str:
    """Wrap a dict (serialized as JSON) or raw string in a json:contract fence.

    The grader looks for fenced ``json:contract`` blocks anywhere in the
    response; we add a short narrative prefix to mimic the way agents frame
    contracts after a reply.
    """
    if isinstance(contract_obj_or_str, str):
        body = contract_obj_or_str
    else:
        body = json.dumps(contract_obj_or_str, indent=2)
    return textwrap.dedent(
        f"""\
        Some narrative from the agent about what it just did.

        ```json:contract
        {body}
        ```
        """
    )


def _well_formed(plan_status: str = "IN_PROGRESS", **overrides) -> dict:
    """Build a minimally valid contract dict. Overrides merged at top level."""
    contract = {
        "agent_status": {
            "plan_status": plan_status,
            "agent_id": "a12345",
            "pending_steps": [],
            "next_action": "done",
        },
        "evidence_report": {
            "patterns_checked": [],
            "files_checked": [],
            "commands_run": [],
            "key_outputs": [],
            "verbatim_outputs": [],
            "cross_layer_impacts": [],
            "open_gaps": [],
            "verification": None,
        },
        "consolidation_report": None,
        "approval_request": None,
    }
    contract.update(overrides)
    return contract


# ---------------------------------------------------------------------------
# (a) Well-formed IN_PROGRESS contract
# ---------------------------------------------------------------------------

def test_well_formed_in_progress_passes():
    response = _wrap(_well_formed(plan_status="IN_PROGRESS"))
    result = contract_grader(response, contract_expect={})
    assert isinstance(result, GradeResult)
    assert result.passed is True
    assert result.score == 1.0


def test_well_formed_complete_passes():
    response = _wrap(_well_formed(plan_status="COMPLETE"))
    result = contract_grader(response, contract_expect=None)
    assert result.passed is True
    assert result.score == 1.0


def test_well_formed_blocked_passes():
    response = _wrap(_well_formed(plan_status="BLOCKED"))
    result = contract_grader(response, contract_expect={})
    assert result.passed is True


def test_well_formed_needs_input_passes():
    response = _wrap(_well_formed(plan_status="NEEDS_INPUT"))
    result = contract_grader(response, contract_expect={})
    assert result.passed is True


# ---------------------------------------------------------------------------
# (b) Well-formed APPROVAL_REQUEST with valid approval_request shape
# ---------------------------------------------------------------------------

def _approval_contract() -> dict:
    return _well_formed(
        plan_status="APPROVAL_REQUEST",
        approval_request={
            "operation": "git push",
            "exact_content": "git push origin feature/foo",
            "scope": "repo",
            "risk_level": "MEDIUM",
            "rollback": "git push --delete origin feature/foo",
            "verification": "ci green",
        },
    )


def test_well_formed_approval_request_passes():
    response = _wrap(_approval_contract())
    result = contract_grader(
        response,
        contract_expect={"plan_status": "APPROVAL_REQUEST"},
    )
    assert result.passed is True
    assert result.score == 1.0


def test_approval_request_missing_required_field_fails():
    contract = _approval_contract()
    # Drop a load-bearing field
    contract["approval_request"].pop("operation")
    response = _wrap(contract)
    result = contract_grader(
        response,
        contract_expect={"plan_status": "APPROVAL_REQUEST"},
    )
    assert result.passed is False
    assert result.score == 0.0
    assert any("approval_request missing" in r for r in result.reasons)


def test_approval_request_not_an_object_fails():
    contract = _approval_contract()
    contract["approval_request"] = "pending"  # wrong type
    response = _wrap(contract)
    result = contract_grader(response, contract_expect={})
    assert result.passed is False
    assert any("not an object" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# (c) Malformed JSON in the fenced block
# ---------------------------------------------------------------------------

def test_malformed_json_fails():
    response = _wrap('{"agent_status": { "plan_status": "IN_PROGRESS", }')  # trailing comma + unclosed
    result = contract_grader(response, contract_expect={})
    assert result.passed is False
    assert result.score == 0.0
    assert any("not valid JSON" in r for r in result.reasons)


def test_no_contract_block_fails():
    response = "The agent forgot to emit a contract block entirely."
    result = contract_grader(response, contract_expect={})
    assert result.passed is False
    assert result.score == 0.0
    assert result.reasons == ["no json:contract fenced block found"]


def test_last_contract_block_is_used():
    """Agents sometimes show an example contract earlier in their narrative.

    The grader must use the LAST fenced block (the operative one), not the
    first. We seed a malformed early block and a valid late block and assert
    the result is pass.
    """
    early = "```json:contract\n{ malformed\n```"
    late = _wrap(_well_formed(plan_status="COMPLETE"))
    response = f"Here is an example:\n{early}\n\nAnd here is my real contract:\n{late}"
    result = contract_grader(response, contract_expect={})
    assert result.passed is True


# ---------------------------------------------------------------------------
# (d) Missing required top-level keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "missing_key",
    ["agent_status", "evidence_report", "consolidation_report", "approval_request"],
)
def test_missing_top_level_key_fails(missing_key):
    contract = _well_formed(plan_status="IN_PROGRESS")
    contract.pop(missing_key)
    response = _wrap(contract)
    result = contract_grader(response, contract_expect={})
    assert result.passed is False
    assert result.score == 0.0
    assert any(missing_key in r for r in result.reasons)


def test_agent_status_not_an_object_fails():
    contract = _well_formed()
    contract["agent_status"] = "COMPLETE"  # wrong type
    response = _wrap(contract)
    result = contract_grader(response, contract_expect={})
    assert result.passed is False
    assert any("agent_status must be an object" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# (e) Wrong plan_status for the expected scenario
# ---------------------------------------------------------------------------

def test_plan_status_mismatch_fails():
    """S6 expects APPROVAL_REQUEST but the agent shipped COMPLETE."""
    response = _wrap(_well_formed(plan_status="COMPLETE"))
    result = contract_grader(
        response,
        contract_expect={"plan_status": "APPROVAL_REQUEST"},
    )
    assert result.passed is False
    assert any("plan_status mismatch" in r for r in result.reasons)


def test_invalid_plan_status_enum_fails():
    contract = _well_formed()
    contract["agent_status"]["plan_status"] = "DONE"  # not in canonical enum
    response = _wrap(contract)
    result = contract_grader(response, contract_expect={})
    assert result.passed is False
    assert any("not in" in r for r in result.reasons)


def test_plan_status_missing_fails():
    contract = _well_formed()
    contract["agent_status"].pop("plan_status")
    response = _wrap(contract)
    result = contract_grader(response, contract_expect={})
    assert result.passed is False


# ---------------------------------------------------------------------------
# Scenario shape: S5 (contract_shape) -- any valid status passes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "status",
    ["IN_PROGRESS", "APPROVAL_REQUEST", "COMPLETE", "BLOCKED", "NEEDS_INPUT"],
)
def test_s5_any_valid_status_passes_when_expect_empty(status):
    """S5 only requires a well-formed contract -- not a specific status."""
    if status == "APPROVAL_REQUEST":
        contract = _approval_contract()
    else:
        contract = _well_formed(plan_status=status)
    response = _wrap(contract)
    result = contract_grader(response, contract_expect={})
    assert result.passed is True
    assert result.score == 1.0
