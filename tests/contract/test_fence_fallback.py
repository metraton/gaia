"""
AC-13 -- M6 fence fallback: a legacy fenced ``agent_contract_handoff`` block
validates through the SAME core validator.

The regex/fence parser (``hooks.modules.agents.contract_validator.parse_contract``)
degrades to a migration-only fallback: it extracts a dict out of raw fenced
text -- work the portable core (``gaia.contract.validator.validate_form``)
deliberately does not do, since the core takes an already-parsed dict, never
raw text (T1 carry-forward). Once extracted, the dict's SHAPE must be
validated by the exact same core the CLI (M2) and the hook gate (M4) use --
NOT by a second, independently re-implemented shape check.

This suite proves three distinct things, each insufficient alone:
    1. parse_contract() only EXTRACTS (never independently decides validity).
    2. The legacy validate() entry point ROUTES its shape decision through
       gaia.contract.validator.validate_form -- proven by actually observing
       the SSOT function get called (patch-and-spy), not merely by matching
       output shape.
    3. The two entry points -- validate_form() called directly on the parsed
       dict, and contract_validator.validate() called on the raw fenced text
       -- agree on ok/not-ok for the same envelope, because they are the same
       decision, reached once.
"""

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[1].parent / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from modules.agents import contract_validator  # noqa: E402
from modules.agents.contract_validator import parse_contract, validate  # noqa: E402

from gaia.contract.validator import (  # noqa: E402
    CANONICAL_REPAIR_MESSAGE,
    FormErrorCode,
    validate_form,
)


def _fence(body: str, tag: str = "agent_contract_handoff") -> str:
    return f"Some prose.\n\n```{tag}\n{body}\n```\n"


def _valid_complete_body() -> str:
    return """{
  "agent_status": {
    "plan_status": "COMPLETE",
    "agent_id": "a1b2c3",
    "pending_steps": [],
    "next_action": "done"
  },
  "evidence_report": {
    "patterns_checked": [], "files_checked": [], "commands_run": [],
    "key_outputs": [], "verbatim_outputs": [], "cross_layer_impacts": [],
    "open_gaps": [],
    "verification": { "method": "pytest", "result": "pass", "details": "green" }
  },
  "consolidation_report": null,
  "approval_request": null
}"""


# ---------------------------------------------------------------------------
# 1. parse_contract() is EXTRACTION ONLY -- it never decides validity itself.
#    A malformed agent_id (fails the core's AGENT_ID_FORMAT rule) still
#    extracts cleanly; only the downstream validate() call rejects it.
# ---------------------------------------------------------------------------
def test_parse_contract_extracts_even_a_shape_invalid_envelope():
    body = _valid_complete_body().replace('"agent_id": "a1b2c3"', '"agent_id": "not-a-valid-id"')
    output = _fence(body)

    parsed = parse_contract(output)

    assert parsed is not None
    assert parsed["agent_status"]["agent_id"] == "not-a-valid-id"
    # parse_contract does not reject -- it only extracts. The core is the one
    # that will find this invalid.
    form_result = validate_form(parsed)
    assert form_result.ok is False
    assert form_result.codes == [FormErrorCode.AGENT_ID_FORMAT]


# ---------------------------------------------------------------------------
# 2. contract_validator.validate() ROUTES through gaia.contract.validator.
#    validate_form, proven by spying on the actual call -- not by inferring
#    it from matching output (which a coincidentally-identical duplicate
#    implementation could also produce).
# ---------------------------------------------------------------------------
def test_legacy_validate_calls_the_ssot_validate_form(monkeypatch):
    calls = []
    real_validate_form = contract_validator.validate_form

    def _spy(envelope):
        calls.append(envelope)
        return real_validate_form(envelope)

    monkeypatch.setattr(contract_validator, "validate_form", _spy)

    output = _fence(_valid_complete_body())
    result = validate(output, {})

    assert result.is_valid
    assert len(calls) == 1, "validate() must call the SSOT validate_form exactly once"
    assert calls[0]["agent_status"]["plan_status"] == "COMPLETE"


