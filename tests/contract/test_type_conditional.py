"""
R3 -- type-conditional verification shape validation (VERIFICATION_SHAPE).

The form layer validates ``evidence_report.verification`` conditionally BY TYPE,
mirroring the pre-existing conditional-BY-VALUE pattern (VERIFICATION_RESULT):
when ``verification.type`` declares a type, validate_form requires the evidence
that type demands and rejects an omission with the additive, distinct code
VERIFICATION_SHAPE.

  * "command"/"code" (deterministic) -> non-empty ``command`` (the oracle to run)
  * "semantic"                       -> truthy ``requires_human`` marker
  * "self_review"                    -> non-empty ``reviewed`` statement
  * "none"                           -> nothing (the declared absence of a check)
  * anything else                    -> at least ONE of the three above

The last line is what closes the enum escape: the demand keys on the PRESENCE
of a declared type, not on its membership in the enum, so inventing a word no
longer costs less than producing evidence. The vocabulary stays open -- see the
open-vocabulary rationale in gaia/contract/validator.py.

Backward compatible: an ABSENT or blank verification.type fires no requirement.

Test-naming contract (brief AC-1 / AC-4):
  * ``pytest -k type_conditional``       selects the NEGATIVE and POSITIVE cases.
  * ``pytest -k type_conditional_valid`` selects ONLY the POSITIVE cases (the
    negative cases deliberately omit the substring "valid").
"""

from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
    ENVELOPE_VERIFICATION_TYPES,
    VALID_VERIFICATION_TYPES,
    FormErrorCode,
    validate_form,
)
from gaia.contract.validator import _canonical_verification_type as _canonical
from gaia.state import VALID_VERIFICATION_TYPES as STATE_VERIFICATION_TYPES

import pytest


def _base_envelope() -> dict:
    """A shape-valid, non-COMPLETE (IN_PROGRESS) envelope -- the mutation base.

    IN_PROGRESS isolates the by-TYPE check from the by-VALUE COMPLETE/result
    check, so a fired VERIFICATION_SHAPE is provably the only invalidity.
    """
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


def _with_verification(verification: dict) -> dict:
    env = _base_envelope()
    env["evidence_report"]["verification"] = verification
    return env


def _canonicalized_type(raw: str) -> str:
    """The value that would actually be STORED for ``raw``.

    Goes through the public canonicalize_envelope rather than the helper, so
    the assertion covers the path a real write takes (validate, then
    canonicalize, then persist) and not just the folding function.
    """
    from gaia.contract.validator import canonicalize_envelope

    env = canonicalize_envelope(_with_verification({"type": raw}))
    return env["evidence_report"]["verification"]["type"]


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
    env["agent_status"]["agent_state"] = "COMPLETE"
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
# Backward compatibility: an ABSENT (or blank) type adds NO requirement. That
# is the boundary of the open-vocabulary rule -- it triggers on a declared
# claim, never on the absence of one.
# (Selected by -k type_conditional; deliberately no "valid".)
# ---------------------------------------------------------------------------
def test_type_conditional_absent_type_is_backward_compatible():
    # A verification block with NO 'type' key -- pre-R3 behaviour: no check.
    env = _with_verification({"method": "free text", "details": "whatever"})

    result = validate_form(env)

    assert result.ok is True
    assert result.errors == ()


def test_type_conditional_blank_type_fires_no_shape_check():
    """An empty/whitespace type is not a claim, so it demands no evidence."""
    env = _with_verification({"method": "x", "type": "   "})

    result = validate_form(env)

    assert result.ok is True
    assert result.errors == ()


# ---------------------------------------------------------------------------
# THE ENUM ESCAPE (closed). The requirement used to fire only for a type INSIDE
# the enum, so an invented word switched it off -- inventing a type was cheaper
# than producing evidence, and 127 of 522 typed rows in the live population sat
# outside the enum with 123 of them carrying no evidence at all. The demand now
# keys on the PRESENCE of a declared type, not on its membership.
# ---------------------------------------------------------------------------
def test_type_conditional_unknown_type_still_requires_evidence():
    env = _with_verification({"method": "x", "type": "totally-unknown-type"})

    result = validate_form(env)

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]
    offending = [e for e in result.errors if e.code == FormErrorCode.VERIFICATION_SHAPE]
    assert offending[0].field == "evidence_report.verification"


@pytest.mark.parametrize(
    "vtype",
    ["test", "manual", "observation", "oracle", "dry_run", "command_execution"],
)
def test_type_conditional_real_population_types_require_evidence(vtype):
    """The actual out-of-enum words measured in the live population.

    Parametrized on the real values rather than a synthetic placeholder: these
    are the types that were switching the requirement off, and each must now
    be held to it.
    """
    result = validate_form(_with_verification({"type": vtype}))

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]


@pytest.mark.parametrize("evidence_field,value", [
    ("command", "pytest tests/contract -q"),
    ("reviewed", "re-read the diff and confirmed the branch is additive"),
    ("requires_human", True),
])
def test_type_conditional_valid_unknown_type_with_evidence_passes(evidence_field, value):
    """The vocabulary stays OPEN: an agent's own word is accepted, priced.

    Each of the three evidence fields independently satisfies an out-of-enum
    type -- the demand is a disjunction, because the envelope cannot know which
    oracle a novel word names.
    """
    result = validate_form(_with_verification({"type": "observation", evidence_field: value}))

    assert result.ok is True, result.error_summary()
    assert result.errors == ()


