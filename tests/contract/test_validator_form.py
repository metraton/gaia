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
            "agent_state": "IN_PROGRESS",
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
    env["agent_status"]["agent_state"] = "COMPLETE"
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
    env["agent_status"]["agent_state"] = "BOGUS"

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


# ---------------------------------------------------------------------------
# R4 -- APPROVAL_REQUEST_SHAPE (Part 1a: approval_request presence + shape)
# ---------------------------------------------------------------------------
def _valid_approval_request_envelope() -> dict:
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = "APPROVAL_REQUEST"
    env["agent_status"]["next_action"] = "awaiting user approval"
    env["approval_request"] = {
        "operation": "MUTATIVE command intercepted: push",
        "exact_content": "git push origin main",
        "scope": "git",
        "risk_level": "MEDIUM",
        "rollback": None,
        "verification": "confirm the push landed on origin/main",
        "approval_id": "P-deadbeefcafebabe0000000000000000",
    }
    return env


def test_positive_approval_request_with_block_present():
    """A well-formed APPROVAL_REQUEST (approval_request present, exact_content
    non-empty) is shape-valid."""
    result = validate_form(_valid_approval_request_envelope())

    assert result.ok is True
    assert result.errors == ()
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


def test_negative_approval_request_missing_block():
    """APPROVAL_REQUEST with approval_request left null/absent is rejected --
    the FORM layer closes the gap where a plan_status of APPROVAL_REQUEST
    previously carried no shape guarantee about the block itself."""
    env = _valid_approval_request_envelope()
    env["approval_request"] = None

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.APPROVAL_REQUEST_SHAPE]
    offending = [
        e for e in result.errors if e.code == FormErrorCode.APPROVAL_REQUEST_SHAPE
    ]
    assert offending and offending[0].field == "approval_request"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


def test_negative_approval_request_missing_exact_content():
    """APPROVAL_REQUEST with approval_request present but exact_content blank
    is rejected -- the user cannot give informed consent without seeing the
    verbatim content."""
    env = _valid_approval_request_envelope()
    env["approval_request"]["exact_content"] = ""

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.APPROVAL_REQUEST_SHAPE]
    offending = [
        e for e in result.errors if e.code == FormErrorCode.APPROVAL_REQUEST_SHAPE
    ]
    assert offending and offending[0].field == "approval_request.exact_content"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


def test_positive_approval_request_without_approval_id_still_valid():
    """approval_id is deliberately NOT required: agent-response documents a
    legitimate approval_request with no approval_id yet (a plan presented
    before the hook has blocked anything and minted a grant)."""
    env = _valid_approval_request_envelope()
    del env["approval_request"]["approval_id"]

    result = validate_form(env)

    assert result.ok is True
    assert result.errors == ()


# ---------------------------------------------------------------------------
# R4 -- COMPLETE_SHAPE (Part 1b: COMPLETE => next_action=='done', pending_steps==[])
# ---------------------------------------------------------------------------
def test_positive_complete_with_done_and_empty_pending_steps():
    """A well-formed COMPLETE (next_action == 'done', pending_steps == [])
    is shape-valid -- the baseline this rule must not disturb."""
    result = validate_form(_valid_complete_envelope())

    assert result.ok is True
    assert result.errors == ()


def test_negative_complete_next_action_not_done():
    env = _valid_complete_envelope()
    env["agent_status"]["next_action"] = "keep going"

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.COMPLETE_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.COMPLETE_SHAPE]
    assert offending and offending[0].field == "agent_status.next_action"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE


def test_negative_complete_pending_steps_not_empty():
    env = _valid_complete_envelope()
    env["agent_status"]["pending_steps"] = ["one more thing"]

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.COMPLETE_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.COMPLETE_SHAPE]
    assert offending and offending[0].field == "agent_status.pending_steps"
    assert result.repair_message == CANONICAL_REPAIR_MESSAGE
