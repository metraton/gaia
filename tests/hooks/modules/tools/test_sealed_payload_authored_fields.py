#!/usr/bin/env python3
"""The intercept-time sealed payload authors impact, rollback and verification.

Two properties are under test, and they pull in opposite directions on purpose.

AUTHORED CONTENT: for a verdict the mapping covers, the three fields the payload
producer used to leave undeclared now carry a statement the presented surface
shows, and none of them resolves to the declared-absence text the presentation
layer substitutes for a missing field. For a verdict the mapping does not cover,
the SAME producer seals nothing and the absence text survives -- the uncovered
branch is reachable, and a claim is not manufactured to fill the slot.

FINGERPRINT NON-INTERFERENCE: neither fingerprint that binds a decision to an
execution may move because a descriptive field was authored. The reference for
that is INDEPENDENT: the expected digests are written into this file as literals
and never re-derived from the enriched payload by the module that produced it,
because a value compared against itself proves only self-agreement. The
fail-closed direction is asserted in the same run, so this file cannot be
satisfied by weakening the match it exists to protect.

Every payload under assertion is built by calling the real production producer,
``bash_validator._build_sealed_payload``, with real classifier inputs. A dict
literal standing in for a payload would assert over a shape production never
emits.
"""

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.tools.bash_validator import (  # noqa: E402
    _authored_statements,
    _build_sealed_payload,
)
from adapters.consent_presentation import (  # noqa: E402
    VISIBLE_FIELDS,
    envelope_from_sealed_payload,
    native_presentation,
    sealed_field,
)
from adapters.types import ConsentBinding, ConsentRequestEnvelope  # noqa: E402
from gaia.approvals.chain import fingerprint_payload  # noqa: E402
from gaia.approvals.command_set import (  # noqa: E402
    command_fingerprint,
    request_fingerprint,
)

# Independent reference values. SHA-256 of the command text itself and of the
# ordered command list, written here as literals so the comparison is against a
# constant this run cannot influence.
SINGLE_COMMAND = "git push origin main"
SINGLE_COMMAND_SHA256 = (
    "16f880284c51ff513ff5465f0082c75d9c7ebb186e65e98b4fa362534044846a"
)
ORDERED_COMMANDS = ["git add -A", "git push origin main"]
ORDERED_REQUEST_SHA256 = (
    "54411fd3c8c4b0d5410c1f1da6a65bcfedb09123b61bdddf564fa525192de783"
)

AUTHORED_FIELDS = ("impact", "rollback", "verification")

BINDING = ConsentBinding(agent_id="developer", session_id="S-test", call_id="C-test")


def _absence_text(field: str) -> str:
    """The text the presentation layer shows when no producer declared a field."""
    for name, _label, _keys, absent in VISIBLE_FIELDS:
        if name == field:
            return absent
    raise AssertionError(f"{field} is not a visible consent field")


def _surface(payload: dict) -> str:
    """The user-visible consent surface for a payload, through the real producer."""
    envelope = envelope_from_sealed_payload(
        payload, approval_id="P-test", binding=BINDING
    )
    return native_presentation(envelope, payload)["visible_text"]


# ---------------------------------------------------------------------------
# Authored content: three distinct verdicts, one of them a destructive category
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "verb,category",
    [
        ("push", "MUTATIVE"),
        ("apply", "MUTATIVE"),
        ("delete", "DESTRUCTIVE"),
    ],
)
def test_covered_verdict_seals_all_three_fields(verb, category):
    payload = _build_sealed_payload(
        command=SINGLE_COMMAND,
        verb=verb,
        category=category,
        agent_type="developer",
    )
    for field in AUTHORED_FIELDS:
        value = sealed_field(payload, field)
        assert value.strip(), f"{field} is empty for verdict {verb}/{category}"
        assert value != _absence_text(field), (
            f"{field} resolved to the declared-absence text for a covered verdict"
        )


def test_destructive_category_verdict_is_covered_and_scored_high():
    payload = _build_sealed_payload(
        command="terraform destroy",
        verb="a-verb-no-table-covers",
        category="DESTRUCTIVE",
        agent_type="platform-architect",
    )
    assert payload["risk_level"] == "high"
    for field in AUTHORED_FIELDS:
        assert sealed_field(payload, field) != _absence_text(field)


