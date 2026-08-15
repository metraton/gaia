"""The open-vocabulary verification rule must be REACHABLE by whoever copies the example.

The rule in ``_verification_type_shape_error`` keys on ``verification.type``:
declaring a type is a claim that a check ran, and the claim is priced in a
companion field. A rule keyed on a field nobody is ever taught to write is
inert -- and that is exactly the state this file exists to prevent from
returning. The canonical block printed in EVERY rejection, the ``fill --json``
payload embedded in the VERIFICATION_RESULT detail, and the worked examples in
the skills all used to teach ``verification.method``, a key no code reads. An
agent following them declared no type, so it owed no evidence, so the rule
never fired for the population it was written for.

What is asserted here is the PROPERTY, not the wording: take the example as
printed, and (1) it declares a type, (2) that type is load-bearing -- remove
the evidence and the same envelope is rejected, (3) the example as printed is
itself accepted, so copying it is not a trap, and (4) the rejection it can
produce is escapable in one write.

``method`` is deliberately NOT retired. It is the free-prose "how you checked"
slot the live population actually uses, and it stays taught alongside ``type``
-- the classifier the validator reads. The backward-compatibility half of this
file pins that: a block carrying prose in ``method`` and no ``type`` at all is
still valid, exactly as it was before the example was fixed.
"""

import copy
import json
import re
from pathlib import Path

import pytest

from gaia.contract.validator import (
    CANONICAL_REPAIR_MESSAGE,
    ROW_ENVELOPE_REPAIR_MESSAGE,
    FormErrorCode,
    validate_form,
)
from gaia.contract.validator import _canonical_verification_type as _canonical

_REPO_ROOT = Path(__file__).resolve().parents[2]

# The three fields any declared type can be priced in. Stripping all three is
# how a test proves the declared type is load-bearing rather than decorative.
_EVIDENCE_FIELDS = ("command", "reviewed", "requires_human")

# Every doc surface that teaches a verification block by example. A JSON
# payload here is copied verbatim by agents, so each is held to the same
# reachability property as the CLI's own canonical block.
_EXAMPLE_DOCS = (
    "skills/agent-protocol/examples.md",
)


