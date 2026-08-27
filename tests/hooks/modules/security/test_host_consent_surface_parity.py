#!/usr/bin/env python3
"""One sealed payload renders one consent surface, whichever host presents it.

Two properties, and the second is the reason the first is not enough.

PARITY (gate 907): a payload built by the real producer is fed through BOTH
host paths in the same test -- the host-bound presentation and the surface
``approval_grants.render_consent_surface`` reconstructs -- and every field the
consent contract defines is asserted to carry the same value on both. The
reference is the payload the PRODUCER sealed, read key by key in this file:
comparing one surface against an envelope the other derived would let two
surfaces agree with each other while both disagreed with the payload, which is
the failure this file exists to exclude. The fingerprints are recomputed here
with hashlib for the same reason.

NO MANUFACTURED CLAIM (gate 908): a payload declaring no impact, no rollback
and no verification renders the shared absence statement on both surfaces, and
never the literal ``NOT REVERSIBLE`` -- an irreversibility claim the payload
cannot source, which the reconstructed surface used to substitute on operations
that are reversible. A synthetic payload is the right instrument there, because
an arbitrary caller really can seal one with those fields missing. The positive
control runs beside it: a renderer stuck on the sentinel would pass every
absence assertion while destroying the content the seal-time producer authored.

The classifier verdict is never written here. Each command's text goes through
``detect_mutative_command`` and the classifier's own verb and category are fed
to the producer, exactly as every production call site does -- a verb chosen by
a test can be paired with command text no interception would classify that way,
which asserts over a shape production never emits.
"""

import hashlib
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).parent.parent.parent.parent.parent / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from modules.security.approval_grants import render_consent_surface  # noqa: E402
from modules.security.mutative_verbs import (  # noqa: E402
    CATEGORY_MUTATIVE,
    detect_mutative_command,
)
from modules.tools.bash_validator import _build_sealed_payload  # noqa: E402
from adapters.consent_presentation import (  # noqa: E402
    VISIBLE_FIELDS,
    envelope_from_sealed_payload,
    render_native_text,
)
from adapters.types import ConsentBinding  # noqa: E402
from adapters.registry import (  # noqa: E402
    registered_host_mechanism_names,
    registered_host_surface_names,
)

APPROVAL_ID = "P-" + "a1b2c3d4" + "e" * 24
HOST_BINDING = ConsentBinding(
    agent_id="gitops-operator", session_id="S-parity", call_id="call-parity"
)

#: (command text, the verb the classifier derives from it). The verb column is
#: the assertion that the pairing is derived rather than declared.
COVERED_COMMANDS = [
    ("git push origin main", "push"),
    ("kubectl apply -f deployment.yaml", "apply"),
    ("kubectl delete pod probe-pod", "delete"),
]

#: Labels of the host-specific surface this task removed. An agnostic surface
#: that carries one has re-acquired the defect.
RETIRED_LABELS = ("OPERACION", "COMANDOS", "COMANDO:", "RIESGO")

AUTHORED_FIELDS = ("impact", "rollback", "verification")


def _absence_text(field: str) -> str:
    """The one shared statement shown when no producer declared a field."""
    for name, _label, _keys, absent in VISIBLE_FIELDS:
        if name == field:
            return absent
    raise AssertionError(f"{field} is not a visible consent field")


def _host_bound_surface(payload: dict) -> str:
    """The surface a host presents, bound to the call that asked for consent."""
    return render_native_text(
        envelope_from_sealed_payload(
            payload, approval_id=APPROVAL_ID, binding=HOST_BINDING
        )
    )


def _line_for(surface: str, label: str) -> str:
    for line in surface.splitlines():
        if line.startswith(f"{label}:"):
            return line
    raise AssertionError(f"{label} has no line in the surface:\n{surface}")


def _without_consent_line(surface: str) -> list[str]:
    return [line for line in surface.splitlines() if not line.startswith("CONSENT:")]


def _sealed_values(payload: dict) -> dict[str, str]:
    """What the PRODUCER sealed, read from the payload and composed here.

    Every value is taken straight off the payload keys, never off an envelope
    either renderer built, so a surface is compared with the seal rather than
    with the other surface's derivation of it.
    """
    return {
        "OPERATION": payload["operation"],
        "SCOPE": payload["scope"],
        "IMPACT": payload["impact"],
        "RISK": f"{payload['risk_level']} -- {payload['rationale']}",
        "ROLLBACK": payload["rollback_hint"],
        "VERIFICATION": payload["verification"],
    }


