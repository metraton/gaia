"""
Order-aware rejection detail -- the form layer teaches build ORDER, not just denial.

Goal: writing agent_status.agent_state to a terminal value (COMPLETE,
APPROVAL_REQUEST) before the field that state depends on is filled is a
BUILD-ORDER mistake, not a content mistake. Before this change, the rejection
named only the missing/wrong content (e.g. "COMPLETE requires a verification
object with result == 'pass'") without ever saying that the fix is a matter of
WHICH FIELD to fill and in WHAT ORDER relative to agent_state. This suite
pins the improved ``detail`` text for every cross-field code reachable on a
normal turn (VERIFICATION_RESULT, COMPLETE_SHAPE, APPROVAL_REQUEST_SHAPE) and
confirms validation still rejects exactly what it rejected before -- no
envelope that was invalid becomes valid.
"""

from gaia.contract.validator import (
    FormErrorCode,
    validate_form,
)


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
# The measured reproduction: agent_state=COMPLETE set before
# evidence_report.verification is filled. This is the exact case reported --
# "COMPLETE requires a verification object with result == 'pass'" -- with no
# mention anywhere that the defect is ORDER, not content.
# ---------------------------------------------------------------------------
def test_negative_complete_before_verification_now_teaches_order():
    env = _valid_envelope()
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    # evidence_report.verification deliberately left unset -- the reproduced
    # ordering mistake: agent_state flipped to COMPLETE first.

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_RESULT]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_RESULT]
    assert offending and offending[0].field == "evidence_report.verification"
    detail = offending[0].detail

    # BEFORE the fix, this exact substring was the ENTIRE message -- i.e. it
    # never said anything about order. Confirm it is still present (the
    # rejection still names the same defect)...
    assert "requires evidence_report.verification" in detail
    assert "result == 'pass'" in detail
    # ...and confirm the NEW part: the message now names the missing field
    # AND the order to build in (dependency first, agent_state last).
    assert "order" in detail.lower()
    assert "evidence_report.verification" in detail
    assert "agent_state" in detail
    assert "before" in detail.lower() or "last" in detail.lower()

    # The turn-level repair message also carries a build-order paragraph.
    assert "build order" in result.repair_message.lower()


def test_negative_complete_verification_wrong_result_teaches_order():
    env = _valid_complete_envelope()
    env["evidence_report"]["verification"]["result"] = "fail"

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_RESULT]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_RESULT]
    assert offending and offending[0].field == "evidence_report.verification.result"
    detail = offending[0].detail
    assert "order" in detail.lower()
    assert "pass" in detail.lower()


# ---------------------------------------------------------------------------
# COMPLETE_SHAPE: the same order trap, one turn-status cycle earlier -- a
# leftover pending_steps entry, or a stale next_action, from an IN_PROGRESS
# turn that never got cleared before agent_state flipped to COMPLETE.
# ---------------------------------------------------------------------------
def test_negative_complete_pending_steps_teaches_order():
    env = _valid_complete_envelope()
    env["agent_status"]["pending_steps"] = ["one more thing"]

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.COMPLETE_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.COMPLETE_SHAPE]
    assert offending and offending[0].field == "agent_status.pending_steps"
    detail = offending[0].detail
    assert "order" in detail.lower()
    assert "pending_steps" in detail
    assert "agent_state" in detail


def test_negative_complete_next_action_teaches_order():
    env = _valid_complete_envelope()
    env["agent_status"]["next_action"] = "keep going"

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.COMPLETE_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.COMPLETE_SHAPE]
    assert offending and offending[0].field == "agent_status.next_action"
    detail = offending[0].detail
    assert "order" in detail.lower()
    assert "next_action" in detail


# ---------------------------------------------------------------------------
# APPROVAL_REQUEST_SHAPE: agent_state=APPROVAL_REQUEST set before
# approval_request is filled -- the sibling of the COMPLETE trap for a
# different terminal-ish state.
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


def test_negative_approval_request_missing_block_teaches_order():
    env = _valid_approval_request_envelope()
    env["approval_request"] = None

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.APPROVAL_REQUEST_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.APPROVAL_REQUEST_SHAPE]
    assert offending and offending[0].field == "approval_request"
    detail = offending[0].detail
    assert "order" in detail.lower()
    assert "approval_request" in detail
    assert "agent_state" in detail


def test_negative_approval_request_missing_exact_content_teaches_order():
    env = _valid_approval_request_envelope()
    env["approval_request"]["exact_content"] = ""

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.APPROVAL_REQUEST_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.APPROVAL_REQUEST_SHAPE]
    assert offending and offending[0].field == "approval_request.exact_content"
    detail = offending[0].detail
    assert "order" in detail.lower()
    assert "exact_content" in detail


# ---------------------------------------------------------------------------
# Regression guard: validation still rejects everything it rejected before --
# the message is richer, the verdict is unchanged. A well-formed envelope of
# every declared status still passes.
# ---------------------------------------------------------------------------
def test_positive_valid_complete_envelope_still_passes():
    result = validate_form(_valid_complete_envelope())

    assert result.ok is True
    assert result.errors == ()


def test_positive_valid_approval_request_still_passes():
    result = validate_form(_valid_approval_request_envelope())

    assert result.ok is True
    assert result.errors == ()


def test_negative_still_rejects_malformed_agent_id():
    """An unrelated defect (AGENT_ID_FORMAT) is untouched by this change --
    the order-aware detail only landed on the four cross-field codes."""
    env = _valid_envelope()
    env["agent_status"]["agent_id"] = "not-a-valid-id"

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.AGENT_ID_FORMAT]


def test_verification_shape_command_type_teaches_order():
    """VERIFICATION_SHAPE (opt-in, per-type) gets the same order-aware
    treatment: declaring verification.type before its required field."""
    env = _valid_envelope()
    env["evidence_report"]["verification"] = {"type": "command"}

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_SHAPE]
    assert offending and offending[0].field == "evidence_report.verification.command"
    detail = offending[0].detail
    assert "order" in detail.lower()
    assert "command" in detail