def test_legacy_validate_verdict_is_entirely_driven_by_the_core(monkeypatch):
    """When the core (stubbed) says invalid, the legacy fence path must say
    invalid too -- proving the fence path has no independent shape opinion
    that could disagree with the core."""

    # Build a stub FormValidationResult using the real dataclass shape so the
    # rest of _validate_from_handoff (which reads .errors/.error_summary())
    # keeps working -- only the VERDICT is forced to invalid.
    from gaia.contract.validator import FormError, FormValidationResult

    def _stub_validate_form(envelope):
        return FormValidationResult(
            ok=False,
            errors=(FormError(code=FormErrorCode.PLAN_STATUS, field="agent_status.plan_status", detail="stubbed"),),
            repair_message=CANONICAL_REPAIR_MESSAGE,
        )

    monkeypatch.setattr(contract_validator, "validate_form", _stub_validate_form)

    # A structurally perfect envelope -- would pass under the OLD, locally
    # re-implemented shape check. It must now fail, because the fence path
    # has no shape decision of its own; it defers entirely to the core.
    output = _fence(_valid_complete_body())
    result = validate(output, {})

    assert not result.is_valid
    assert "PLAN_STATUS" in result.missing


# ---------------------------------------------------------------------------
# 3. Direct validate_form() on the extracted dict and contract_validator.
#    validate() on the raw fenced text AGREE -- same decision, reached once.
#    Covers the AC-1 four named codes surfacing through the fence path.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutate,expected_code",
    [
        (lambda b: b.replace('"agent_id": "a1b2c3"', '"agent_id": "nope"'), FormErrorCode.AGENT_ID_FORMAT),
        (lambda b: b.replace('"plan_status": "COMPLETE"', '"plan_status": "BOGUS"'), FormErrorCode.PLAN_STATUS),
        (lambda b: b.replace('"result": "pass"', '"result": "fail"'), FormErrorCode.VERIFICATION_RESULT),
        (lambda b: b.replace('"files_checked": [], "commands_run": [],', '"files_checked": [],'), FormErrorCode.MISSING_FIELD),
    ],
)
def test_fence_path_and_direct_core_call_agree(mutate, expected_code):
    body = mutate(_valid_complete_body())
    output = _fence(body)

    # Path A: the SAME core, called directly on the extracted dict.
    direct = validate_form(parse_contract(output))

    # Path B: the legacy fence entry point.
    legacy = validate(output, {})

    assert direct.ok is False
    assert expected_code in direct.codes
    assert legacy.is_valid is False, f"Expected fence path to reject too. Missing: {legacy.missing}"


def test_fence_path_positive_agrees_with_core():
    output = _fence(_valid_complete_body())

    direct = validate_form(parse_contract(output))
    legacy = validate(output, {})

    assert direct.ok is True
    assert legacy.is_valid is True
    assert legacy.missing == []


# ---------------------------------------------------------------------------
# The repair guidance is the core's canonical message -- not a second,
# independently maintained copy of the same text.
# ---------------------------------------------------------------------------
def test_fence_path_repair_message_reuses_canonical_repair_message():
    body = _valid_complete_body().replace('"agent_id": "a1b2c3"', '"agent_id": "nope"')
    output = _fence(body)

    result = validate(output, {})

    assert not result.is_valid
    assert CANONICAL_REPAIR_MESSAGE in result.error_message


# ---------------------------------------------------------------------------
# The ```json``` tolerant-fence fallback (a pre-existing migration aid) also
# shares the same core -- the fence LABEL is tolerated, the shape decision is
# not relaxed or re-implemented for it.
# ---------------------------------------------------------------------------
def test_json_fence_fallback_also_routes_through_the_core():
    body = _valid_complete_body().replace('"agent_id": "a1b2c3"', '"agent_id": "nope"')
    output = _fence(body, tag="json")

    result = validate(output, {})

    assert not result.is_valid
    assert "AGENT_ID" in result.missing
    assert CANONICAL_REPAIR_MESSAGE in result.error_message


# ---------------------------------------------------------------------------
# No parseable fence at all funnels through the same call, not a separate
# hand-written "everything missing" branch.
# ---------------------------------------------------------------------------
def test_no_fence_at_all_still_routes_through_validate_form(monkeypatch):
    calls = []
    real_validate_form = contract_validator.validate_form

    def _spy(envelope):
        calls.append(envelope)
        return real_validate_form(envelope)

    monkeypatch.setattr(contract_validator, "validate_form", _spy)

    result = validate("no contract block here at all", {})

    assert not result.is_valid
    assert len(calls) == 1
    assert calls[0] is None