@pytest.mark.parametrize("command,expected_verb", COVERED_COMMANDS)
def test_both_hosts_render_the_values_the_producer_sealed(command, expected_verb):
    verdict = detect_mutative_command(command)
    assert verdict.is_mutative, f"{command!r} is not intercepted at all"
    assert verdict.verb == expected_verb
    assert verdict.category == CATEGORY_MUTATIVE

    payload = _build_sealed_payload(
        command=command,
        verb=verdict.verb,
        category=verdict.category,
        agent_type="gitops-operator",
    )
    host_surface = _host_bound_surface(payload)
    reconstructed = render_consent_surface(payload, APPROVAL_ID)

    for label, sealed in _sealed_values(payload).items():
        assert sealed and sealed.strip(), (
            f"{label} is empty in the sealed payload, so asserting it proves nothing"
        )
        assert _line_for(host_surface, label).endswith(sealed), host_surface
        assert _line_for(reconstructed, label).endswith(sealed), reconstructed

    for index, sealed_command in enumerate(payload["commands"], start=1):
        entry = f"  [{index}] {sealed_command}"
        digest = hashlib.sha256(sealed_command.encode("utf-8")).hexdigest()
        for surface in (host_surface, reconstructed):
            assert entry in surface, surface
            assert f"      sha256 {digest}" in surface, surface
            assert surface.index(entry) < surface.index(f"sha256 {digest}"), surface

    for surface in (host_surface, reconstructed):
        assert APPROVAL_ID in surface, surface
        for label in RETIRED_LABELS:
            assert label not in surface, f"{label} survives in:\n{surface}"
        lowered = surface.lower()
        forbidden_names = (
            registered_host_mechanism_names() + registered_host_surface_names()
        )
        for name in forbidden_names:
            assert name not in lowered, f"{name} is named in:\n{surface}"


def test_the_two_surfaces_differ_only_where_the_consent_attempt_differs():
    """Every contract field is identical; the correlation line is not, correctly.

    ``mint_correlation_id`` is a pure function of the approval id and the
    binding, and a correlation identifies ONE consent attempt rather than the
    payload -- so two attempts on one payload must not collide. The
    reconstructed surface has no host call to bind to, and borrowing or
    inventing a session id would assert a consent attempt that never happened.
    """
    command, _verb = COVERED_COMMANDS[0]
    verdict = detect_mutative_command(command)
    payload = _build_sealed_payload(
        command=command,
        verb=verdict.verb,
        category=verdict.category,
        agent_type="gitops-operator",
    )

    host_surface = _host_bound_surface(payload)
    reconstructed = render_consent_surface(payload, APPROVAL_ID)

    assert _without_consent_line(host_surface) == _without_consent_line(reconstructed)
    for surface in (host_surface, reconstructed):
        assert _line_for(surface, "CONSENT").startswith("CONSENT:"), surface
        assert "protocol 1  correlation C-" in _line_for(surface, "CONSENT"), surface


def _undeclared_payload() -> dict:
    """A payload an arbitrary caller can seal: three fields simply absent.

    Written as a literal on purpose -- the property under test is what the
    renderers do with fields nothing declared, and the intercept-time producer
    covers exactly five verbs, so a produced payload cannot exhibit the absence
    for those five at all.
    """
    return {
        "operation": "MUTATIVE command intercepted: chmod",
        "exact_content": "chmod 600 /tmp/parity-probe",
        "commands": ["chmod 600 /tmp/parity-probe"],
        "scope": "chmod",
        "risk_level": "medium",
        "rationale": "A verb no statement table covers",
        "impact": None,
        "rollback_hint": None,
        "verification": None,
    }


def test_an_undeclared_field_states_its_absence_on_both_surfaces():
    payload = _undeclared_payload()
    host_surface = _host_bound_surface(payload)
    reconstructed = render_consent_surface(payload, APPROVAL_ID)

    for surface in (host_surface, reconstructed):
        assert "NOT REVERSIBLE" not in surface, (
            f"An irreversibility claim the payload cannot source:\n{surface}"
        )

    for field, label in (
        ("impact", "IMPACT"),
        ("rollback", "ROLLBACK"),
        ("verification", "VERIFICATION"),
    ):
        absent = _absence_text(field)
        host_line = _line_for(host_surface, label)
        reconstructed_line = _line_for(reconstructed, label)
        assert host_line.endswith(absent), host_line
        assert reconstructed_line.endswith(absent), reconstructed_line
        # Shared, not merely similar: a user must not be able to infer a
        # different degree of certainty from which host they are reading.
        assert host_line == reconstructed_line


def test_a_declared_field_renders_its_value_and_no_sentinel():
    """The positive control, in the same run as the absence assertions."""
    command, _verb = COVERED_COMMANDS[0]
    verdict = detect_mutative_command(command)
    payload = _build_sealed_payload(
        command=command,
        verb=verdict.verb,
        category=verdict.category,
        agent_type="gitops-operator",
    )
    for field in AUTHORED_FIELDS:
        key = "rollback_hint" if field == "rollback" else field
        assert payload[key], f"{field} is unsealed, so the control proves nothing"

    host_surface = _host_bound_surface(payload)
    reconstructed = render_consent_surface(payload, APPROVAL_ID)

    for surface in (host_surface, reconstructed):
        assert payload["impact"] in surface, surface
        assert payload["rollback_hint"] in surface, surface
        assert payload["verification"] in surface, surface
        for field in AUTHORED_FIELDS:
            assert _absence_text(field) not in surface, (
                f"{field} shows the absence statement over declared content:\n{surface}"
            )