def test_type_conditional_none_is_the_only_free_type():
    """The exemption exists at exactly ONE auditable spelling.

    This is what proves the discount was removed rather than relocated: the
    free option is the word that says out loud that no oracle was required,
    and no invented word reaches it.
    """
    assert validate_form(_with_verification({"type": "none"})).ok is True
    assert validate_form(_with_verification({"type": "no-oracle-needed"})).ok is False


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
    env["agent_status"]["agent_state"] = "COMPLETE"
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


# ---------------------------------------------------------------------------
# CANONICALIZATION -- family 1: pure orthography, folded without loss.
#
# The live population spells one concept two ways (self-review/self_review,
# dry-run/dry_run, command-execution/command_execution). Separators fold;
# words never do. The two halves must agree, so both the value validate_form
# JUDGED and the value canonicalize_envelope STORES are asserted here.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hyphen,underscore", [
    ("self-review", "self_review"),
    ("dry-run", "dry_run"),
    ("command-execution", "command_execution"),
    ("read-only-query", "read_only_query"),
])
def test_type_conditional_spelling_variants_converge(hyphen, underscore):
    assert _canonical(hyphen) == _canonical(underscore) == underscore

    stored_hyphen = _canonicalized_type(hyphen)
    stored_underscore = _canonicalized_type(underscore)

    assert stored_hyphen == stored_underscore == underscore


def test_type_conditional_hyphen_spelling_inherits_the_known_type_demand():
    """'self-review' folds onto the KNOWN type and owes ITS field, not the
    weaker one-of-three an unknown type owes.

    Without the fold this spelling would be out-of-enum and satisfiable by a
    'command' -- the enum escape surviving in miniature, at one spelling.
    """
    missing = validate_form(_with_verification({"type": "self-review"}))
    assert missing.ok is False
    offending = [e for e in missing.errors if e.code == FormErrorCode.VERIFICATION_SHAPE]
    assert offending[0].field == "evidence_report.verification.reviewed"

    wrong_evidence = validate_form(
        _with_verification({"type": "self-review", "command": "pytest -q"})
    )
    assert wrong_evidence.ok is False

    satisfied = validate_form(
        _with_verification({"type": "self-review", "reviewed": "re-read the diff"})
    )
    assert satisfied.ok is True, satisfied.error_summary()


@pytest.mark.parametrize("vtype", ["test", "manual", "observation", "oracle"])
def test_type_conditional_semantic_types_are_never_remapped(vtype):
    """Family 2: genuinely ambiguous words survive VERBATIM.

    Mapping 'observation' or 'oracle' onto a known type would be inventing a
    claim the agent never made. Canonicalization touches separators only, so
    these come back as themselves -- and land in no enum member.
    """
    stored = _canonicalized_type(vtype)

    assert stored == vtype
    assert stored not in ENVELOPE_VERIFICATION_TYPES


def test_type_conditional_canonicalization_reports_the_substitution():
    """A rewrite is announced, never silent -- the caller stores a value that
    differs from the one it wrote."""
    env = _with_verification({"type": "Dry-Run", "command": "terraform plan"})
    changes: list = []

    from gaia.contract.validator import canonicalize_envelope

    result = canonicalize_envelope(env, changes=changes)

    assert result["evidence_report"]["verification"]["type"] == "dry_run"
    assert any("evidence_report.verification.type" in line for line in changes)
    assert env["evidence_report"]["verification"]["type"] == "Dry-Run"


# ---------------------------------------------------------------------------
# NOT A CELL -- the rejection has a handle on the inside.
#
# Every write validates the WHOLE envelope, so a new rejection that could
# invalidate an already-existing state would also reject the write that fixes
# it. This file has been bitten by exactly that (see the sanitization note in
# validator.py). The escape here is structural rather than a repair pass: the
# remedy is an ADDITION, and the mutating verbs deep-merge into the existing
# verification object -- so the corrective write produces a merged envelope
# that validates.
# ---------------------------------------------------------------------------
def _deep_merge(base: dict, patch: dict) -> dict:
    """The CLI's own merge semantics (bin/cli/contract.py::_deep_merge)."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


@pytest.mark.parametrize("patch", [
    {"evidence_report": {"verification": {"command": "pytest -q"}}},
    {"evidence_report": {"verification": {"reviewed": "checked the output by hand"}}},
    {"evidence_report": {"verification": {"requires_human": True}}},
    {"evidence_report": {"verification": {"type": "none"}}},
])
def test_type_conditional_rejected_state_is_escapable_in_one_write(patch):
    """An inherited draft holding a bare out-of-enum type recovers in ONE write.

    Each patch is a real `gaia contract fill --json` payload. If any of these
    stayed rejected after merging, the constraint would be a cell rather than a
    validation.
    """
    stuck = _with_verification({"method": "x", "type": "manual"})
    assert validate_form(stuck).ok is False

    recovered = _deep_merge(stuck, patch)

    assert validate_form(recovered).ok is True, validate_form(recovered).error_summary()


@pytest.mark.parametrize("guidance", [
    # QUOTED spellings, not the bare words. The detail also lists the enum
    # ("is not one of command, code, semantic, self_review, none"), so a bare
    # `"command" in detail` passes even with the guidance deleted -- an
    # assertion that cannot fail. Each token below occurs ONLY in the guidance.
    "'command'",
    "'reviewed'",
    "'requires_human'",
    "verification.type 'none'",
])
def test_type_conditional_rejection_names_every_way_out(guidance):
    """The rejection is only escapable if it SAYS how to escape.

    A handle nobody can find is the same defect as no handle, so the detail
    must name all four exits the test above proves reachable.
    """
    result = validate_form(_with_verification({"type": "oracle"}))
    detail = result.errors[0].detail

    assert guidance in detail, f"the rejection never offers {guidance}"
