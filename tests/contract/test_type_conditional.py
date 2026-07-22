"""
R3 -- type-conditional verification shape validation (VERIFICATION_SHAPE).

The form layer validates ``evidence_report.verification`` conditionally BY TYPE,
mirroring the pre-existing conditional-BY-VALUE pattern (VERIFICATION_RESULT):
when ``verification.type`` names a KNOWN type (SSOT: gaia.state.
VALID_VERIFICATION_TYPES), validate_form requires the field that type demands
and rejects an omission with the additive, distinct code VERIFICATION_SHAPE.

  * "command"/"code" (deterministic) -> non-empty ``command`` (the oracle to run)
  * "semantic"                       -> truthy ``requires_human`` marker
  * "self_review"                    -> non-empty ``reviewed`` statement

Backward compatible: an ABSENT verification.type (or a type outside the enum)
fires no new requirement -- behaviour is identical to pre-R3.

Test-naming contract (brief AC-1 / AC-4):
  * ``pytest -k type_conditional``       selects the NEGATIVE and POSITIVE cases.
  * ``pytest -k type_conditional_valid`` selects ONLY the POSITIVE cases (the
    negative cases deliberately omit the substring "valid").
"""

from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
    VALID_VERIFICATION_TYPES,
    FormErrorCode,
    validate_form,
)
from gaia.state import VALID_VERIFICATION_TYPES as STATE_VERIFICATION_TYPES

import pytest


def _base_envelope() -> dict:
    """A shape-valid, non-COMPLETE (IN_PROGRESS) envelope -- the mutation base.

    IN_PROGRESS isolates the by-TYPE check from the by-VALUE COMPLETE/result
    check, so a fired VERIFICATION_SHAPE is provably the only invalidity.
    """
    return {
        "agent_status": {
            "plan_status": "IN_PROGRESS",
            "agent_id": "a1b2c3",
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


def _with_verification(verification: dict) -> dict:
    env = _base_envelope()
    env["evidence_report"]["verification"] = verification
    return env


# A valid verification block for each enum type (POSITIVE fixtures).
_VALID_VERIFICATION_BY_TYPE = {
    "command": {"method": "command", "type": "command", "command": "pytest -q"},
    "code": {"method": "code", "type": "code", "command": "ruff check ."},
    "semantic": {"method": "semantic", "type": "semantic", "requires_human": True},
    "self_review": {
        "method": "self_review",
        "type": "self_review",
        "reviewed": "re-read the diff and confirmed the branch is additive",
    },
}


# ---------------------------------------------------------------------------
# Sanity: SSOT lives in gaia.state and the validator mirror matches it (AC-2).
# ---------------------------------------------------------------------------
def test_type_conditional_enum_ssot_matches_validator_mirror():
    assert VALID_VERIFICATION_TYPES == STATE_VERIFICATION_TYPES
    assert set(VALID_VERIFICATION_TYPES) == {
        "command",
        "code",
        "semantic",
        "self_review",
    }
    # Every enum type has a positive fixture below (keeps AC-4 exhaustive).
    assert set(_VALID_VERIFICATION_BY_TYPE) == set(VALID_VERIFICATION_TYPES)


# ---------------------------------------------------------------------------
# AC-1 (NEGATIVE): a declared deterministic type omitting its required field is
# rejected with EXACTLY VERIFICATION_SHAPE (one code per invalidity). These
# names contain "type_conditional" but NOT "type_conditional_valid".
# ---------------------------------------------------------------------------
def test_type_conditional_deterministic_command_omits_field_rejected():
    env = _with_verification({"method": "command", "type": "command"})

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_SHAPE]
    assert offending and offending[0].field == "evidence_report.verification.command"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


def test_type_conditional_deterministic_code_omits_field_rejected():
    env = _with_verification({"method": "code", "type": "code", "command": "  "})

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]


def test_type_conditional_semantic_omits_marker_rejected():
    env = _with_verification({"method": "semantic", "type": "semantic"})

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_SHAPE]
    assert offending[0].field == "evidence_report.verification.requires_human"


def test_type_conditional_self_review_omits_statement_rejected():
    env = _with_verification({"method": "self_review", "type": "self_review"})

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_SHAPE]
    assert offending[0].field == "evidence_report.verification.reviewed"


def test_type_conditional_shape_distinct_from_verification_result():
    """A COMPLETE contract can fail BOTH invalidities at once: result != pass
    (VERIFICATION_RESULT) AND a declared type missing its field
    (VERIFICATION_SHAPE). They are distinct codes and both fire."""
    env = _base_envelope()
    env["agent_status"]["plan_status"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    env["evidence_report"]["verification"] = {
        "method": "command",
        "type": "command",  # missing required 'command' -> VERIFICATION_SHAPE
        "result": "fail",  # not pass -> VERIFICATION_RESULT
    }

    result = validate_form(env)

    assert result.ok is False
    assert set(result.codes) == {
        FormErrorCode.VERIFICATION_SHAPE,
        FormErrorCode.VERIFICATION_RESULT,
    }


# ---------------------------------------------------------------------------
# Backward compatibility: absent type, or a type outside the enum, adds NO new
# requirement. (Selected by -k type_conditional; deliberately no "valid".)
# ---------------------------------------------------------------------------
def test_type_conditional_absent_type_is_backward_compatible():
    # A verification block with NO 'type' key -- pre-R3 behaviour: no check.
    env = _with_verification({"method": "free text", "details": "whatever"})

    result = validate_form(env)

    assert result.ok is True
    assert result.errors == ()


def test_type_conditional_unknown_type_fires_no_shape_check():
    env = _with_verification({"method": "x", "type": "totally-unknown-type"})

    result = validate_form(env)

    assert result.ok is True


# ---------------------------------------------------------------------------
# AC-4 (POSITIVE): a valid contract of EACH enum type passes validate_form.
# These names contain "type_conditional_valid".
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("vtype", list(VALID_VERIFICATION_TYPES))
def test_type_conditional_valid_each_type_passes(vtype):
    env = _with_verification(dict(_VALID_VERIFICATION_BY_TYPE[vtype]))

    result = validate_form(env)

    assert result.ok is True, result.error_summary()
    assert result.errors == ()
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


def test_type_conditional_valid_complete_command_contract_passes():
    """A COMPLETE contract declaring a deterministic type with BOTH its
    required 'command' AND result == 'pass' satisfies both branches."""
    env = _base_envelope()
    env["agent_status"]["plan_status"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    env["evidence_report"]["verification"] = {
        "method": "command",
        "type": "command",
        "command": "pytest tests/contract/ -q",
        "result": "pass",
        "details": "green",
    }

    result = validate_form(env)

    assert result.ok is True, result.error_summary()
