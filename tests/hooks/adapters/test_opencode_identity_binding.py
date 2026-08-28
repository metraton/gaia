"""Identity binding at the OpenCode T3 boundary.

Every payload asserted here is produced by the real adapter -- ``parse_event``
followed by ``build_policy_payload`` -- because the defect these tests exist to
prevent was proven only against a hand-built dict no adapter ever emitted.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = Path(__file__).resolve().parents[3] / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

from adapters.opencode import OpenCodeAdapter
from modules.orchestrator.delegate_mode import (
    ORCHESTRATOR_AGENT_TYPES,
    SessionRole,
    classify_session_role,
)
from modules.security.gaia_cli_only_guard import check as gaia_cli_check
from modules.security.gaia_cli_only_guard import is_orchestrator_role
from modules.security.host_attestation import host_run_id, issue


@pytest.fixture(autouse=True)
def _ledger(tmp_path, monkeypatch):
    """Keep issuance and resolution inside a ledger this module owns."""
    monkeypatch.setenv("GAIA_OPENCODE_ATTESTATION_DIR", str(tmp_path / "ledger"))


@pytest.fixture
def attested_orchestrator():
    """The control-plane claim as the host issues it: a token, not a string.

    Issued in this process's own host run, which is also the run the adapter
    resolves in: the namespace is not a value either side gets to choose, so a
    test cannot align them by naming one.
    """
    issued = issue(
        host_run=host_run_id(),
        session_id="ses-1",
        role="gaia-orchestrator",
        issuer="opencode-runtime",
    )
    return dict(ATTESTED_ORCHESTRATOR, attestation=issued.token)


# Well formed and caller-minted: the interpolation the plugin used to perform.
# It resolves against no ledger, so every use below is a forgery case.
ATTESTED_ORCHESTRATOR = {
    "role": "gaia-orchestrator",
    "capabilities": ["plan.manage", "approvals.present"],
    "issuer": "opencode-runtime",
    "attestation": "ses-1:gaia-orchestrator",
    "verified": True,
}

# Read from the classifier itself: a spelling added there must be covered here
# without this file being edited, because the hole these tests close was exactly
# a set with two members fenced against a literal with one.
CONTROL_PLANE_SPELLINGS = sorted(ORCHESTRATOR_AGENT_TYPES)


def _unattested_context(role):
    return {
        "role": role,
        "capabilities": [],
        "issuer": "opencode-runtime",
        "attestation": "",
        "verified": False,
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


def test_attested_context_reaches_the_runtime_classifier_as_a_mapping(
    attested_orchestrator,
):
    payload = _policy_payload(roleContext=attested_orchestrator)

    assert isinstance(payload["role_context"], dict)
    assert payload["role_context"]["attestation"] == attested_orchestrator["attestation"]
    assert tuple(payload["role_context"]["capabilities"]) == (
        "plan.manage",
        "approvals.present",
    )
    assert classify_session_role(payload) is SessionRole.ORCHESTRATOR


def test_control_plane_role_depends_on_attestation_and_not_on_a_name(
    attested_orchestrator,
):
    payload = _policy_payload(roleContext=attested_orchestrator)
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


def test_the_orchestrator_only_guard_engages_only_for_the_attested_lane(
    attested_orchestrator,
):
    orchestrator = _policy_payload(roleContext=attested_orchestrator)
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


def test_the_attested_control_plane_is_not_denied_for_its_identity(
    attested_orchestrator,
):
    response = OpenCodeAdapter().adapt_pre_tool_use(
        _event(
            tool="task",
            args={"subagent_type": "developer", "prompt": "go"},
            roleContext=attested_orchestrator,
        )
    )

    assert "control-plane dispatches" not in str(response.output)
    assert "untrusted issuer" not in str(response.output)


@pytest.mark.parametrize("spelling", CONTROL_PLANE_SPELLINGS)
def test_prompt_declared_control_plane_spelling_is_denied(spelling):
    response = OpenCodeAdapter().adapt_pre_tool_use(_event(agent=spelling))

    assert response.output["action"] == "deny"
    assert response.exit_code == 2


@pytest.mark.parametrize("spelling", CONTROL_PLANE_SPELLINGS)
def test_unattested_context_carrying_a_control_plane_spelling_is_denied(spelling):
    response = OpenCodeAdapter().adapt_pre_tool_use(
        _event(roleContext=_unattested_context(spelling))
    )

    assert response.output["action"] == "deny"
    assert response.exit_code == 2


@pytest.mark.parametrize("spelling", CONTROL_PLANE_SPELLINGS)
@pytest.mark.parametrize(
    "claim",
    ["declared-only", "unattested-context"],
)
def test_no_unattested_payload_ever_classifies_as_the_control_plane(spelling, claim):
    """Hold the invariant at the payload, not at the pre-check that guards it.

    The denial above depends on ``_identity_rejection`` running first; this
    asserts the normalized payload itself, so a refactor that moves or skips
    that pre-check still cannot hand the orchestrator lane to a name.
    """
    overrides = (
        {"agent": spelling}
        if claim == "declared-only"
        else {"roleContext": _unattested_context(spelling)}
    )
    payload = _policy_payload(**overrides)

    assert payload["agent_type"].strip().lower() not in ORCHESTRATOR_AGENT_TYPES
    assert classify_session_role(payload) is not SessionRole.ORCHESTRATOR


_PLUGIN_SOURCE = (
    Path(__file__).resolve().parents[3] / "opencode" / "plugin.ts"
).read_text()

# The runtime state a control-plane turn holds when tool.execute.before fires:
# the primary session is in agentBySession (a NAME) and in no dispatch map, and
# roleContext() attests it. agentID is absent because JSON.stringify drops the
# undefined a dispatch-map miss returns.
PLUGIN_CONTROL_PLANE_EVENT = {
    "event": "tool.execute.before",
    "sessionID": "ses-1",
    "callID": "call-1",
    "agent": "gaia-orchestrator",
    "roleContext": ATTESTED_ORCHESTRATOR,
    "tool": "bash",
    "args": {"command": "gaia plan show brief"},
}


def _bridge_call_fields(event_name):
    """Map each field of one plugin.ts bridge call to its source expression."""
    start = _PLUGIN_SOURCE.index(f'event: "{event_name}"')
    depth = 1
    body = ""
    for offset in range(start, len(_PLUGIN_SOURCE)):
        if _PLUGIN_SOURCE[offset] == "{":
            depth += 1
        elif _PLUGIN_SOURCE[offset] == "}":
            depth -= 1
            if depth == 0:
                body = _PLUGIN_SOURCE[start:offset]
                break
    fields = {}
    for line in body.splitlines():
        match = re.match(r"\s*(\w+)(?::\s*(.+?))?,\s*$", line)
        if match:
            fields[match.group(1)] = match.group(2) or match.group(1)
    return fields


def test_the_event_plugin_ts_emits_reaches_the_attested_control_plane_lane(
    attested_orchestrator,
):
    """Clause 2 over the host's own shape, read from plugin.ts, not restated.

    A tripwire on the source text, kept beside the executed-plugin coverage in
    test_opencode_attestation_provenance.py: this one fails on a renamed or
    reformatted field, that one fails on a plugin whose behaviour changed.
    """
    fields = _bridge_call_fields("tool.execute.before")

    assert set(fields) == set(PLUGIN_CONTROL_PLANE_EVENT) | {
        "agentID", "cwd", "worktree", "originalTool", "originalArgs",
        "consentRetry",
    }
    assert fields["agent"] == "agent"
    # Any truthy agent_id classifies SUBAGENT before classify_session_role
    # consults the attested context, so a role name here makes this lane
    # unreachable end to end however well-formed the attestation is.
    assert fields["agentID"] != fields["agent"]
    assert fields["agentID"] == "dispatchHandle(call.sessionID)", (
        f"agentID is not the single dispatch predicate: {fields['agentID']}"
    )
    # Both bridge calls must ask the same predicate. Reading the dispatch map
    # directly is the spelling that left agent_id absent for a child's whole
    # run, because that map is written only once the child has finished.
    assert _PLUGIN_SOURCE.count("agentID: dispatchHandle(call.sessionID)") == 2
    assert "agentID: dispatchBySession" not in _PLUGIN_SOURCE

    attested = dict(PLUGIN_CONTROL_PLANE_EVENT, roleContext=attested_orchestrator)
    payload = OpenCodeAdapter().build_policy_payload(
        OpenCodeAdapter().parse_event(json.dumps(attested))
    )

    assert payload["agent_id"] == ""
    assert payload["role_context"]["attestation"] == attested_orchestrator["attestation"]
    assert classify_session_role(payload) is SessionRole.ORCHESTRATOR
    assert is_orchestrator_role(payload) is True
    allowed, reason = gaia_cli_check("rm -rf /tmp/gaia-identity-probe", payload)
    assert allowed is False and reason


def test_a_dispatched_child_session_still_classifies_as_a_subagent():
    """Correcting the conflation must not hand the lane to a real subagent."""
    payload = OpenCodeAdapter().build_policy_payload(
        OpenCodeAdapter().parse_event(
            json.dumps(dict(PLUGIN_CONTROL_PLANE_EVENT, agentID="call-1"))
        )
    )

    assert payload["agent_id"] == "call-1"
    assert classify_session_role(payload) is SessionRole.SUBAGENT
