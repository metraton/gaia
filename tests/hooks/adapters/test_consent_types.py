"""Tests for the harness-neutral consent contract."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


HOOKS_DIR = Path(__file__).parents[3] / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

from adapters.types import (  # noqa: E402
    ConsentBinding,
    ConsentDecision,
    ConsentDecisionReceived,
    ConsentRequestEnvelope,
    RoleCapabilityContext,
)


def _request() -> ConsentRequestEnvelope:
    return ConsentRequestEnvelope(
        correlation_id="corr-1",
        approval_id="P-consent-1",
        operation="apply migration",
        commands=("git reset --hard HEAD~1",),
        scope="repository /workspace",
        impact="rewrites local history",
        risk="high",
        rollback="restore the prior ref",
        verification="git status --short",
        binding=ConsentBinding("agent-1", "session-1", "call-1"),
        role_context=RoleCapabilityContext(
            role="gaia-orchestrator",
            capabilities=("brief.read", "plan.manage"),
            issuer="gaia-runtime",
            attestation="signed-binding",
            verified=True,
        ),
    )


def test_request_seals_exact_commands_and_fingerprints() -> None:
    request = _request()

    assert len(request.fingerprints) == 1
    assert request.fingerprints[0]
    assert '"commands":["git reset --hard HEAD~1"]' in request.canonical_payload()


def test_request_rejects_changed_fingerprint() -> None:
    with pytest.raises(ValueError, match="fingerprints"):
        ConsentRequestEnvelope(
            **{
                **_request().__dict__,
                "fingerprints": ("0" * 64,),
            }
        )


def test_control_plane_requires_runtime_attestation_not_role_text_alone() -> None:
    context = RoleCapabilityContext(role="gaia-orchestrator")
    assert not context.is_verified_control_plane

    verified = _request().role_context
    assert verified.is_verified_control_plane


def test_decision_is_normalized_and_correlated_to_the_same_binding() -> None:
    request = _request()
    decision = ConsentDecisionReceived(
        correlation_id=request.correlation_id,
        decision=ConsentDecision.ONCE,
        binding=request.binding,
        request_fingerprint=request.fingerprints[0],
    )

    assert decision.decision.value == "once"
    assert decision.binding == request.binding