def _base_envelope() -> dict:
    """A shape-valid IN_PROGRESS envelope carrying no verification block.

    IN_PROGRESS isolates the by-TYPE shape check from the by-VALUE
    COMPLETE/result check, so a fired VERIFICATION_SHAPE is provably the only
    invalidity.
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


def _fenced_json(message: str) -> dict:
    """Parse the ```json shape block the repair message prints.

    Parsing rather than substring-matching is the point: the block is
    advertised as valid JSON an agent can copy into `gaia contract fill --json`,
    so a test that reads it the way the agent does also proves that
    advertisement.
    """
    match = re.search(r"```json\n(.*?)```", message, re.DOTALL)
    assert match, "the repair message no longer carries a fenced envelope block"
    return json.loads(match.group(1))


def _canonical_example_verification() -> dict:
    verification = _fenced_json(CANONICAL_REPAIR_MESSAGE)["evidence_report"]["verification"]
    assert isinstance(verification, dict)
    return verification


def _stripped_of_evidence(verification: dict) -> dict:
    stripped = copy.deepcopy(verification)
    for field in _EVIDENCE_FIELDS:
        stripped.pop(field, None)
    return stripped


def _deep_merge(base: dict, patch: dict) -> dict:
    """The CLI's own merge semantics (bin/cli/contract.py::_deep_merge)."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _json_payloads(relative_path: str) -> list:
    """Every JSON payload a reader of this doc could copy.

    Two shapes carry envelopes in the skills: a fenced ```json block, and an
    inline ``--json '<payload>'`` argument in a shell block. Both are copied
    verbatim by agents, so both are collected.
    """
    text = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
    payloads = []
    for opener in re.finditer(r"```json\n|--json '", text):
        # raw_decode, not a regex for the closing delimiter: one worked example
        # embeds a fenced code block inside a JSON string, and another nests
        # braces inside the --json argument, so any non-greedy terminator cuts
        # the document short. Decoding from the opening brace consumes exactly
        # one document and ignores whatever follows it.
        rest = text[opener.end():]
        if not rest.lstrip().startswith("{"):
            continue
        start = opener.end() + (len(rest) - len(rest.lstrip()))
        payload, _ = json.JSONDecoder().raw_decode(text, start)
        payloads.append(payload)
    return payloads


def _verification_blocks(relative_path: str) -> list:
    """The ``evidence_report.verification`` objects a doc teaches.

    ``approval_request.verification`` is a different field (free prose naming
    how a granted command would be checked) and is deliberately not collected.
    """
    blocks = []
    for payload in _json_payloads(relative_path):
        evidence = payload.get("evidence_report")
        if not isinstance(evidence, dict):
            continue
        verification = evidence.get("verification")
        if isinstance(verification, dict):
            blocks.append(verification)
    return blocks


# ---------------------------------------------------------------------------
# THE CENTRAL PROPERTY: the example makes the rule reachable.
# ---------------------------------------------------------------------------
def test_canonical_example_declares_a_verification_type():
    """A copier of the canonical block declares a type -- the rule's trigger.

    Without this the rule is inert by construction: no type declared, no
    companion field owed, no check ever fired.
    """
    verification = _canonical_example_verification()

    assert _canonical(verification.get("type")) != "", (
        "the canonical repair block teaches no verification.type, so an agent "
        "copying it declares none and the type-conditional rule never fires"
    )


def test_canonical_example_type_is_not_the_one_exempt_spelling():
    """'none' is the single type that owes nothing.

    Teaching it in the canonical block would satisfy the assertion above while
    leaving every copier exempt -- the inert state, relocated rather than
    removed.
    """
    assert _canonical(_canonical_example_verification().get("type")) not in ("", "none")


def test_canonical_example_copier_owes_its_evidence():
    """The declared type is load-bearing, not decoration.

    Strip the evidence fields from the block as printed and the SAME envelope
    must be rejected. If it still validated, the example would declare a type
    that costs nothing.
    """
    verification = _canonical_example_verification()

    result = validate_form(_with_verification(_stripped_of_evidence(verification)))

    assert result.ok is False
    assert result.codes == [FormErrorCode.VERIFICATION_SHAPE]


def test_canonical_example_is_accepted_as_printed():
    """Copying the example verbatim must not be a trap.

    The block is what an agent reproduces under a rejection it is already
    trying to escape; if the example itself failed the check it teaches, the
    rejection would have no exit.
    """
    result = validate_form(_with_verification(_canonical_example_verification()))

    assert result.ok is True, result.error_summary()


def test_canonical_example_still_teaches_the_free_prose_field():
    """``method`` keeps its slot: 278 of 400 persisted rows carry it, and its
    modal content is a multi-clause description of how the check was run.
    Dropping it from the example would deprecate the store's richest
    descriptive field on the way to fixing the classifier."""
    assert "method" in _canonical_example_verification()


def test_both_repair_messages_carry_the_same_example():
    """The row-envelope repair shares ``_REPAIR_MESSAGE_BODY``.

    Fixing one message and not the other would leave half the rejections
    teaching the inert form.
    """
    assert _fenced_json(ROW_ENVELOPE_REPAIR_MESSAGE) == _fenced_json(CANONICAL_REPAIR_MESSAGE)


# ---------------------------------------------------------------------------
# The other example the CLI prints: the fill --json payload embedded in the
# VERIFICATION_RESULT detail, which is what a COMPLETE-too-early turn reads.
# ---------------------------------------------------------------------------
def _verification_result_detail() -> str:
    env = _base_envelope()
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    result = validate_form(env)

    details = [
        error.detail
        for error in result.errors
        if error.code == FormErrorCode.VERIFICATION_RESULT
    ]
    assert details, "a COMPLETE with no verification block no longer rejects"
    return details[0]


def _verification_result_example() -> dict:
    detail = _verification_result_detail()
    match = re.search(r"--json '(\{.*\})'", detail)
    assert match, "the VERIFICATION_RESULT detail no longer embeds a fill payload"
    return json.loads(match.group(1))["evidence_report"]["verification"]


def test_verification_result_detail_example_declares_a_type():
    assert _canonical(_verification_result_example().get("type")) not in ("", "none")


def test_verification_result_detail_example_owes_its_evidence():
    verification = _verification_result_example()

    accepted = validate_form(_with_verification(verification))
    rejected = validate_form(_with_verification(_stripped_of_evidence(verification)))

    assert accepted.ok is True, accepted.error_summary()
    assert rejected.codes == [FormErrorCode.VERIFICATION_SHAPE]


def test_verification_result_detail_example_completes_the_envelope_it_repairs():
    """The payload is offered as the fix for a COMPLETE that has no
    verification. Merged into that exact envelope, it must actually clear the
    rejection -- otherwise the repair instruction sends the agent into a second
    one."""
    env = _base_envelope()
    env["agent_status"]["agent_state"] = "COMPLETE"
    env["agent_status"]["next_action"] = "done"
    assert validate_form(env).ok is False

    repaired = _deep_merge(
        env, {"evidence_report": {"verification": _verification_result_example()}}
    )

    assert validate_form(repaired).ok is True, validate_form(repaired).error_summary()


# ---------------------------------------------------------------------------
# BACKWARD COMPATIBILITY: nothing that validates today stops validating.
# ---------------------------------------------------------------------------
def test_prose_only_method_block_stays_valid():
    """The live-population shape: prose in ``method``, no ``type`` at all.

    Verbatim from a persisted row. Making the example teach ``type`` must not
    turn the rows that never did into rejections -- the trigger stays the
    PRESENCE of a declared type.
    """
    env = _with_verification({
        "method": (
            "terragrunt plan -lock=false contra estado real de GCP para "
            "dns-dev/dns-demo/dns-staging; git log/show sobre account.hcl en la "
            "rama y en origin/main para establecer orden causal"
        ),
        "result": "pass",
        "details": "plan limpio en los tres entornos",
    })

    result = validate_form(env)

    assert result.ok is True, result.error_summary()
    assert result.errors == ()


def test_prose_method_alongside_a_declared_type_stays_valid():
    """The two fields coexist, each in its role -- what the example now shows."""
    result = validate_form(_with_verification({
        "type": "command",
        "command": "pytest tests/contract -q",
        "method": "ran the affected subset and read the summary line",
        "result": "pass",
        "details": "123 passed",
    }))

    assert result.ok is True, result.error_summary()


# ---------------------------------------------------------------------------
# NOT A CELL: the rejection the example can produce has a handle inside.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("patch", [
    # Pay what the declared type asks for.
    {"evidence_report": {"verification": {"command": "pytest -q"}}},
    # Or re-declare the type as what the turn actually did, with ITS evidence.
    {"evidence_report": {"verification": {
        "type": "self_review", "reviewed": "re-read the diff line by line",
    }}},
    # Or say out loud that no oracle was required.
    {"evidence_report": {"verification": {"type": "none"}}},
])
def test_copier_who_omits_the_evidence_recovers_in_one_write(patch):
    """An agent that copies the type and forgets the companion field is one
    ``gaia contract fill --json`` away from valid. Every mutating verb
    deep-merges, so the corrective write is an ADDITION and lands.

    A KNOWN type is priced in ONE specific field, so its exits are narrower
    than the one-of-three an unknown word may pay: supply that field, or
    re-declare the type. Both are a single write, which is what keeps the
    rejection a validation rather than a cell.
    """
    stuck = _with_verification(_stripped_of_evidence(_canonical_example_verification()))
    assert validate_form(stuck).ok is False

    recovered = _deep_merge(stuck, patch)

    assert validate_form(recovered).ok is True, validate_form(recovered).error_summary()


# ---------------------------------------------------------------------------
# The skills teach the same reachable block the CLI prints.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("doc", _EXAMPLE_DOCS)
def test_skill_examples_declare_a_verification_type(doc):
    blocks = _verification_blocks(doc)

    assert blocks, f"{doc} teaches no verification block -- the guard would be vacuous"
    for verification in blocks:
        assert _canonical(verification.get("type")) != "", (
            f"{doc} teaches a verification block with no type: {verification!r}"
        )


@pytest.mark.parametrize("doc", _EXAMPLE_DOCS)
def test_skill_examples_pay_for_the_type_they_declare(doc):
    """Each worked example must itself satisfy the rule it demonstrates.

    An example that declares a type without its companion field would be
    rejected the moment an agent copied it.
    """
    for verification in _verification_blocks(doc):
        result = validate_form(_with_verification(verification))

        assert FormErrorCode.VERIFICATION_SHAPE not in result.codes, (
            f"{doc} teaches an unpayable verification block: {verification!r} "
            f"-> {result.error_summary()}"
        )


@pytest.mark.parametrize("doc", _EXAMPLE_DOCS)
def test_skill_examples_that_declare_a_type_are_load_bearing(doc):
    """Strip the evidence and every taught block must reject.

    Without this, an example could satisfy the type check through a type that
    demands nothing and the guard above would pass on a block teaching the
    inert form again.
    """
    for verification in _verification_blocks(doc):
        result = validate_form(_with_verification(_stripped_of_evidence(verification)))

        assert result.codes == [FormErrorCode.VERIFICATION_SHAPE], (
            f"{doc} teaches a verification block whose type costs nothing: "
            f"{verification!r}"
        )
