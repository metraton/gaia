"""Harness-neutral consent decision normalization and deduplication.

A host may deliver one user reply through more than one native event. This
module owns the rules that collapse those deliveries into exactly one neutral
decision: the lane vocabulary, its precedence, the deterministic correlation
identity, and the single-effect ledger. No harness event name appears here --
an adapter translates its own event names into a lane before crossing this
boundary, so a host-specific spelling can never become policy vocabulary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from .types import (
    CONSENT_PROTOCOL_VERSION,
    ConsentBinding,
    ConsentDecision,
    ConsentDecisionReceived,
)

PREFERRED_DECISION_LANE = "preferred"
COMPATIBILITY_DECISION_LANE = "compatibility"

# Ordered strongest-first: a lane's index IS its rank, so precedence lives in
# one table instead of a comparison repeated at each call site.
DECISION_LANE_PRECEDENCE = (PREFERRED_DECISION_LANE, COMPATIBILITY_DECISION_LANE)


def lane_rank(lane: object) -> int:
    """Return the precedence rank of a lane, rejecting an unknown one."""
    if not isinstance(lane, str) or lane not in DECISION_LANE_PRECEDENCE:
        raise ValueError(f"unknown consent decision lane: {lane!r}")
    return DECISION_LANE_PRECEDENCE.index(lane)


def normalize_decision(reply: object) -> ConsentDecision:
    """Map a host reply onto the neutral decision vocabulary.

    An unrecognized reply is an error, never an approval: a spelling this layer
    has not been taught must not grant capability by defaulting to consent.
    """
    if isinstance(reply, ConsentDecision):
        return reply
    if isinstance(reply, str):
        candidate = reply.strip().lower()
        for decision in ConsentDecision:
            if candidate == decision.value:
                return decision
    raise ValueError(f"unrecognized consent reply: {reply!r}")


def _canonical_identity(kind: str, approval_id: str, binding: ConsentBinding) -> str:
    if not isinstance(approval_id, str) or not approval_id.strip():
        raise ValueError("approval_id is required to derive a consent identity")
    return json.dumps(
        {
            "kind": kind,
            "protocol_version": CONSENT_PROTOCOL_VERSION,
            "approval_id": approval_id.strip(),
            "agent_id": binding.agent_id,
            "session_id": binding.session_id,
            "call_id": binding.call_id,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def mint_correlation_id(approval_id: str, binding: ConsentBinding) -> str:
    """Derive the correlation identity of one consent attempt.

    The identity is a pure function of the approval and its binding, so two
    deliveries of the same reply -- in different lanes, or in different
    processes sharing no memory -- collapse onto one correlation.
    """
    canonical = _canonical_identity("correlation", approval_id, binding)
    return f"C-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"


def mint_request_fingerprint(approval_id: str, binding: ConsentBinding) -> str:
    """Bind a decision to the exact approval and binding it answers."""
    canonical = _canonical_identity("request", approval_id, binding)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_decision(
    approval_id: str, binding: ConsentBinding, reply: object
) -> ConsentDecisionReceived:
    """Assemble the neutral decision for one host reply."""
    return ConsentDecisionReceived(
        correlation_id=mint_correlation_id(approval_id, binding),
        decision=normalize_decision(reply),
        binding=binding,
        request_fingerprint=mint_request_fingerprint(approval_id, binding),
    )


def binding_from_mapping(value: object) -> ConsentBinding:
    """Read a binding from a host payload, rejecting anything but an object."""
    if not isinstance(value, Mapping):
        raise ValueError("consent binding must be an object")
    return ConsentBinding(
        agent_id=str(value.get("agent_id") or ""),
        session_id=str(value.get("session_id") or ""),
        call_id=str(value.get("call_id") or ""),
    )


@dataclass(frozen=True)
class ConsentDecisionAdmission:
    """One delivery's outcome against the single-effect rule.

    ``decision`` is always the decision that took effect for the correlation,
    so a superseding delivery reports the effect that already happened rather
    than implying a second one. ``accepted`` is true exactly once per
    correlation; ``lane`` is the highest-precedence lane observed so far.
    """

    decision: ConsentDecisionReceived
    lane: str
    accepted: bool
    duplicate: bool
    superseded_lane: str | None = None
    conflicting_lane: str | None = None


class ConsentDecisionLedger:
    """Collapse repeated deliveries of one consent attempt into one effect."""

    def __init__(self) -> None:
        self._records: dict[str, ConsentDecisionAdmission] = {}

    def admit(self, lane: str, decision: ConsentDecisionReceived) -> ConsentDecisionAdmission:
        """Record a delivery and report whether it is the one that acts."""
        rank = lane_rank(lane)
        correlation_id = decision.correlation_id
        prior = self._records.get(correlation_id)
        if prior is None:
            admission = ConsentDecisionAdmission(
                decision=decision, lane=lane, accepted=True, duplicate=False
            )
            self._records[correlation_id] = admission
            return admission
        if rank < lane_rank(prior.lane):
            admission = ConsentDecisionAdmission(
                decision=prior.decision,
                lane=lane,
                accepted=False,
                duplicate=True,
                superseded_lane=prior.lane,
                conflicting_lane=(
                    lane if decision.decision is not prior.decision.decision else None
                ),
            )
            self._records[correlation_id] = admission
            return admission
        return ConsentDecisionAdmission(
            decision=prior.decision, lane=prior.lane, accepted=False, duplicate=True
        )

    def effective(self, correlation_id: str) -> ConsentDecisionAdmission | None:
        """Return the decision that took effect for a correlation, if any."""
        return self._records.get(correlation_id)
