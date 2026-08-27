"""One durable record for a consent decision that produced no grant.

A decision the user made and a decision never made are two different facts, and
until this channel existed the store held the same thing for both: nothing.
Every non-activation exit on a consent path logged a line and returned, so a
dropped signature left nothing a later reader could find -- measured on
2026-08-19, when two protected-path approvals were presented in ONE host
question event, the user approved both, and only the first activated.

The substrate is ``harness_events``, not a new table: it is already the
append-only audit mirror surfaced by ``gaia query --surface harness_events`` and
by ``gaia defects``, and it carries no foreign key to ``approvals`` -- which this
record needs, because the exit that loses a signature most quietly is the one
where no nonce resolved at all and there is therefore no approval row an
``approval_events`` row could hang off.

One shape, one event type, one reason vocabulary, every lane. A path that
receives a structured decision and produces no executable grant appends here
under its own reason code instead of minting a parallel record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)

DECISION_NOT_ACTIVATED_EVENT = "consent.decision.not_activated"
DECISION_NOT_ACTIVATED_SOURCE = "consent"

# Lanes a decision can arrive on. The lane is recorded, not inferred, so a
# reader can tell which adapter received the decision that granted nothing.
LANE_CLAUDE_CODE_QUESTION = "claude_code.ask_user_question"
LANE_OPENCODE_PERMISSION = "opencode.permission_replied"

REASON_NO_SESSION_BINDING = "no_session_binding"
REASON_NO_NONCE_IN_LABELS = "no_nonce_in_labels"
REASON_ACTIVATION_FAILED = "activation_failed"
REASON_ALWAYS_REFUSED = "always_refused"

# A decision that grants nothing is not automatically a fault -- a plain
# rejection is the consent layer working as designed. Only the reasons where a
# signature was given and could not be honored are graded above info, because
# that grading is exactly what `gaia defects` reads.
_SEVERITY_BY_REASON = {
    REASON_NO_SESSION_BINDING: "warning",
    REASON_NO_NONCE_IN_LABELS: "info",
    REASON_ACTIVATION_FAILED: "warning",
    REASON_ALWAYS_REFUSED: "info",
}

_FALLBACK_SEVERITY = "warning"

# Caller context nests under this ONE payload key rather than merging flat, so a
# caller's key named "reason" or "lane" cannot overwrite the two fields the
# record exists to carry.
DETAILS_PAYLOAD_KEY = "details"

# How much of each decision value to keep. Enough to recognize WHICH label the
# user picked -- including a reject phrasing -- without copying a whole prompt
# into the audit row.
_VALUE_PREVIEW_CHARS = 200


@dataclass(frozen=True)
class DecisionNotActivated:
    """One non-activation record, ready to append.

    The five scalar fields map one-to-one onto ``harness_events`` columns and
    ``meta`` is what lands in ``payload`` as JSON. Frozen because a record is a
    statement about something that happened: rewriting it after construction
    would be editing the audit trail rather than writing it.
    """

    event_type: str
    source: str
    agent: str
    result: str
    severity: str
    meta: dict[str, Any]

    def as_write_kwargs(self) -> dict[str, Any]:
        """Return this record as keyword arguments for ``write_harness_event``.

        ``meta`` is copied, so a caller cannot reach back through the returned
        dict and mutate the frozen record's payload. ``workspace`` and
        ``db_path`` are deliberately absent: they locate the substrate written
        to, not the event being recorded.
        """
        return {
            "event_type": self.event_type,
            "source": self.source,
            "agent": self.agent,
            "result": self.result,
            "severity": self.severity,
            "meta": dict(self.meta),
        }


def _preview(value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    return text[:_VALUE_PREVIEW_CHARS]


def build_decision_not_activated(
    *,
    reason: str,
    lane: str,
    session_id: str = "",
    approval_id: str | None = None,
    nonce_prefix: str | None = None,
    agent: str = "",
    decision_values: Sequence[object] = (),
    detail: str = "",
    details: Mapping[str, Any] | None = None,
) -> DecisionNotActivated:
    """Build the record for one structured decision that granted nothing.

    Args:
        reason:          Why no grant resulted -- one of the ``REASON_*``
                         constants. An unknown reason is recorded verbatim and
                         graded ``warning``, because a reason this vocabulary has
                         not been taught is the case least safe to grade down.
        lane:            Which adapter received the decision (``LANE_*``).
        session_id:      Session the decision arrived under; empty is itself a
                         finding, recorded as such rather than omitted.
        approval_id:     Correlation identity when the path resolved one.
        nonce_prefix:    Correlation identity when only the prefix is known --
                         the no-nonce exit has neither, which is the point.
        agent:           Agent bound to the decision, when known.
        decision_values: The answered values as received. Kept because they are
                         the only evidence of WHAT the user chose, and because
                         nothing else in the record distinguishes a rejection
                         from a malformed approve label.
        detail:          Free text from the failing writer (an activation
                         result's own reason, for instance).
        details:         Optional structured context, nested under
                         :data:`DETAILS_PAYLOAD_KEY`.
    """
    values = [_preview(v) for v in decision_values]
    meta: dict[str, Any] = {
        "reason": reason,
        "lane": lane,
        "session_id": session_id,
        "decision_count": len(values),
        "decision_values": values,
    }
    if approval_id:
        meta["approval_id"] = approval_id
    if nonce_prefix:
        meta["nonce_prefix"] = nonce_prefix
    if agent:
        meta["agent"] = agent
    if detail:
        meta["detail"] = detail
    if details:
        meta[DETAILS_PAYLOAD_KEY] = dict(details)

    # The reason leads the line because both readers truncate it, and a triage
    # listing cut off before the reason forces a second query to learn anything.
    result = f"{reason}: decision received on lane '{lane}' produced no grant"
    if detail:
        result = f"{result} -- {detail}"

    return DecisionNotActivated(
        event_type=DECISION_NOT_ACTIVATED_EVENT,
        source=DECISION_NOT_ACTIVATED_SOURCE,
        agent=agent,
        result=result,
        severity=_SEVERITY_BY_REASON.get(reason, _FALLBACK_SEVERITY),
        meta=meta,
    )


def record_decision_not_activated(
    *,
    reason: str,
    lane: str,
    session_id: str = "",
    approval_id: str | None = None,
    nonce_prefix: str | None = None,
    agent: str = "",
    decision_values: Sequence[object] = (),
    detail: str = "",
    details: Mapping[str, Any] | None = None,
) -> int | None:
    """Append the record and return its ``harness_events`` row id.

    Returns ``None`` when the append itself failed. This sits on a consent
    path, where an audit-write hiccup must never withhold a grant the user did
    sign, so the failure is logged and swallowed rather than raised.
    """
    event = build_decision_not_activated(
        reason=reason,
        lane=lane,
        session_id=session_id,
        approval_id=approval_id,
        nonce_prefix=nonce_prefix,
        agent=agent,
        decision_values=decision_values,
        detail=detail,
        details=details,
    )
    try:
        from gaia.project import resolve_workspace
        from gaia.store.writer import write_harness_event

        return write_harness_event(
            workspace=resolve_workspace(), **event.as_write_kwargs()
        )
    except Exception as exc:
        logger.warning(
            "Failed to record non-activation (%s) on lane %s (non-fatal): %s",
            reason, lane, exc,
        )
        return None


__all__ = [
    "DECISION_NOT_ACTIVATED_EVENT",
    "DECISION_NOT_ACTIVATED_SOURCE",
    "DETAILS_PAYLOAD_KEY",
    "LANE_CLAUDE_CODE_QUESTION",
    "LANE_OPENCODE_PERMISSION",
    "REASON_ACTIVATION_FAILED",
    "REASON_ALWAYS_REFUSED",
    "REASON_NO_NONCE_IN_LABELS",
    "REASON_NO_SESSION_BINDING",
    "DecisionNotActivated",
    "build_decision_not_activated",
    "record_decision_not_activated",
]
