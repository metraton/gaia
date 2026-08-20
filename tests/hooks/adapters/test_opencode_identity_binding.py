"""Identity binding at the OpenCode T3 boundary.

Every payload asserted here is produced by the real adapter -- ``parse_event``
followed by ``build_policy_payload`` -- because the defect these tests exist to
prevent was proven only against a hand-built dict no adapter ever emitted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from adapters.opencode import OpenCodeAdapter
from modules.orchestrator.delegate_mode import SessionRole, classify_session_role
from modules.security.gaia_cli_only_guard import check as gaia_cli_check
from modules.security.gaia_cli_only_guard import is_orchestrator_role


ATTESTED_ORCHESTRATOR = {
    "role": "gaia-orchestrator",
    "capabilities": ["plan.manage", "approvals.present"],
    "issuer": "opencode-runtime",
    "attestation": "ses-1:gaia-orchestrator",
    "verified": True,
}

ATTESTED_DEVELOPER = {
    "role": "developer",
    "capabilities": [],
    "issuer": "opencode-runtime",
    "attestation": "ses-1:developer",
    "verified": True,
}


def _event(**overrides):
    raw = {
        "event": "tool.execute.before",
        "sessionID": "ses-1",
        "callID": "call-1",
        "tool": "bash",
        "args": {"command": "gaia plan show brief"},
    }
    raw.update(overrides)
    return OpenCodeAdapter().parse_event(json.dumps(raw))


def _policy_payload(**overrides):
    adapter = OpenCodeAdapter()
    return adapter.build_policy_payload(_event(**overrides))


def test_attested_context_reaches_the_runtime_classifier_as_a_mapping():
    payload = _policy_payload(roleContext=ATTESTED_ORCHESTRATOR)

    assert isinstance(payload["role_context"], dict)
    assert payload["role_context"]["attestation"] == "ses-1:gaia-orchestrator"
    assert tuple(payload["role_context"]["capabilities"]) == (
        "plan.manage",
        "approvals.present",
    )
    assert classify_session_role(payload) is SessionRole.ORCHESTRATOR


def test_control_plane_role_depends_on_attestation_and_not_on_a_name():
    payload = _policy_payload(roleContext=ATTESTED_ORCHESTRATOR)
    tampered = dict(payload)
    tampered["role_context"] = dict(payload["role_context"], verified=False)
    tampered["agent_type"] = "developer"

    assert classify_session_role(tampered) is SessionRole.NAMED_SPECIALIST

    specialist = _policy_payload(roleContext=ATTESTED_DEVELOPER)
    assert classify_session_role(specialist) is SessionRole.NAMED_SPECIALIST


def test_a_call_carrying_no_claim_is_never_the_control_plane():
    payload = _policy_payload()

    assert payload["role_context"] is None
    assert payload["agent_type"] == "opencode-unattested"
    assert classify_session_role(payload) is not SessionRole.ORCHESTRATOR


def test_the_orchestrator_only_guard_engages_only_for_the_attested_lane():
    orchestrator = _policy_payload(roleContext=ATTESTED_ORCHESTRATOR)
    specialist = _policy_payload(roleContext=ATTESTED_DEVELOPER)
    probe = "rm -rf /tmp/gaia-identity-probe"

    assert is_orchestrator_role(orchestrator) is True
    allowed, reason = gaia_cli_check(probe, orchestrator)
    assert allowed is False and reason

    assert is_orchestrator_role(specialist) is False
    assert gaia_cli_check(probe, specialist) == (True, None)


@pytest.mark.parametrize(
    "overrides,expected",
    [
        (
            {"roleContext": dict(ATTESTED_ORCHESTRATOR, issuer="opencode-plugin")},
            "untrusted issuer",
        ),
        (
            {"roleContext": dict(ATTESTED_ORCHESTRATOR, attestation="")},
            "not attested",
        ),
        (
            {
                "roleContext": {
                    "role": "gaia-orchestrator",
                    "issuer": "opencode-runtime",
                    "attestation": "ses-1:gaia-orchestrator",
                }
            },
            "not attested",
        ),
        (
            {"agent": "developer", "roleContext": ATTESTED_ORCHESTRATOR},
            "does not match",
        ),
        (
            {"agent": "gaia-orchestrator"},
            "declared without an attested runtime context",
        ),
    ],
    ids=[
        "wrong-issuer",
        "absent-attestation",
        "verified-unset",
        "role-disagrees-with-declared-agent",
        "prompt-declared-role-with-no-context",
    ],
)
def test_forged_control_plane_identity_is_rejected(overrides, expected):
    response = OpenCodeAdapter().adapt_pre_tool_use(_event(**overrides))

    assert response.output["action"] == "deny"
    assert expected in response.output["reason"]
    assert response.exit_code == 2


@pytest.mark.parametrize(
    "overrides",
    [{}, {"roleContext": ATTESTED_DEVELOPER}],
    ids=["no-claim-at-all", "ordinary-attested-agent"],
)
def test_ordinary_opencode_agents_cannot_enter_the_control_plane_lane(overrides):
    response = OpenCodeAdapter().adapt_pre_tool_use(
        _event(tool="task", args={"subagent_type": "developer", "prompt": "go"}, **overrides)
    )

    assert response.output["action"] == "deny"
    assert "control-plane dispatches" in response.output["reason"]
    assert response.exit_code == 2


def test_the_attested_control_plane_is_not_denied_for_its_identity():
    response = OpenCodeAdapter().adapt_pre_tool_use(
        _event(
            tool="task",
            args={"subagent_type": "developer", "prompt": "go"},
            roleContext=ATTESTED_ORCHESTRATOR,
        )
    )

    assert "control-plane dispatches" not in str(response.output)
    assert "untrusted issuer" not in str(response.output)