def test_covered_verdict_surface_shows_the_authored_text_and_no_absence_text():
    payload = _build_sealed_payload(
        command=SINGLE_COMMAND,
        verb="push",
        category="MUTATIVE",
        agent_type="developer",
    )
    surface = _surface(payload)
    for field in AUTHORED_FIELDS:
        assert sealed_field(payload, field) in surface
        assert _absence_text(field) not in surface


# ---------------------------------------------------------------------------
# Honest degradation: the uncovered branch is reachable and stays empty
# ---------------------------------------------------------------------------

def test_uncovered_verdict_selects_no_statement_at_all():
    assert _authored_statements("chmod", "MUTATIVE") == {}


def test_uncovered_verdict_seals_nothing_and_keeps_the_absence_text():
    payload = _build_sealed_payload(
        command="chmod 600 /tmp/probe.txt",
        verb="chmod",
        category="MUTATIVE",
        agent_type="developer",
    )
    assert payload["impact"] is None
    assert payload["rollback_hint"] is None
    assert payload["verification"] is None
    surface = _surface(payload)
    for field in AUTHORED_FIELDS:
        assert sealed_field(payload, field) == _absence_text(field)
        assert _absence_text(field) in surface


# ---------------------------------------------------------------------------
# Fingerprint non-interference, against the independent reference
# ---------------------------------------------------------------------------

def test_command_fingerprint_matches_the_literal_digest_for_both_verdicts():
    covered = _build_sealed_payload(
        command=SINGLE_COMMAND, verb="push", category="MUTATIVE"
    )
    uncovered = _build_sealed_payload(
        command=SINGLE_COMMAND, verb="a-verb-no-table-covers", category="MUTATIVE"
    )
    assert covered["exact_content"] == SINGLE_COMMAND
    assert uncovered["exact_content"] == SINGLE_COMMAND
    assert command_fingerprint(covered["exact_content"]) == SINGLE_COMMAND_SHA256
    assert command_fingerprint(uncovered["exact_content"]) == SINGLE_COMMAND_SHA256


def test_request_fingerprint_over_the_ordered_set_matches_the_literal_digest():
    payload = _build_sealed_payload(
        command=ORDERED_COMMANDS[0],
        verb="push",
        category="MUTATIVE",
        agent_type="developer",
        command_set=[{"command": c, "rationale": ""} for c in ORDERED_COMMANDS],
    )
    assert payload["commands"] == ORDERED_COMMANDS
    assert request_fingerprint(payload["commands"]) == ORDERED_REQUEST_SHA256
    assert sealed_field(payload, "rollback") != _absence_text("rollback")


def test_altered_command_text_is_still_rejected_by_the_fingerprint_match():
    payload = _build_sealed_payload(
        command=SINGLE_COMMAND, verb="push", category="MUTATIVE"
    )
    sealed = envelope_from_sealed_payload(
        payload, approval_id="P-test", binding=BINDING
    )
    assert sealed.fingerprints == (SINGLE_COMMAND_SHA256,)
    with pytest.raises(ValueError, match="fingerprints do not match"):
        ConsentRequestEnvelope(
            correlation_id=sealed.correlation_id,
            operation=sealed.operation,
            commands=("git push origin release",),
            scope=sealed.scope,
            impact=sealed.impact,
            risk=sealed.risk,
            rollback=sealed.rollback,
            verification=sealed.verification,
            binding=sealed.binding,
            role_context=sealed.role_context,
            approval_id=sealed.approval_id,
            fingerprints=sealed.fingerprints,
        )


def test_pending_dedup_fingerprint_moves_because_it_is_derived_from_the_payload():
    """The one fingerprint authoring DOES move, reported rather than hidden.

    ``approvals.fingerprint`` is a digest of the whole sealed payload, computed
    once at insert before any grant exists, so a payload carrying three more
    statements hashes differently. Nothing is re-derived and no row is mutated:
    the value is written at mint and never recomputed against a later payload.
    """
    covered = _build_sealed_payload(
        command=SINGLE_COMMAND, verb="push", category="MUTATIVE"
    )
    without_statements = {
        key: value
        for key, value in covered.items()
        if key not in ("impact", "verification")
    }
    without_statements["rollback_hint"] = None
    assert fingerprint_payload(covered) != fingerprint_payload(without_statements)
