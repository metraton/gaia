"""Harness-neutral rendering of a sealed consent request for native presentation.

A host's native permission mechanism is handed two things: text a user reads and
metadata a program reads. Both are produced HERE, from one sealed
:class:`ConsentRequestEnvelope`, so a host edge can only carry them -- it can
neither compose the text nor choose which fields the user gets to see. That is
what makes "visible text and metadata agree with the envelope" a property of the
producer instead of a hope about each adapter.

The visible surface is not a summary: every field the envelope seals appears in
it verbatim, commands in order and byte-exact with their fingerprints, because a
field present only in structured metadata is a field the consenting user never
saw. :func:`missing_visible_fields` is the executable form of that rule and
:func:`native_presentation` refuses to emit a surface that fails it. That check
reads the SEALED PAYLOAD, never the envelope the render came from: a check whose
reference is the same envelope can only prove this module agrees with itself,
and the divergence worth catching is a render carrying something other than
what a producer sealed.

No dependency on any Gaia module outside this package -- same rule as
``types.py``: a module-level import reaching ``modules.security`` from here
breaks every hook entry point.
"""

from __future__ import annotations

from typing import Any, Mapping

from .consent_events import mint_correlation_id
from .types import (
    ConsentBinding,
    ConsentRequestEnvelope,
    RoleCapabilityContext,
    _command_fingerprint,
)

_ROLLBACK_ABSENT = (
    "No rollback was declared with this request; reversibility is unknown -- do "
    "not assume the commands below can be undone."
)
_IMPACT_ABSENT = (
    "No impact statement was declared with this request; treat every command "
    "below as changing state outside this session."
)
_VERIFICATION_ABSENT = (
    "No verification step was declared with this request; confirm the resulting "
    "state yourself before granting again."
)
_OPERATION_ABSENT = "Execute a T3 operation"
_SCOPE_ABSENT = "unscoped"
_RISK_ABSENT = "unknown"

#: Every field the visible surface must carry, in render order: its envelope
#: name, the label it is shown under, the keys a sealed payload may spell it
#: with, and the text shown when the payload declares none of them. One table
#: serves the derivation, the render, and the tripwire, so what a producer
#: sealed and what the check looks for cannot drift apart. ``commands`` is
#: absent because it is a sequence checked for order as well as presence.
VISIBLE_FIELDS = (
    ("operation", "OPERATION", ("operation",), _OPERATION_ABSENT),
    ("scope", "SCOPE", ("scope",), _SCOPE_ABSENT),
    ("impact", "IMPACT", ("impact",), _IMPACT_ABSENT),
    ("risk", "RISK", ("risk_level", "risk"), _RISK_ABSENT),
    ("rollback", "ROLLBACK", ("rollback_hint", "rollback"), _ROLLBACK_ABSENT),
    ("verification", "VERIFICATION", ("verification",), _VERIFICATION_ABSENT),
)

_LABEL_WIDTH = max(len(label) for _, label, _, _ in VISIBLE_FIELDS) + 2


def payload_commands(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the exact commands a sealed payload covers, in their sealed order."""
    listed = payload.get("commands")
    if isinstance(listed, (list, tuple)):
        commands = tuple(item for item in listed if isinstance(item, str) and item)
        if commands:
            return commands
    single = payload.get("exact_content")
    if isinstance(single, str) and single:
        return (single,)
    return ()


def sealed_field(payload: Mapping[str, Any], name: str) -> str:
    """Read what the visible surface must carry for one field of a sealed payload.

    A field no producer declared resolves to the text stating that, so an absent
    field is shown as absent: a user reading "none was declared" is informed,
    whereas a plausible sentence composed here would be this module asserting a
    consequence nobody assessed.
    """
    for field, _label, keys, absent in VISIBLE_FIELDS:
        if field != name:
            continue
        value = absent
        for key in keys:
            declared = str(payload.get(key) or "").strip()
            if declared:
                value = declared
                break
        if field == "risk":
            # A bare level shown alone reads as a verdict with no grounds, so
            # the rationale that produced it travels in the same line.
            rationale = str(payload.get("rationale") or "").strip()
            return f"{value} -- {rationale}" if rationale else value
        return value
    raise KeyError(f"{name} is not a visible consent field")


def envelope_from_sealed_payload(
    payload: Mapping[str, Any],
    *,
    approval_id: str,
    binding: ConsentBinding,
    role: str = "",
) -> ConsentRequestEnvelope:
    """Seal one pending approval into the versioned neutral request envelope.

    ``verification`` and ``rollback`` are supplied by the plan-first producer
    (``gaia approvals request-set``); ``impact`` has no key any Gaia producer
    writes today. Each is filled from the payload when its producer declares it
    and otherwise states that nothing was declared -- see :func:`sealed_field`.
    """
    commands = payload_commands(payload)
    if not commands:
        raise ValueError(f"approval {approval_id} seals no exact command to present")
    return ConsentRequestEnvelope(
        correlation_id=mint_correlation_id(approval_id, binding),
        operation=sealed_field(payload, "operation"),
        commands=commands,
        scope=sealed_field(payload, "scope"),
        impact=sealed_field(payload, "impact"),
        risk=sealed_field(payload, "risk"),
        rollback=sealed_field(payload, "rollback"),
        verification=sealed_field(payload, "verification"),
        binding=binding,
        role_context=RoleCapabilityContext(role=role or binding.agent_id or "unattributed"),
        approval_id=approval_id,
    )


def render_native_text(envelope: ConsentRequestEnvelope) -> str:
    """Render the user-visible consent surface for one sealed envelope."""
    values = {name: getattr(envelope, name) for name, _, _, _ in VISIBLE_FIELDS}
    lines = [
        f"GAIA T3 APPROVAL REQUEST  {envelope.approval_id or envelope.correlation_id}",
        _labelled("OPERATION", values["operation"]),
        f"COMMANDS ({len(envelope.commands)}) -- exact bytes, in order:",
    ]
    for index, (command, fingerprint) in enumerate(
        zip(envelope.commands, envelope.fingerprints), start=1
    ):
        lines.append(f"  [{index}] {command}")
        lines.append(f"      sha256 {fingerprint}")
    lines.extend(
        _labelled(label, values[name])
        for name, label, _, _ in VISIBLE_FIELDS
        if name != "operation"
    )
    lines.append(
        _labelled(
            "CONSENT",
            f"protocol {envelope.protocol_version}  correlation {envelope.correlation_id}",
        )
    )
    return "\n".join(lines)


def _labelled(label: str, value: str) -> str:
    return f"{(label + ':').ljust(_LABEL_WIDTH)}{value}"


def native_metadata(envelope: ConsentRequestEnvelope) -> dict[str, Any]:
    """Structured mirror of the same envelope, for a program rather than a user."""
    return {
        "protocol_version": envelope.protocol_version,
        "correlation_id": envelope.correlation_id,
        "approval_id": envelope.approval_id,
        "operation": envelope.operation,
        "commands": list(envelope.commands),
        "fingerprints": list(envelope.fingerprints),
        "scope": envelope.scope,
        "impact": envelope.impact,
        "risk": envelope.risk,
        "rollback": envelope.rollback,
        "verification": envelope.verification,
        "binding": {
            "agent_id": envelope.binding.agent_id,
            "session_id": envelope.binding.session_id,
            "call_id": envelope.binding.call_id,
        },
        "canonical_payload": envelope.canonical_payload(),
    }


def missing_visible_fields(
    visible_text: str,
    sealed_payload: Mapping[str, Any],
) -> tuple[str, ...]:
    """Name every sealed field the visible text omits, alters, or reorders.

    The reference is the sealed payload -- what a producer stored and the
    approvals chain fingerprints -- so a surface that agrees with a derived
    envelope while carrying something else than what was sealed is still named.

    A command is missing when its exact bytes are absent, and out of order when
    it appears before the command sealed ahead of it: consent to an ordered set
    presented in another order is consent to something else.
    """
    missing = [
        name
        for name, _label, _keys, _absent in VISIBLE_FIELDS
        if sealed_field(sealed_payload, name) not in visible_text
    ]
    commands = payload_commands(sealed_payload)
    cursor = -1
    for index, command in enumerate(commands, start=1):
        at = visible_text.find(command, cursor + 1)
        if at < 0:
            missing.append(f"commands[{index}]")
            continue
        if at <= cursor:
            missing.append(f"commands[{index}]:order")
        cursor = at
    missing.extend(
        f"fingerprints[{index}]"
        for index, command in enumerate(commands, start=1)
        if _command_fingerprint(command) not in visible_text
    )
    return tuple(missing)


def native_presentation(
    envelope: ConsentRequestEnvelope,
    sealed_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Produce the complete native payload: visible lines plus their metadata.

    Raises when the rendered surface would hide a field the sealed payload
    declared, so an incomplete consent surface cannot reach a user through any
    host -- and so a render that lost a sealed value on its way through the
    envelope is refused rather than delivered.
    """
    visible_text = render_native_text(envelope)
    incomplete = missing_visible_fields(visible_text, sealed_payload)
    if incomplete:
        raise ValueError(
            "consent surface would hide sealed fields: " + ", ".join(incomplete)
        )
    return {
        "visible_text": visible_text,
        # Delivered as lines as well as one block: a host that renders each
        # entry on its own row must not be the reason a field goes unseen.
        "visible_lines": visible_text.split("\n"),
        "metadata": native_metadata(envelope),
    }
