"""
Gate 34 (plan 34 / brief 114, task 4): the TURN-status envelope field was
renamed ``plan_status`` -> ``agent_state``.

This test pins the rename at the SSOT core (``gaia.contract.validator``): the
canonical name of the turn-status field in the ``agent_contract_handoff``
envelope and in the handoff schema is now ``agent_state``; the former
``plan_status`` envelope key is no longer accepted as the turn field.

Deliberately UNCHANGED by the rename and asserted here so the boundary is
explicit:
  * the error CODE ``FormErrorCode.PLAN_STATUS`` keeps its name (stable
    public-surface identifier), even though the field it now guards is
    ``agent_status.agent_state``;
  * the canonical enum values are unchanged.

NOTE: this file intentionally references the string literal "plan_status" in
its negative assertions -- it is the OLD key whose absence proves the rename.
Keep it out of any blanket plan_status -> agent_state sweep.
"""

# OLD_KEY / NEW_KEY are spelled once, here, so the negative assertions cannot
# be silently rewritten by a mechanical rename sweep.
OLD_KEY = "plan" + "_status"
NEW_KEY = "agent_state"

from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
    FormErrorCode,
    REQUIRED_AGENT_STATUS_FIELDS,
    VALID_PLAN_STATUSES,
    validate_form,
)

_EVIDENCE_KEYS = (
    "patterns_checked",
    "files_checked",
    "commands_run",
    "key_outputs",
    "verbatim_outputs",
    "cross_layer_impacts",
    "open_gaps",
)


def _valid_envelope(state: str = "IN_PROGRESS") -> dict:
    """A shape-valid, non-COMPLETE envelope using the NEW ``agent_state`` key."""
    return {
        "agent_status": {
            NEW_KEY: state,
            "agent_id": "a1b2c3",
            "pending_steps": [],
            "next_action": "continue",
        },
        "evidence_report": {k: [] for k in _EVIDENCE_KEYS},
        "consolidation_report": None,
        "approval_request": None,
    }


def test_agent_state_is_the_required_turn_field():
    """The turn field is named agent_state; plan_status is no longer required."""
    assert NEW_KEY in REQUIRED_AGENT_STATUS_FIELDS
    assert OLD_KEY not in REQUIRED_AGENT_STATUS_FIELDS


def test_valid_envelope_with_agent_state_passes():
    assert validate_form(_valid_envelope()).ok


def test_legacy_plan_status_key_is_rejected_as_missing_agent_state():
    """An envelope that still uses the OLD plan_status key is now rejected:
    the validator reads agent_state and reports it missing."""
    env = _valid_envelope()
    env["agent_status"][OLD_KEY] = env["agent_status"].pop(NEW_KEY)
    result = validate_form(env)
    assert not result.ok
    assert FormErrorCode.MISSING_FIELD in result.codes
    assert any(e.field == "agent_status.agent_state" for e in result.errors)


def test_repair_message_uses_agent_state_key_not_plan_status():
    """The handoff schema shown in the canonical repair message uses the new
    field name and never the old envelope key."""
    assert f'"{NEW_KEY}":' in CANONICAL_REPAIR_MESSAGE
    assert f'"{OLD_KEY}":' not in CANONICAL_REPAIR_MESSAGE


def test_out_of_enum_agent_state_still_fires_the_PLAN_STATUS_code():
    """The error code name PLAN_STATUS is preserved (stable public surface)
    even though it now guards the agent_state field."""
    result = validate_form(_valid_envelope("NOT_A_REAL_STATUS"))
    assert not result.ok
    assert FormErrorCode.PLAN_STATUS in result.codes
    assert any(e.field == "agent_status.agent_state" for e in result.errors)


def test_enum_values_unchanged_by_the_rename():
    for value in ("IN_PROGRESS", "APPROVAL_REQUEST", "COMPLETE",
                  "BLOCKED", "NEEDS_INPUT", "NEEDS_VERIFICATION"):
        assert value in VALID_PLAN_STATUSES
