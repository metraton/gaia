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
:func:`native_presentation` refuses to emit a surface that fails it.

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
)

#: The envelope fields whose value must appear verbatim in the visible surface,
#: paired with the label the surface shows them under. ``commands`` is absent
#: because it is a sequence checked for order as well as presence.
VISIBLE_SCALAR_FIELDS = (
    ("operation", "OPERATION"),
    ("scope", "SCOPE"),
    ("impact", "IMPACT"),
    ("risk", "RISK"),
    ("rollback", "ROLLBACK"),
    ("verification", "VERIFICATION"),
)

_LABEL_WIDTH = max(len(label) for _, label in VISIBLE_SCALAR_FIELDS) + 2

_ROLLBACK_ABSENT = "NOT REVERSIBLE"
_IMPACT_ABSENT = (
    "No impact statement was declared with this request; treat every command "
    "below as changing state outside this session."
)
_VERIFICATION_ABSENT = (
    "No verification step was declared with this request; confirm the resulting "
    "state yourself before granting again."
)


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


def envelope_from_sealed_payload(
    payload: Mapping[str, Any],
    *,
    approval_id: str,
    binding: ConsentBinding,
    role: str = "",
) -> ConsentRequestEnvelope:
    """Seal one pending approval into the versioned neutral request envelope.

    ``impact`` and ``verification`` have no key in the sealed approval payload
    any Gaia surface writes today, and the envelope requires both. They are
    filled from the payload when a producer starts supplying them and otherwise
    state that nothing was declared -- a user reading "none was declared" is
    informed, whereas a plausible sentence composed here would be this module
    asserting a consequence nobody assessed.
    """
    commands = payload_commands(payload)
    if not commands:
        raise ValueError(f"approval {approval_id} seals no exact command to present")
    return ConsentRequestEnvelope(
        correlation_id=mint_correlation_id(approval_id, binding),
        operation=str(payload.get("operation") or "").strip() or "Execute a T3 operation",
        commands=commands,
        scope=str(payload.get("scope") or "").strip() or "unscoped",
        impact=str(payload.get("impact") or "").strip() or _IMPACT_ABSENT,
        risk=_render_risk(payload),
        rollback=str(payload.get("rollback_hint") or payload.get("rollback") or "").strip()
        or _ROLLBACK_ABSENT,
        verification=str(payload.get("verification") or "").strip() or _VERIFICATION_ABSENT,
        binding=binding,
        role_context=RoleCapabilityContext(role=role or binding.agent_id or "unattributed"),
        approval_id=approval_id,
    )


def _render_risk(payload: Mapping[str, Any]) -> str:
    """Fold the rationale into the risk line so a bare level is never shown alone."""
    level = str(payload.get("risk_level") or payload.get("risk") or "").strip() or "unknown"
    rationale = str(payload.get("rationale") or "").strip()
    return f"{level} -- {rationale}" if rationale else level


def render_native_text(envelope: ConsentRequestEnvelope) -> str:
    """Render the user-visible consent surface for one sealed envelope."""
    values = {name: getattr(envelope, name) for name, _ in VISIBLE_SCALAR_FIELDS}
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
        for name, label in VISIBLE_SCALAR_FIELDS
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
    envelope: ConsentRequestEnvelope,
) -> tuple[str, ...]:
    """Name every sealed field the visible text omits, alters, or reorders.

    A command is missing when its exact bytes are absent, and out of order when
    it appears before the command sealed ahead of it: consent to an ordered set
    presented in another order is consent to something else.
    """
    missing = [
        name
        for name, _ in VISIBLE_SCALAR_FIELDS
        if getattr(envelope, name) not in visible_text
    ]
    cursor = -1
    for index, command in enumerate(envelope.commands, start=1):
        at = visible_text.find(command, cursor + 1)
        if at < 0:
            missing.append(f"commands[{index}]")
            continue
        if at <= cursor:
            missing.append(f"commands[{index}]:order")
        cursor = at
    missing.extend(
        f"fingerprints[{index}]"
        for index, fingerprint in enumerate(envelope.fingerprints, start=1)
        if fingerprint not in visible_text
    )
    return tuple(missing)


def native_presentation(envelope: ConsentRequestEnvelope) -> dict[str, Any]:
    """Produce the complete native payload: visible lines plus their metadata.

    Raises when the rendered surface would hide a sealed field, so an
    incomplete consent surface cannot reach a user through any host.
    """
    visible_text = render_native_text(envelope)
    incomplete = missing_visible_fields(visible_text, envelope)
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
