"""
AC-1 -- form-layer validator: NAMED error codes + canonical repair message.

The form layer validates an ``agent_contract_handoff`` envelope by SHAPE only
and rejects each malformed case with a NAMED code:

    * agent_id not matching ^a[0-9a-f]{5,}$  -> AGENT_ID_FORMAT
    * plan_status out of the canonical enum  -> PLAN_STATUS
    * COMPLETE without verification.result == "pass" -> VERIFICATION_RESULT
    * a missing required evidence_report key -> MISSING_FIELD

AC-1 requires this suite to enumerate the 4 negative cases + 1 positive case.
Each negative case is asserted to fire EXACTLY its one intended code (no
fan-out), and every result is asserted to carry the canonical rich repair
message.
"""

from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
    FormErrorCode,
    validate_form,
)


def _valid_envelope() -> dict:
    """A fully shape-valid, non-COMPLETE envelope used as the mutation base."""
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


def _valid_complete_envelope() -> dict:
    env = _valid_envelope()
    env["agent_status"]["plan_status"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    env["evidence_report"]["verification"] = {
        "method": "test",
        "result": "pass",
        "details": "pytest green",
    }
    return env


# ---------------------------------------------------------------------------
# Negative 1/4 -- AGENT_ID_FORMAT
# ---------------------------------------------------------------------------
def test_negative_agent_id_format():
    env = _valid_envelope()
    env["agent_status"]["agent_id"] = "not-a-valid-id"

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.AGENT_ID_FORMAT]
    offending = [e for e in result.errors if e.code == FormErrorCode.AGENT_ID_FORMAT]
    assert offending and offending[0].field == "agent_status.agent_id"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


# ---------------------------------------------------------------------------
# Negative 2/4 -- PLAN_STATUS
# ---------------------------------------------------------------------------
def test_negative_plan_status_out_of_enum():
    env = _valid_envelope()
    env["agent_status"]["plan_status"] = "BOGUS"

    result = validate_form(env)

    assert result.ok is False
    # Exactly one code -- an unknown status must NOT also fan out into a
    # missing-evidence error (one anomaly per invalidity).
    assert result.codes == [FormErrorCode.PLAN_STATUS]
    assert "BOGUS" in result.error_summary()
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


# ---------------------------------------------------------------------------
# Negative 3/4 -- VERIFICATION_RESULT
# ---------------------------------------------------------------------------
def test_negative_verification_result_not_pass():
    env = _valid_complete_envelope()
    env["evidence_report"]["verification"]["result"] = "fail"

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_RESULT]
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


# ---------------------------------------------------------------------------
# Negative 4/4 -- MISSING_FIELD
# ---------------------------------------------------------------------------
def test_negative_missing_required_evidence_key():
    env = _valid_envelope()
    del env["evidence_report"]["commands_run"]

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.MISSING_FIELD]
    offending = [e for e in result.errors if e.code == FormErrorCode.MISSING_FIELD]
    assert offending and offending[0].field == "evidence_report.commands_run"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


# ---------------------------------------------------------------------------
# Positive 1/1 -- a fully shape-valid envelope passes
# ---------------------------------------------------------------------------
def test_positive_valid_envelope():
    result = validate_form(_valid_complete_envelope())

    assert result.ok is True
    assert result.errors == ()
    # The canonical repair message is ALWAYS returned, even on success.
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE
